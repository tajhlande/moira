import { defineStore } from "pinia";
import { computed, ref } from "vue";
import {
  api,
  type ConversationDetail,
  type ConversationInfo,
  type ExecutionStep,
  type ExecutionStepDetailResponse,
  type MessageInfo,
  type RunSettings,
  type WorkflowRunInfo,
} from "../api/client";

const STAGE_LABELS: Record<string, string> = {
  planning: "Planning",
  tool_discovery: "Discovering Tools",
  tool_selection: "Selecting Tools",
  research_execution: "Researching",
  compression: "Summarizing",
  draft_synthesis: "Drafting",
  verification: "Verifying",
  report_generation: "Generating Report",
};

type RunSnapshotInput = Partial<WorkflowRunInfo> &
  Record<string, unknown> & {
    execution_steps?: Array<Record<string, unknown>>;
  };

function detailKey(runId: string, stepId: string, stepVersion: number): string {
  return `${runId}:${stepId}:${stepVersion}`;
}

function mergeHistoricalCarryover(
  previous: ExecutionStep[],
  incoming: ExecutionStep[],
): ExecutionStep[] {
  const incomingIds = new Set(
    incoming
      .map((step) => step.id)
      .filter((stepId): stepId is string => typeof stepId === "string"),
  );

  const carryover = previous.filter(
    (step) =>
      step.status !== "running" &&
      (!step.id || !incomingIds.has(step.id)),
  );

  if (carryover.length === 0) {
    return incoming;
  }

  return [...carryover, ...incoming];
}

function mergeRunHistory(
  previous: WorkflowRunInfo,
  latest: WorkflowRunInfo,
): WorkflowRunInfo {
  return {
    ...latest,
    started_at: previous.started_at || latest.started_at,
    attempts: latest.attempts ?? previous.attempts,
    execution_steps: mergeHistoricalCarryover(
      previous.execution_steps,
      latest.execution_steps,
    ),
    tool_executions: [...previous.tool_executions, ...latest.tool_executions],
    verification_attempts: [
      ...previous.verification_attempts,
      ...latest.verification_attempts,
    ],
  };
}

export const useChatStore = defineStore("chat", () => {
  const conversations = ref<ConversationInfo[]>([]);
  const currentConversationId = ref<string | null>(null);
  const isNewChat = ref(true);
  const messages = ref<MessageInfo[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);

  // Canonical per-message run snapshots for the selected conversation.
  const runs = ref<Map<number, WorkflowRunInfo>>(new Map());

  const stepDetails = ref<Map<string, ExecutionStepDetailResponse>>(new Map());
  const loadingStepDetails = ref<Set<string>>(new Set());
  const stepDetailInflight = new Map<string, Promise<ExecutionStepDetailResponse>>();

  const runSettings = ref<RunSettings>({ budget: 50 });
  const runningConversations = ref<Set<string>>(new Set());

  // Message id of the run currently being streamed for this conversation.
  const activeUserMessageId = ref<number | null>(null);

  let streamAbort: AbortController | null = null;
  let globalEventsAbort: AbortController | null = null;
  let selectConversationRequestId = 0;

  const activeRun = computed<WorkflowRunInfo | null>(() => {
    const messageId = activeUserMessageId.value;
    if (!messageId) return null;
    return runs.value.get(messageId) || null;
  });

  const executionSteps = computed<ExecutionStep[]>(() => {
    const run = activeRun.value;
    if (!run) return [];
    return run.execution_steps.filter((s) => s.status !== "running");
  });

  const currentStep = computed<ExecutionStep | null>(() => {
    const run = activeRun.value;
    if (!run) return null;
    return run.execution_steps.find((s) => s.status === "running") || null;
  });

  const toolExecutions = computed(() => activeRun.value?.tool_executions ?? []);

  const verificationAttemptData = computed(
    () => activeRun.value?.verification_attempts ?? [],
  );

  const currentReport = computed(() => activeRun.value?.report ?? null);

  const budgetConsumed = computed(() => activeRun.value?.budget_consumed ?? 0);

  const budgetRemaining = computed<number | null>(() => {
    const run = activeRun.value;
    if (!run) return null;
    const runningStep = run.execution_steps.find((s) => s.status === "running");
    if (runningStep && typeof runningStep.budget_remaining === "number") {
      return runningStep.budget_remaining;
    }
    return run.budget_limit - run.budget_consumed;
  });

  const verificationAttempts = computed(
    () => verificationAttemptData.value.length,
  );

  const totalElapsedMs = computed(() => activeRun.value?.total_elapsed_ms ?? null);

  const runStopped = computed(() => activeRun.value?.status === "stopped");

  const runErrored = computed(
    () => activeRun.value?.status === "error" && !activeRun.value?.report,
  );

  function cancelStream() {
    if (streamAbort) {
      streamAbort.abort();
      streamAbort = null;
    }
  }

  function disconnectGlobalEvents() {
    if (globalEventsAbort) {
      globalEventsAbort.abort();
      globalEventsAbort = null;
    }
  }

  function normalizeStep(step: Record<string, unknown>): ExecutionStep {
    const node = String(step.node ?? "");
    const label = String(step.label ?? STAGE_LABELS[node] ?? node);
    const statusRaw = String(step.status ?? "completed");
    const status: ExecutionStep["status"] =
      statusRaw === "running" ||
      statusRaw === "completed" ||
      statusRaw === "error" ||
      statusRaw === "stopped"
        ? statusRaw
        : "completed";

    const detail =
      step.detail && typeof step.detail === "object"
        ? (step.detail as Record<string, unknown>)
        : undefined;

    return {
      id: step.id ? String(step.id) : undefined,
      detail_run_id:
        typeof step.detail_run_id === "string" ? step.detail_run_id : undefined,
      node,
      label,
      status,
      cost: typeof step.cost === "number" ? step.cost : 0,
      budget_remaining:
        typeof step.budget_remaining === "number" ? step.budget_remaining : 0,
      elapsed_ms: typeof step.elapsed_ms === "number" ? step.elapsed_ms : undefined,
      started_at:
        typeof step.started_at === "string" ? step.started_at : undefined,
      error: typeof step.error === "string" ? step.error : undefined,
      tool_call_count:
        typeof step.tool_call_count === "number" ? step.tool_call_count : undefined,
      step_version:
        typeof step.step_version === "number" ? step.step_version : undefined,
      has_detail:
        typeof step.has_detail === "boolean" ? step.has_detail : undefined,
      detail,
    };
  }

  function normalizeRunSnapshot(
    snapshot: RunSnapshotInput,
    fallbackUserMessageId?: number,
  ): WorkflowRunInfo | null {
    const userMessageId =
      typeof snapshot.user_message_id === "number"
        ? snapshot.user_message_id
        : fallbackUserMessageId;
    if (!userMessageId) return null;

    const existing = runs.value.get(userMessageId);
    const rawStatus = String(snapshot.status ?? existing?.status ?? "running");
    const status: WorkflowRunInfo["status"] =
      rawStatus === "running" ||
      rawStatus === "completed" ||
      rawStatus === "error" ||
      rawStatus === "stopped"
        ? rawStatus
        : "running";

    const executionStepsRaw = Array.isArray(snapshot.execution_steps)
      ? snapshot.execution_steps
      : existing?.execution_steps ?? [];
    const executionSteps = executionStepsRaw.map((step) =>
      normalizeStep(step as Record<string, unknown>),
    );

    return {
      id: typeof snapshot.id === "string" ? snapshot.id : existing?.id ?? "",
      conversation_id:
        typeof snapshot.conversation_id === "string"
          ? snapshot.conversation_id
          : existing?.conversation_id,
      user_message_id: userMessageId,
      attempts: Array.isArray(snapshot.attempts)
        ? (snapshot.attempts as WorkflowRunInfo["attempts"])
        : existing?.attempts,
      execution_steps: executionSteps,
      tool_executions: Array.isArray(snapshot.tool_executions)
        ? snapshot.tool_executions
        : existing?.tool_executions ?? [],
      verification_attempts: Array.isArray(snapshot.verification_attempts)
        ? snapshot.verification_attempts
        : existing?.verification_attempts ?? [],
      report:
        snapshot.report !== undefined
          ? (snapshot.report as WorkflowRunInfo["report"])
          : existing?.report ?? null,
      budget_limit:
        typeof snapshot.budget_limit === "number"
          ? snapshot.budget_limit
          : existing?.budget_limit ?? 0,
      budget_consumed:
        typeof snapshot.budget_consumed === "number"
          ? snapshot.budget_consumed
          : existing?.budget_consumed ?? 0,
      error:
        typeof snapshot.error === "string" ? snapshot.error : existing?.error ?? "",
      status,
      state_version:
        typeof snapshot.state_version === "number"
          ? snapshot.state_version
          : existing?.state_version,
      started_at:
        typeof snapshot.started_at === "string"
          ? snapshot.started_at
          : existing?.started_at ?? new Date().toISOString(),
      completed_at:
        typeof snapshot.completed_at === "string"
          ? snapshot.completed_at
          : existing?.completed_at ?? "",
      updated_at:
        typeof snapshot.updated_at === "string"
          ? snapshot.updated_at
          : existing?.updated_at,
      total_elapsed_ms:
        typeof snapshot.total_elapsed_ms === "number"
          ? snapshot.total_elapsed_ms
          : existing?.total_elapsed_ms,
    };
  }

  function pruneStepDetailsForRun(run: WorkflowRunInfo) {
    if (!run.id) return;

    const currentKeys = new Set<string>();
    for (const step of run.execution_steps) {
      if (!step.id || typeof step.step_version !== "number") continue;
      currentKeys.add(detailKey(run.id, step.id, step.step_version));
    }

    if (currentKeys.size === 0) return;

    const nextDetails = new Map(stepDetails.value);
    for (const key of nextDetails.keys()) {
      if (!key.startsWith(`${run.id}:`)) continue;
      const split = key.split(":");
      if (split.length < 3) continue;
      const stepId = split[1] || "";
      const version = split[2] || "";
      const exact = `${run.id}:${stepId}:${version}`;
      if (!currentKeys.has(exact)) {
        nextDetails.delete(key);
      }
    }
    stepDetails.value = nextDetails;
  }

  function upsertRunSnapshot(
    snapshot: RunSnapshotInput,
    fallbackUserMessageId?: number,
  ): WorkflowRunInfo | null {
    const normalized = normalizeRunSnapshot(snapshot, fallbackUserMessageId);
    if (!normalized) return null;

    const current = runs.value.get(normalized.user_message_id);
    const incomingVersion =
      typeof normalized.state_version === "number" ? normalized.state_version : null;
    const currentVersion =
      typeof current?.state_version === "number" ? current.state_version : null;

    if (
      current &&
      current.id === normalized.id &&
      incomingVersion !== null &&
      currentVersion !== null &&
      incomingVersion <= currentVersion
    ) {
      return current;
    }

    if (current) {
      normalized.execution_steps = mergeHistoricalCarryover(
        current.execution_steps,
        normalized.execution_steps,
      );
    }

    const nextRuns = new Map(runs.value);
    nextRuns.set(normalized.user_message_id, normalized);
    runs.value = nextRuns;

    if (normalized.status === "running") {
      activeUserMessageId.value = normalized.user_message_id;
      loading.value = true;
    } else if (activeUserMessageId.value === normalized.user_message_id) {
      loading.value = false;
    }

    const conversationId =
      normalized.conversation_id ?? currentConversationId.value ?? undefined;
    if (conversationId) {
      const nextRunning = new Set(runningConversations.value);
      if (normalized.status === "running") {
        nextRunning.add(conversationId);
      } else {
        nextRunning.delete(conversationId);
      }
      runningConversations.value = nextRunning;
    }

    if (normalized.status === "error" && normalized.error) {
      error.value = normalized.error;
    }

    pruneStepDetailsForRun(normalized);
    return normalized;
  }

  function clearRunViewState() {
    activeUserMessageId.value = null;
    stepDetails.value = new Map();
    loadingStepDetails.value = new Set();
    stepDetailInflight.clear();
  }

  function applyConversationDetail(detail: ConversationDetail) {
    messages.value = detail.messages;

    const nextRuns = new Map<number, WorkflowRunInfo>();
    for (const rawRun of detail.runs) {
      const normalized = normalizeRunSnapshot(rawRun as RunSnapshotInput);
      if (!normalized) continue;

      const prior = nextRuns.get(normalized.user_message_id);
      if (!prior) {
        nextRuns.set(normalized.user_message_id, normalized);
        continue;
      }

      nextRuns.set(
        normalized.user_message_id,
        mergeRunHistory(prior, normalized),
      );
    }
    runs.value = nextRuns;
    stepDetails.value = new Map();
    loadingStepDetails.value = new Set();
    stepDetailInflight.clear();

    let activeMessageId: number | null = null;
    const runningRun = [...nextRuns.values()].find((r) => r.status === "running");
    if (runningRun) {
      activeMessageId = runningRun.user_message_id;
      loading.value = true;
    } else {
      loading.value = false;
    }

    const nextRunningConversations = new Set(runningConversations.value);
    if (runningRun) {
      nextRunningConversations.add(detail.id);
    } else {
      nextRunningConversations.delete(detail.id);
    }
    runningConversations.value = nextRunningConversations;

    activeUserMessageId.value = activeMessageId;
  }

  async function connectGlobalEvents() {
    disconnectGlobalEvents();
    const abort = new AbortController();
    globalEventsAbort = abort;

    const url = api.eventsUrl();
    let resp: Response;
    try {
      resp = await fetch(url, { signal: abort.signal });
    } catch (e: any) {
      if (e.name === "AbortError") return;
      return;
    }

    if (!resp.ok) return;

    const reader = resp.body?.getReader();
    if (!reader) return;

    const decoder = new TextDecoder();
    let buffer = "";
    let currentEventType = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("event:")) {
            currentEventType = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            const dataStr = line.slice(5).trim();
            if (!dataStr) continue;
            try {
              const payload = JSON.parse(dataStr);
              if (currentEventType === "run_status") {
                const next = new Set(runningConversations.value);
                if (payload.status === "running") {
                  next.add(payload.conversation_id);
                } else {
                  next.delete(payload.conversation_id);
                }
                runningConversations.value = next;
              }
              currentEventType = "";
            } catch {
              // non-JSON data, skip
            }
          } else if (line.trim() === "") {
            currentEventType = "";
          }
        }
      }
    } catch (e: any) {
      if (e.name === "AbortError") return;
    } finally {
      if (globalEventsAbort === abort) {
        globalEventsAbort = null;
      }
    }
  }

  function isConversationRunning(id: string): boolean {
    return runningConversations.value.has(id);
  }

  async function fetchConversations() {
    try {
      conversations.value = await api.listConversations();
    } catch (e: any) {
      error.value = e.message;
    }
  }

  function startNewChat() {
    cancelStream();
    selectConversationRequestId += 1;
    currentConversationId.value = null;
    isNewChat.value = true;
    messages.value = [];
    runs.value = new Map();
    error.value = null;
    loading.value = false;
    clearRunViewState();
  }

  async function selectConversation(id: string) {
    cancelStream();
    const requestId = ++selectConversationRequestId;
    currentConversationId.value = id;
    isNewChat.value = false;
    clearRunViewState();
    loading.value = false;
    try {
      const detail = await api.getConversation(id);
      if (
        requestId !== selectConversationRequestId ||
        currentConversationId.value !== id
      ) {
        return;
      }

      applyConversationDetail(detail);
      const runningRun = detail.runs.find((r) => r.status === "running");
      if (runningRun) {
        activeUserMessageId.value = runningRun.user_message_id;
        loading.value = true;
        void connectStream(id, runningRun.user_message_id);
      }
    } catch (e: any) {
      if (
        requestId !== selectConversationRequestId ||
        currentConversationId.value !== id
      ) {
        return;
      }
      error.value = e.message;
    }
  }

  async function sendMessage(content: string) {
    loading.value = true;
    error.value = null;
    clearRunViewState();

    try {
      let createdNewConversation = false;
      if (!currentConversationId.value) {
        const conversation = await api.createConversation();
        currentConversationId.value = conversation.id;
        isNewChat.value = false;
        conversations.value.unshift(conversation);
        createdNewConversation = true;
      }

      const { run_id, user_message_id } = await api.startRun(
        currentConversationId.value,
        content,
        runSettings.value,
      );

      if (createdNewConversation) {
        generateTitle(currentConversationId.value);
      }

      messages.value.push({
        id: user_message_id,
        role: "user",
        content,
        created_at: new Date().toISOString(),
      });

      activeUserMessageId.value = user_message_id;
      upsertRunSnapshot(
        {
          id: run_id,
          conversation_id: currentConversationId.value,
          user_message_id,
          execution_steps: [],
          tool_executions: [],
          verification_attempts: [],
          report: null,
          budget_limit: runSettings.value.budget ?? 50,
          budget_consumed: 0,
          error: "",
          status: "running",
          state_version: 0,
          started_at: new Date().toISOString(),
          completed_at: "",
          updated_at: new Date().toISOString(),
        },
        user_message_id,
      );

      await connectStream(currentConversationId.value, user_message_id);
    } catch (e: any) {
      error.value = e.message;
      loading.value = false;
    }
  }

  async function stopRun() {
    if (!currentConversationId.value) return;
    try {
      await api.stopRun(currentConversationId.value);
    } catch (e: any) {
      error.value = e.message;
    }
  }

  async function resumeRun() {
    if (!currentConversationId.value) return;
    loading.value = true;
    error.value = null;

    let targetMessageId = activeUserMessageId.value;
    if (!targetMessageId) {
      const candidates = [...runs.value.values()].filter(
        (r) => r.status === "stopped" || r.status === "error",
      );
      const last = candidates.length > 0 ? candidates[candidates.length - 1] : null;
      targetMessageId = last?.user_message_id ?? null;
    }

    try {
      const { run_id, user_message_id } = await api.resumeRun(
        currentConversationId.value,
      );

      const detail = await api.getConversation(currentConversationId.value);
      applyConversationDetail(detail);

      const runningRun = detail.runs.find((r) => r.status === "running");
      if (runningRun) {
        activeUserMessageId.value = runningRun.user_message_id;
        loading.value = true;
      }

      void connectStream(currentConversationId.value, user_message_id);
    } catch (e: any) {
      error.value = e.message;
      loading.value = false;
      if (targetMessageId) {
        activeUserMessageId.value = targetMessageId;
      }
    }
  }

  async function connectStream(
    conversationId: string,
    userMessageId: number,
  ): Promise<void> {
    cancelStream();
    const abort = new AbortController();
    streamAbort = abort;

    const url = api.streamUrl(conversationId);
    let resp: Response;
    try {
      resp = await fetch(url, { signal: abort.signal });
    } catch (e: any) {
      if (e.name === "AbortError") return;
      throw e;
    }

    if (!resp.ok) {
      if (resp.status === 404) {
        const detail = await api.getConversation(conversationId);
        applyConversationDetail(detail);
        loading.value = false;
        return;
      }
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${resp.status}`);
    }

    const reader = resp.body?.getReader();
    if (!reader) throw new Error("No response body");

    const decoder = new TextDecoder();
    let buffer = "";
    let currentEventType = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("event:")) {
            currentEventType = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            const dataStr = line.slice(5).trim();
            if (!dataStr) continue;
            try {
              const payload = JSON.parse(dataStr);
              handleEvent(currentEventType, payload, userMessageId);
              currentEventType = "";
            } catch {
              // non-JSON data, skip
            }
          } else if (line.trim() === "") {
            currentEventType = "";
          }
        }
      }
    } catch (e: any) {
      if (e.name === "AbortError") return;
      if (!e.message?.includes("input stream")) {
        throw e;
      }
    } finally {
      if (streamAbort === abort) {
        streamAbort = null;
      }
    }
  }

  function handleEvent(eventType: string, payload: any, userMessageId?: number) {
    if (eventType === "run_snapshot") {
      const run = upsertRunSnapshot(payload as RunSnapshotInput, userMessageId);
      if (run && run.status !== "running") {
        loading.value = false;
      }
    }
  }

  async function loadStepDetail(
    runId: string,
    stepId: string,
    stepVersion: number,
  ): Promise<ExecutionStepDetailResponse> {
    const key = detailKey(runId, stepId, stepVersion);
    const cached = stepDetails.value.get(key);
    if (cached) return cached;

    const inflight = stepDetailInflight.get(key);
    if (inflight) return inflight;

    const loadingSet = new Set(loadingStepDetails.value);
    loadingSet.add(key);
    loadingStepDetails.value = loadingSet;

    const request = (async () => {
      const detail = await api.getRunStepDetail(runId, Number(stepId));
      const version =
        typeof detail.step_version === "number"
          ? detail.step_version
          : stepVersion;
      const resolvedKey = detailKey(runId, stepId, version);
      const nextDetails = new Map(stepDetails.value);
      nextDetails.set(resolvedKey, detail);
      stepDetails.value = nextDetails;
      return detail;
    })();

    stepDetailInflight.set(key, request);

    try {
      return await request;
    } finally {
      stepDetailInflight.delete(key);
      const nextLoading = new Set(loadingStepDetails.value);
      nextLoading.delete(key);
      loadingStepDetails.value = nextLoading;
    }
  }

  function getStepDetail(
    runId: string,
    stepId: string,
    stepVersion: number,
  ): ExecutionStepDetailResponse | null {
    return stepDetails.value.get(detailKey(runId, stepId, stepVersion)) || null;
  }

  function isStepDetailLoading(
    runId: string,
    stepId: string,
    stepVersion: number,
  ): boolean {
    return loadingStepDetails.value.has(detailKey(runId, stepId, stepVersion));
  }

  async function renameConversation(id: string, title: string) {
    try {
      const updated = await api.updateConversation(id, title);
      const idx = conversations.value.findIndex((c) => c.id === id);
      if (idx !== -1) {
        const existing = conversations.value[idx];
        if (existing) {
          existing.title = updated.title;
        }
      }
    } catch (e: any) {
      error.value = e.message;
    }
  }

  async function generateTitle(id: string) {
    try {
      const updated = await api.generateTitle(id);
      const idx = conversations.value.findIndex((c) => c.id === id);
      if (idx !== -1) {
        const existing = conversations.value[idx];
        if (existing) {
          existing.title = updated.title;
        }
      }
    } catch (e: any) {
      error.value = e.message;
    }
  }

  async function deleteConversation(id: string) {
    try {
      await api.deleteConversation(id);
      conversations.value = conversations.value.filter((c) => c.id !== id);
      if (currentConversationId.value === id) {
        cancelStream();
        selectConversationRequestId += 1;
        currentConversationId.value = null;
        messages.value = [];
        runs.value = new Map();
        loading.value = false;
        clearRunViewState();
      }
    } catch (e: any) {
      error.value = e.message;
    }
  }

  function getRunForMessage(
    messageId: number | undefined,
  ): WorkflowRunInfo | null {
    if (!messageId) return null;
    return runs.value.get(messageId) || null;
  }

  return {
    conversations,
    currentConversationId,
    isNewChat,
    messages,
    loading,
    error,
    runs,
    executionSteps,
    currentStep,
    budgetRemaining,
    budgetConsumed,
    currentReport,
    toolExecutions,
    verificationAttempts,
    verificationAttemptData,
    activeUserMessageId,
    totalElapsedMs,
    runSettings,
    stepDetails,
    loadingStepDetails,
    fetchConversations,
    startNewChat,
    selectConversation,
    sendMessage,
    stopRun,
    resumeRun,
    renameConversation,
    generateTitle,
    deleteConversation,
    getRunForMessage,
    loadStepDetail,
    getStepDetail,
    isStepDetailLoading,
    connectGlobalEvents,
    disconnectGlobalEvents,
    isConversationRunning,
    runningConversations,
    runStopped,
    runErrored,
  };
});
