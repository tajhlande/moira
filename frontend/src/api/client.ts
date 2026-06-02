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
  detail?: Record<string, unknown>;
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

export const api = {
  health: () => request<{ status: string }>("/health"),

  createConversation: () =>
    request<ConversationInfo>("/conversations", { method: "POST" }),

  listConversations: () => request<ConversationInfo[]>("/conversations"),

  getConversation: (id: string) =>
    request<ConversationDetail>(`/conversations/${id}`),

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

  startRun: (
    conversationId: string,
    content: string,
    settings?: RunSettings,
  ) =>
    request<{ run_id: string; user_message_id: number }>(
      `/conversations/${conversationId}/messages`,
      {
        method: "POST",
        body: JSON.stringify({ content, settings }),
      },
    ),

  streamUrl: (conversationId: string) =>
    `${API_BASE}/conversations/${conversationId}/stream`,

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

  createCredential: (name: string, value: Record<string, unknown>, owner?: string) =>
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
