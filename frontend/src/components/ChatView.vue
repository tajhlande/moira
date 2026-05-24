<script setup lang="ts">
import { NInput, NButton, NAlert, NScrollbar, NSpin } from "naive-ui";
import { ref } from "vue";
import { useChatStore } from "../stores/chat";

const store = useChatStore();
const inputText = ref("");

function send() {
  const text = inputText.value.trim();
  if (!text || store.loading) return;
  inputText.value = "";
  store.sendMessage(text);
}
</script>

<template>
  <div class="chat-container">
    <NScrollbar class="messages-area">
      <div
        v-for="(msg, i) in store.messages"
        :key="i"
        :class="['message', msg.role]"
      >
        <div class="message-role">
          {{ msg.role === "user" ? "You" : "MOiRA" }}
        </div>
        <div class="message-content">{{ msg.content }}</div>
      </div>
      <NSpin v-if="store.loading" style="display: block; margin: 16px auto" />
    </NScrollbar>
    <NAlert v-if="store.error" type="error" style="margin: 8px 16px" closable>
      {{ store.error }}
    </NAlert>
    <div class="input-area">
      <NInput
        v-model:value="inputText"
        :placeholder="
          store.isNewChat ? 'Type your first message...' : 'Type a message...'
        "
        @keyup.enter="send"
        :disabled="store.loading"
      />
      <NButton
        type="primary"
        @click="send"
        :disabled="store.loading || !inputText.trim()"
      >
        Send
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.chat-container {
  display: flex;
  flex-direction: column;
  flex: 1;
  overflow: hidden;
}

.messages-area {
  flex: 1;
  padding: 16px;
}

.message {
  margin-bottom: 12px;
  padding: 8px 12px;
  border-radius: 8px;
  max-width: 80%;
}

.message.user {
  background-color: var(--moira-border, #e0e0e0);
  margin-left: auto;
}

.message.assistant {
  background-color: var(--moira-sidebar-bg, #f0f0f0);
}

.message-role {
  font-weight: bold;
  font-size: 0.85em;
  margin-bottom: 4px;
  opacity: 0.7;
}

.message-content {
  white-space: pre-wrap;
}

.input-area {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid var(--moira-border, #e0e0e0);
}
</style>
