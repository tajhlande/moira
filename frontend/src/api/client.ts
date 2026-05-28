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
  node: string;
  label: string;
  status: "running" | "completed" | "error";
  cost: number;
  budget_remaining: number;
  elapsed_ms?: number;
  started_at?: string;
  error?: string;
}

export interface ToolExecution {
  tool: string;
  result: string;
  duration_ms: number;
  success: boolean;
}

export interface ResearchReport {
  answer: string;
  citations: { source: string; url?: string; excerpt?: string }[];
  support: { content: string; source: string }[];
  critiques: string[];
  unverified_claims: string[];
  budget_consumed: number;
  generation_path?: string;
}

export interface VerificationAttempt {
  report: any;
  attempt: number;
}

export interface WorkflowRunInfo {
  id: string;
  user_message_id: number;
  execution_steps: ExecutionStep[];
  tool_executions: ToolExecution[];
  verification_attempts: VerificationAttempt[];
  thinking_traces: Record<string, string>;
  report: ResearchReport | null;
  budget_limit: number;
  budget_consumed: number;
  error: string;
  status: "running" | "completed" | "error";
  started_at: string;
  completed_at: string;
  total_elapsed_ms?: number;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  messages: MessageInfo[];
  runs: WorkflowRunInfo[];
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

export const api = {
  health: () => request<{ status: string }>("/health"),

  createConversation: () =>
    request<ConversationInfo>("/conversations", { method: "POST" }),

  listConversations: () =>
    request<ConversationInfo[]>("/conversations"),

  getConversation: (id: string) =>
    request<ConversationDetail>(`/conversations/${id}`),

  updateConversation: (id: string, title: string) =>
    request<ConversationInfo>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),

  getModels: () => request<ModelsResponse>("/models"),

  setModels: (assignments: ModelAssignments) =>
    request<ModelAssignments>("/models", {
      method: "PUT",
      body: JSON.stringify(assignments),
    }),
};
