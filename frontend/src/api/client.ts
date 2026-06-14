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

export interface FactRecord {
  id: string;
  subject: string;
  fact_needed: string;
  claim?: string;
  relation?: string;
  value?: string;
  status: string;
  verification_note?: string;
}

export interface ConclusionRecord {
  id: string;
  conclusion: string;
  supporting_fact_ids: string[];
  reasoning?: string;
  status: string;
}

export interface CitationRecord {
  id: string;
  source: string;
  url?: string;
  title?: string;
  excerpt?: string;
}

export interface KnowledgeSummary {
  question: string;
  user_goal: string;
  topic: string;
  entities: string[];
  concepts: string[];
  facts: Record<string, FactRecord[]>;
  conclusions: Record<string, ConclusionRecord[]>;
  citations: CitationRecord[];
}

export interface ResearchReport {
  answer: string;
  citations: { source: string; url?: string; excerpt?: string }[];
  verified_facts: { id: string; subject: string; claim: string; status: string }[];
  verified_conclusions: { id: string; conclusion: string; status: string }[];
  contradicted: { id: string; subject?: string; conclusion?: string; status: string }[];
  unknown_facts: { id: string; subject: string; fact_needed: string; status: string }[];
  critiques: string[];
  total_cost: number;
  tool_call_total_cost: number;
  generation_path?: "verified" | "budget_exhausted" | "error";
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
  knowledge: KnowledgeSummary | null;
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

  getRunKnowledge: (runId: string) =>
    request<{ run_id: string; knowledge: KnowledgeSummary | null }>(`/runs/${runId}/knowledge`),

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

  rerunMessage: (conversationId: string, userMessageId: number, settings?: RunSettings) =>
    request<{ run_id: string; user_message_id: number }>(
      `/conversations/${conversationId}/messages/${userMessageId}/rerun`,
      { method: "POST", body: JSON.stringify({ settings }) },
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

  bulkPatchTools: (updates: { name: string; [key: string]: unknown }[]) =>
    request<{ updated: ToolInfo[] }>("/tool-admin/bulk", {
      method: "PATCH",
      body: JSON.stringify({ updates }),
    }),

  getToolSpec: (name: string) =>
    request<{ config_schema: Record<string, unknown> }>(`/tools/${name}/spec`),

  embeddingSearch: (q: string, topK?: number) => {
    const params = new URLSearchParams({ q });
    if (topK) params.set("top_k", String(topK));
    return request<{
      query: string;
      results: { name: string; description: string; enabled: boolean; distance: number }[];
    }>(`/tools/embeddings/search?${params}`);
  },

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

  getSettingDefinitions: () =>
    request<{ definitions: SettingDefinition[] }>("/settings/definitions"),

  getSettings: (prefix?: string) => {
    const params = new URLSearchParams();
    if (prefix) params.set("prefix", prefix);
    const qs = params.toString();
    return request<{ settings: SettingEntry[] }>(`/settings${qs ? `?${qs}` : ""}`);
  },

  getSetting: (key: string) =>
    request<SettingEntry>(`/settings/${encodeURIComponent(key)}`),

  setSetting: (key: string, value: string | number) =>
    request<SettingEntry>(`/settings/${encodeURIComponent(key)}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),

  batchSetSettings: (settings: { key: string; value: string }[]) =>
    request<{ settings: SettingEntry[] }>("/settings", {
      method: "PUT",
      body: JSON.stringify({ settings }),
    }),

  resetSettings: (keys?: string[]) => {
    const params = new URLSearchParams();
    if (keys?.length) params.set("keys", keys.join(","));
    const qs = params.toString();
    return request<{ settings: SettingEntry[] }>(
      `/settings${qs ? `?${qs}` : ""}`,
      { method: "DELETE" },
    );
  },

  ingestStart: (body: IngestStartRequest) =>
    request<IngestPreview>("/tools/ingest/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  ingestCommit: (body: IngestCommitRequest) =>
    request<IngestCommitResponse>("/tools/ingest/commit", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listIngestSources: () =>
    request<{ sources: ApiSourceInfo[] }>("/tools/ingest/sources"),

  getIngestSource: (sourceId: string) =>
    request<ApiSourceInfo>(`/tools/ingest/sources/${sourceId}`),

  deleteIngestSource: (sourceId: string) =>
    request<{ status: string; deleted_tools: string[] }>(
      `/tools/ingest/sources/${sourceId}`,
      { method: "DELETE" },
    ),

  renameToolGroup: (name: string, displayName: string) =>
    request<{ name: string; display_name: string }>(
      `/tool-admin/groups/${encodeURIComponent(name)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ display_name: displayName }),
      },
    ),

  toggleToolGroup: (name: string, enabled: boolean) =>
    request<{ toggled: number; enabled: boolean }>(
      `/tool-admin/groups/${encodeURIComponent(name)}/toggle`,
      {
        method: "POST",
        body: JSON.stringify({ enabled }),
      },
    ),

  deleteToolGroup: (name: string) =>
    request<{ deleted: string[] }>(
      `/tool-admin/groups/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),

  deleteTool: (name: string) =>
    request<{ deleted: string }>(
      `/tool-admin/tools/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),
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
  original_description: string;
}

export interface SettingDefinition {
  key: string;
  type: "string" | "integer" | "float" | "boolean";
  default: string;
  label: string;
  description: string;
  group: string;
  constraints: Record<string, unknown>;
}

export interface SettingEntry {
  key: string;
  value: string;
  type: string;
  label: string;
  description: string;
  group: string;
  constraints: Record<string, unknown>;
  scope: string;
  scope_id: string;
}

export interface IngestStartRequest {
  url?: string;
  spec_url?: string;
  spec_content?: string;
  group_name?: string;
}

export interface IngestOperation {
  name: string;
  description: string;
  method: string;
  path: string;
  parameters: {
    name: string;
    location: string;
    required: boolean;
    schema_def: Record<string, unknown>;
    description: string;
  }[];
  request_body: {
    content_type: string;
    schema_def: Record<string, unknown>;
    required: boolean;
    description: string;
  } | null;
  tags: string[];
  deprecated: boolean;
  security_requirements: string[];
  operation_id: string | null;
}

export interface IngestPreview {
  source_id: string;
  api_title: string;
  api_description: string;
  api_version: string;
  spec_format: string;
  server_urls: string[];
  base_url: string;
  security_schemes: Record<
    string,
    {
      scheme_type: string;
      name: string;
      location: string;
      description: string;
    }
  >;
  operations: IngestOperation[];
  total_operations: number;
  auth_required: boolean;
  auth_type: string | null;
  group_name: string;
  group_slug: string;
  spec_url: string | null;
}

export interface IngestCommitRequest {
  source_id: string;
  base_url?: string;
  spec_url?: string | null;
  spec_format?: string;
  group_name: string;
  auth_type?: string | null;
  selected_operations: string[];
  operations: IngestOperation[];
  server_url: string;
  is_default?: boolean;
}

export interface IngestCommitResponse {
  succeeded: string[];
  failed: { name: string; reason: string }[];
  disabled: string[];
  total: number;
}

export interface ApiSourceInfo {
  id: string;
  name: string;
  base_url: string;
  spec_url: string | null;
  spec_format: string;
  auth_type: string | null;
  group_name: string;
  tool_count: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}
