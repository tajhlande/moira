const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

export interface ConversationInfo {
  id: string;
  title: string;
  created_at: string;
}

export interface MessageInfo {
  id: number;
  role: string;
  content: string;
  created_at: string;
}

export interface ExecutionStep {
  id?: string;
  detail_run_id?: string;
  node: string;
  label: string;
  status: "running" | "completed" | "error" | "stopped";
  cost: number;
  budget_remaining: number;
  elapsed_ms?: number;
  started_at?: string;
  error?: string;
  tool_call_count?: number;
  step_version?: number;
  has_detail?: boolean;
  detail?: Record<string, unknown>;
}

export interface ToolExecution {
  tool: string;
  args?: Record<string, unknown>;
  result: string;
  duration_ms: number;
  success: boolean;
}

export interface ResearchReport {
  answer: string;
  citations: { source: string; url?: string; excerpt?: string }[];
  critiques: string[];
  unverified_claims: string[];
  budget_consumed: number;
}

export interface RunAttemptSummary {
  run_id: string;
  status: "running" | "completed" | "error" | "stopped";
  started_at: string;
  completed_at: string;
  updated_at?: string;
  state_version?: number;
}

export interface ExecutionStepDetailResponse {
  run_id: string;
  step_id: number;
  step_version: number;
  has_detail: boolean;
  detail: Record<string, unknown>;
}

export interface WorkflowRunInfo {
  id: string;
  conversation_id?: string;
  user_message_id: number;
  attempts?: RunAttemptSummary[];
  execution_steps: ExecutionStep[];
  tool_executions: ToolExecution[];
  report: ResearchReport | null;
  budget_limit: number;
  budget_consumed: number;
  error: string;
  status: "running" | "completed" | "error" | "stopped";
  state_version?: number;
  started_at: string;
  completed_at: string;
  updated_at?: string;
  total_elapsed_ms?: number;
  input_tokens?: number;
  output_tokens?: number;
  thinking_tokens?: number;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  messages: MessageInfo[];
  runs: WorkflowRunInfo[];
}

export interface RunSettings {
  budget?: number;
}

export interface ModelSelection {
  endpoint: string;
  model: string;
}

export interface ModelAssignments {
  intelligence: ModelSelection;
  task: ModelSelection;
}

export interface ModelsResponse {
  models: { id: string; owned_by: string; endpoint: string }[];
  assignments: ModelAssignments;
}

export interface CredentialInfo {
  owner: string;
  name: string;
  encryption_version: number;
  created_at: string;
  updated_at: string;
}

export interface ToolMetricsRow {
  tool_name: string;
  call_type: string;
  period_hour: string;
  call_count: number;
  success_count: number;
  error_count: number;
  aggregate_duration_ms: number;
  low_duration_ms: number;
  high_duration_ms: number;
}

export interface InferenceMetricsRow {
  model: string;
  purpose: string;
  period_hour: string;
  call_count: number;
  input_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
  prompt_time_ms: number;
  gen_time_ms: number;
}

export const api = {
  health: () => request<{ status: string }>("/health"),

  createConversation: () =>
    request<ConversationInfo>("/conversations", { method: "POST" }),

  listConversations: () => request<ConversationInfo[]>("/conversations"),

  getConversation: (id: string) =>
    request<ConversationDetail>(`/conversations/${id}`),

  getRunStepDetail: (runId: string, stepId: number) =>
    request<ExecutionStepDetailResponse>(`/runs/${runId}/steps/${stepId}/detail`),

  updateConversation: (id: string, title: string) =>
    request<ConversationInfo>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),

  generateTitle: (id: string) =>
    request<ConversationInfo>(`/conversations/${id}/generate-title`, {
      method: "POST",
    }),

  deleteConversation: (id: string) =>
    request<{ status: string }>(`/conversations/${id}`, {
      method: "DELETE",
    }),

  startRun: (conversationId: string, content: string, settings?: RunSettings) =>
    request<{ run_id: string; user_message_id: number }>(
      `/conversations/${conversationId}/messages`,
      {
        method: "POST",
        body: JSON.stringify({ content, settings }),
      },
    ),

  stopRun: (conversationId: string) =>
    request<{ status: string }>(
      `/conversations/${conversationId}/runs/stop`,
      { method: "POST" },
    ),

  resumeRun: (conversationId: string) =>
    request<{ run_id: string; user_message_id: number }>(
      `/conversations/${conversationId}/runs/resume`,
      { method: "POST" },
    ),

  streamUrl: (conversationId: string) =>
    `${API_BASE}/conversations/${conversationId}/stream`,

  eventsUrl: () => `${API_BASE}/events`,

  getModels: () => request<ModelsResponse>("/models"),

  setModels: (assignments: ModelAssignments) =>
    request<ModelAssignments>("/models", {
      method: "PUT",
      body: JSON.stringify(assignments),
    }),

  getTools: () =>
    request<{ tools: ToolInfo[]; groups: ToolGroupInfo[] }>("/tools"),

  patchTool: (name: string, fields: Record<string, unknown>) =>
    request<ToolInfo>(`/tools/${name}`, {
      method: "PATCH",
      body: JSON.stringify(fields),
    }),

  getToolSpec: (name: string) =>
    request<{ config_schema: Record<string, unknown> }>(`/tools/${name}/spec`),

  listCredentials: (owner?: string) => {
    const params = owner ? `?owner=${encodeURIComponent(owner)}` : "";
    return request<{ credentials: CredentialInfo[] }>(`/credentials${params}`);
  },

  createCredential: (
    name: string,
    value: Record<string, unknown>,
    owner?: string,
  ) =>
    request<CredentialInfo>("/credentials", {
      method: "POST",
      body: JSON.stringify({ name, value, ...(owner ? { owner } : {}) }),
    }),

  getCredential: (name: string, owner?: string) => {
    const params = owner ? `?owner=${encodeURIComponent(owner)}` : "";
    return request<CredentialInfo>(`/credentials/${name}${params}`);
  },

  deleteCredential: (name: string, owner?: string) => {
    const params = owner ? `?owner=${encodeURIComponent(owner)}` : "";
    return request<{ status: string }>(`/credentials/${name}${params}`, {
      method: "DELETE",
    });
  },

  getToolMetrics: (start?: string, end?: string) => {
    const params = new URLSearchParams();
    if (start) params.set("start", start);
    if (end) params.set("end", end);
    const qs = params.toString();
    return request<{ metrics: ToolMetricsRow[] }>(
      `/metrics${qs ? `?${qs}` : ""}`,
    );
  },

  getInferenceMetrics: (start?: string, end?: string) => {
    const params = new URLSearchParams();
    if (start) params.set("start", start);
    if (end) params.set("end", end);
    const qs = params.toString();
    return request<{ metrics: InferenceMetricsRow[] }>(
      `/metrics/inference${qs ? `?${qs}` : ""}`,
    );
  },
};

export interface ToolGroupInfo {
  name: string;
  display_name: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  argument_schema: Record<string, unknown>;
  config: Record<string, unknown>;
  tags: string[];
  reliability: string;
  is_default: boolean;
  enabled: boolean;
  built_in: boolean;
  implementation: string;
  group_name: string;
}
