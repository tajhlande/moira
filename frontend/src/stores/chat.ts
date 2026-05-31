import { defineStore } from "pinia";
import { ref } from "vue";
import {
  api,
  type ConversationInfo,
  type ConversationDetail,
  type MessageInfo,
  type WorkflowRunInfo,
  type ExecutionStep,
  type ResearchReport,
  type ToolExecution,
  type VerificationAttempt,
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

export const useChatStore = defineStore("chat", () => {
  const conversations = ref<ConversationInfo[]>([]);
  const currentConversationId = ref<string | null>(null);
  const isNewChat = ref(true);
  const messages = ref<MessageInfo[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);

  const runs = ref<Map<number, WorkflowRunInfo>>(new Map());

  // Live streaming state for the current in-progress run
  const executionSteps = ref<ExecutionStep[]>([]);
  const currentStep = ref<ExecutionStep | null>(null);
  const toolExecutions = ref<ToolExecution[]>([]);
  const verificationAttemptData = ref<VerificationAttempt[]>([]);
  const currentReport = ref<ResearchReport | null>(null);
  const budgetRemaining = ref<number | null>(null);
  const budgetConsumed = ref<number>(0);
  const verificationAttempts = ref(0);
  const activeUserMessageId = ref<number | null>(null);
  const totalElapsedMs = ref<number | null>(null);

  async function fetchConversations() {
    try {
      conversations.value = await api.listConversations();
    } catch (e: any) {
      error.value = e.message;
    }
  }

  function startNewChat() {
    currentConversationId.value = null;
    isNewChat.value = true;
    messages.value = [];
    error.value = null;
    resetWorkflowState();
  }

  function resetWorkflowState() {
    executionSteps.value = [];
    currentStep.value = null;
    toolExecutions.value = [];
    verificationAttemptData.value = [];
    currentReport.value = null;
    budgetRemaining.value = null;
    budgetConsumed.value = 0;
    verificationAttempts.value = 0;
    activeUserMessageId.value = null;
    totalElapsedMs.value = null;
  }

  async function selectConversation(id: string) {
    currentConversationId.value = id;
    isNewChat.value = false;
    resetWorkflowState();
    try {
      const detail = await api.getConversation(id);
      messages.value = detail.messages;
      const runMap = new Map<number, WorkflowRunInfo>();
      for (const run of detail.runs) {
        runMap.set(run.user_message_id, run);
      }
      runs.value = runMap;

      // Reconnect to any in-flight run. Don't seed live workflow state here —
      // the SSE replay will populate it from scratch. The loading spinner
      // provides feedback until the first event arrives.
      const runningRun = detail.runs.find((r) => r.status === "running");
      if (runningRun) {
        loading.value = true;
        connectStream(id, runningRun.user_message_id);
      }
    } catch (e: any) {
      error.value = e.message;
    }
  }

  async function sendMessage(content: string) {
    loading.value = true;
    error.value = null;
    resetWorkflowState();

    try {
      let createdNewConversation = false;
      if (!currentConversationId.value) {
        const conversation = await api.createConversation();
        currentConversationId.value = conversation.id;
        isNewChat.value = false;
        conversations.value.unshift(conversation);
        createdNewConversation = true;
      }

      // POST to start the run (returns JSON with run_id + user_message_id).
      // This also persists the user message, so fire title generation after.
      const { user_message_id } = await api.startRun(
        currentConversationId.value,
        content,
      );

      // Fire title generation after the user message is persisted — don't
      // wait for the workflow to complete. The task model generates a title
      // from the user's message content. Only runs for new conversations.
      if (createdNewConversation) {
        generateTitle(currentConversationId.value);
      }

      // Push the real user message (server-assigned ID)
      messages.value.push({
        id: user_message_id,
        role: "user",
        content,
        created_at: new Date().toISOString(),
      });
      activeUserMessageId.value = user_message_id;

      // Connect to the SSE stream for live events
      await connectStream(currentConversationId.value, user_message_id);
    } catch (e: any) {
      error.value = e.message;
    } finally {
      loading.value = false;
    }
  }

  async function connectStream(
    conversationId: string,
    userMessageId: number,
  ): Promise<void> {
    const url = api.streamUrl(conversationId);
    const resp = await fetch(url);

    if (!resp.ok) {
      // 404 means the run already completed between POST and GET.
      // Re-fetch conversation state to get the persisted run.
      if (resp.status === 404) {
        const detail = await api.getConversation(conversationId);
        const run = detail.runs.find((r) => r.user_message_id === userMessageId);
        if (run) {
          const newMap = new Map(runs.value);
          newMap.set(userMessageId, run);
          runs.value = newMap;
        }
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
      // "Error in input stream" is expected when the server closes the SSE
      // connection after the run completes. The run_complete event was already
      // processed, so this is just the stream teardown — safe to ignore.
      if (!e.message?.includes("input stream")) {
        throw e;
      }
    }
  }

  function handleEvent(eventType: string, payload: any, userMessageId?: number) {
    switch (eventType) {
      case "node_start":
        if (currentStep.value) {
          currentStep.value.status = "completed";
          executionSteps.value.push(currentStep.value);
        }
        currentStep.value = {
          node: payload.node,
          label: STAGE_LABELS[payload.node] || payload.node,
          status: "running",
          cost: 0,
          budget_remaining: budgetRemaining.value ?? 0,
          started_at: payload.started_at || new Date().toISOString(),
          elapsed_ms: 0,
        };
        break;
      case "node_end":
        if (payload.budget_remaining !== undefined) {
          budgetRemaining.value = payload.budget_remaining;
        }
        if (currentStep.value) {
          const prevBudget = currentStep.value.budget_remaining;
          currentStep.value.budget_remaining =
            payload.budget_remaining ?? prevBudget;
          currentStep.value.cost =
            prevBudget - (payload.budget_remaining ?? prevBudget);
          currentStep.value.status = "completed";
          currentStep.value.elapsed_ms = payload.elapsed_ms ?? 0;
          if (payload.detail) {
            currentStep.value.detail = payload.detail;
          }
          executionSteps.value.push(currentStep.value);
          currentStep.value = null;
        }
        break;
      case "budget_update":
        budgetRemaining.value = payload.budget_remaining;
        budgetConsumed.value = payload.budget_consumed;
        break;
      case "tool_result":
        {
          const toolEntry = {
            tool: payload.tool,
            result: payload.result ?? payload.output,
            duration_ms: payload.duration_ms,
            success: payload.success,
          };
          toolExecutions.value.push(toolEntry);

          // Attach to the current running step's detail so tool results
          // appear inline under the step that produced them.
          if (currentStep.value) {
            if (!currentStep.value.detail) {
              currentStep.value.detail = {};
            }
            if (!currentStep.value.detail.tool_results) {
              currentStep.value.detail.tool_results = [];
            }
            (currentStep.value.detail.tool_results as Array<typeof toolEntry>).push(toolEntry);
          }
        }
        break;
      case "verification_report":
        verificationAttempts.value = payload.attempt;
        verificationAttemptData.value.push({
          report: payload.report,
          attempt: payload.attempt,
        });
        break;
      case "run_complete":
        if (payload.report) {
          currentReport.value = payload.report;
        }
        totalElapsedMs.value = payload.total_elapsed_ms ?? null;
        finalizeRun(userMessageId);
        break;
      case "run_error":
        error.value = payload.error;
        if (currentStep.value) {
          currentStep.value.status = "error";
          currentStep.value.error = payload.error;
          currentStep.value.elapsed_ms = payload.elapsed_ms ?? 0;
          executionSteps.value.push(currentStep.value);
          currentStep.value = null;
        }
        totalElapsedMs.value = payload.total_elapsed_ms ?? null;
        finalizeRun(userMessageId);
        loading.value = false;
        break;
    }
  }

  function finalizeRun(userMessageId?: number) {
    const msgId = userMessageId ?? activeUserMessageId.value;
    if (!msgId) return;

    const allSteps = [...executionSteps.value];
    if (currentStep.value) {
      allSteps.push({ ...currentStep.value });
    }

    const run: WorkflowRunInfo = {
      id: "",
      user_message_id: msgId,
      execution_steps: allSteps,
      tool_executions: [...toolExecutions.value],
      verification_attempts: [...verificationAttemptData.value],
      report: currentReport.value,
      budget_limit: budgetRemaining.value
        ? budgetConsumed.value + budgetRemaining.value
        : 0,
      budget_consumed: budgetConsumed.value,
      error: error.value || "",
      status: currentReport.value ? "completed" : "error",
      started_at: allSteps[0]?.started_at ?? new Date().toISOString(),
      completed_at: new Date().toISOString(),
      total_elapsed_ms: totalElapsedMs.value ?? undefined,
    };

    const newMap = new Map(runs.value);
    newMap.set(msgId, run);
    runs.value = newMap;
  }

  async function renameConversation(id: string, title: string) {
    try {
      const updated = await api.updateConversation(id, title);
      const idx = conversations.value.findIndex((c) => c.id === id);
      if (idx !== -1) {
        conversations.value[idx].title = updated.title;
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
        conversations.value[idx].title = updated.title;
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
        currentConversationId.value = null;
        messages.value = [];
        runs.value.clear();
        resetWorkflowState();
      }
    } catch (e: any) {
      error.value = e.message;
    }
  }

  function getRunForMessage(messageId: number | undefined): WorkflowRunInfo | null {
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
    fetchConversations,
    startNewChat,
    selectConversation,
    sendMessage,
    renameConversation,
    generateTitle,
    deleteConversation,
    getRunForMessage,
  };
});
