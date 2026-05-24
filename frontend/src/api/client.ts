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

export interface SessionInfo {
  id: string;
  title: string;
  created_at: string;
}

export interface SessionDetail {
  id: string;
  title: string;
  created_at: string;
  messages: MessageInfo[];
}

export interface MessageInfo {
  role: string;
  content: string;
  created_at: string;
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

  createSession: () => request<SessionInfo>("/sessions", { method: "POST" }),

  listSessions: () => request<SessionInfo[]>("/sessions"),

  getSession: (id: string) => request<SessionDetail>(`/sessions/${id}`),

  sendMessage: (sessionId: string, content: string) =>
    request<MessageInfo>(`/sessions/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),

  getModels: () => request<ModelsResponse>("/models"),

  setModels: (assignments: ModelAssignments) =>
    request<ModelAssignments>("/models", {
      method: "PUT",
      body: JSON.stringify(assignments),
    }),
};
