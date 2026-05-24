import { defineStore } from "pinia";
import { ref } from "vue";
import { api, type SessionInfo, type MessageInfo } from "../api/client";

export const useChatStore = defineStore("chat", () => {
  const sessions = ref<SessionInfo[]>([]);
  const currentSessionId = ref<string | null>(null);
  const isNewChat = ref(true);
  const messages = ref<MessageInfo[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function fetchSessions() {
    try {
      sessions.value = await api.listSessions();
    } catch (e: any) {
      error.value = e.message;
    }
  }

  function startNewChat() {
    currentSessionId.value = null;
    isNewChat.value = true;
    messages.value = [];
    error.value = null;
  }

  async function selectSession(id: string) {
    currentSessionId.value = id;
    isNewChat.value = false;
    try {
      const detail = await api.getSession(id);
      messages.value = detail.messages;
    } catch (e: any) {
      error.value = e.message;
    }
  }

  async function sendMessage(content: string) {
    loading.value = true;
    error.value = null;
    messages.value.push({
      role: "user",
      content,
      created_at: new Date().toISOString(),
    });

    try {
      if (!currentSessionId.value) {
        const session = await api.createSession();
        currentSessionId.value = session.id;
        isNewChat.value = false;
        sessions.value.unshift(session);
      }
      const response = await api.sendMessage(currentSessionId.value, content);
      messages.value.push(response);
    } catch (e: any) {
      error.value = e.message;
    } finally {
      loading.value = false;
    }
  }

  return {
    sessions,
    currentSessionId,
    isNewChat,
    messages,
    loading,
    error,
    fetchSessions,
    startNewChat,
    selectSession,
    sendMessage,
  };
});
