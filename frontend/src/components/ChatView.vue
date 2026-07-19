<script setup lang="ts">
import { computed, ref, watch, nextTick } from "vue";
import { NInput, NButton, NAlert, NScrollbar, NText, NSlider } from "naive-ui";
import { IconAdjustments, IconHandStop, IconRefresh } from "@tabler/icons-vue";
import { useRoute, useRouter } from "vue-router";
import {
  DEFAULT_BUDGET,
  DEFAULT_MAX_REVIEW,
  DEFAULT_MAX_EVALUATION,
  useChatStore,
} from "../stores/chat";
import { useDialog } from "naive-ui";
import RunArtifacts from "./RunArtifacts.vue";
import MarkdownContent from "./MarkdownContent.vue";
import ModelSelector from "./ModelSelector.vue";
import CopyButton from "./CopyButton.vue";
import "./workflow-artifacts.css";

const store = useChatStore();
const route = useRoute();
const router = useRouter();
const dialog = useDialog();
const inputText = ref("");
const showSettings = ref(false);
const messagesScrollbar = ref<InstanceType<typeof NScrollbar> | null>(null);
const scrollContent = ref<HTMLElement | null>(null);

// Sync store state with the current route.
// - /conversation/new → startNewChat()
// - /conversation/:id → selectConversation(id) only if not already loaded
watch(
  () => ({ name: route.name, id: route.params.id as string | undefined }),
  (r) => {
    if (r.name === "new-conversation") {
      store.startNewChat();
    } else if (r.id) {
      // Skip re-fetching if we already have this conversation loaded.
      // This happens when sendMessage creates a conversation and we
      // push the route — no need to fetch from the API again.
      if (store.currentConversationId !== r.id) {
        store.selectConversation(r.id);
      }
    }
  },
  { immediate: true },
);

// When sendMessage creates a new conversation, update the URL.
// Uses replace (not push) so the /conversation/new entry is overwritten
// in history — pressing Back doesn't go to a "new" state.
watch(
  () => store.currentConversationId,
  (id) => {
    if (id && route.name === "new-conversation") {
      router.replace({ name: "conversation", params: { id } });
    }
  },
);

// Track conversation switches so we can scroll to the report after data loads.
const justSwitchedConversation = ref(false);

watch(
  () => store.currentConversationId,
  () => {
    justSwitchedConversation.value = true;
  },
);

// On conversation switch: scroll to the top of the latest report.
// Uses a separate watcher from streaming auto-scroll so the two don't
// interfere.  The flag is cleared inside nextTick (not synchronously),
// so the streaming watcher below sees it as true and skips during the
// same flush cycle.
watch(
  () => store.messages,
  () => {
    if (!justSwitchedConversation.value) return;
    nextTick(() => {
      justSwitchedConversation.value = false;
      const reports =
        scrollContent.value?.querySelectorAll<HTMLElement>(".report-panel");
      if (reports && reports.length > 0) {
        reports[reports.length - 1]?.scrollIntoView({
          block: "start",
          behavior: "smooth",
        });
      }
    });
  },
);

// Auto-scroll during streaming as new steps/content arrive.
// Skips during conversation switch (flag still true in same flush cycle).
watch(
  () => [store.activeRun?.execution_steps?.length, store.activeRun?.report],
  () => {
    if (justSwitchedConversation.value) return;
    nextTick(() => {
      messagesScrollbar.value?.scrollTo({ top: 999999, behavior: "smooth" });
    });
  },
);

const currentTitle = computed(() => {
  if (store.isNewChat) return "New Chat";
  const c = store.conversations.find(
    (c) => c.id === store.currentConversationId,
  );
  return c?.title || "Chat";
});

function send() {
  const text = inputText.value.trim();
  if (!text || store.loading) return;
  inputText.value = "";
  store.sendMessage(text);
}

function runForMessage(messageId: number | undefined) {
  return store.getRunForMessage(messageId);
}

function confirmRerun(msgId: number) {
  const hasSubsequent =
    store.messages.findIndex((m) => m.id === msgId) < store.messages.length - 1;

  dialog.warning({
    title: "Rerun from this message?",
    content: hasSubsequent
      ? "This will delete all messages and results after this point and start a new research run. This cannot be undone."
      : "This will discard the previous run and start a new research run from this message.",
    positiveText: "Rerun",
    negativeText: "Cancel",
    onPositiveClick: () => {
      store.rerunFromMessage(msgId);
    },
  });
}
</script>

<template>
  <div class="chat-container">
    <div class="chat-header">
      <ModelSelector
        :conversation-id="store.currentConversationId || ''"
        class="header-model-pill"
      />
      <NText class="chat-title">{{ currentTitle }}</NText>
    </div>

    <NScrollbar ref="messagesScrollbar" class="messages-area">
      <div ref="scrollContent">
        <template v-for="(msg, i) in store.messages" :key="i">
          <!-- Message bubble -->
          <div :class="['message', msg.role]">
            <div class="message-role">
              {{ msg.role === "user" ? "You" : "MOiRA" }}
            </div>
            <MarkdownContent class="message-content" :content="msg.content" />
            <div class="message-actions">
              <NButton
                v-if="msg.role === 'user' && runForMessage(msg.id)"
                quaternary
                circle
                size="tiny"
                class="icon-action-btn"
                title="Rerun from this message"
                @click="confirmRerun(msg.id)"
              >
                <template #icon>
                  <IconRefresh :size="14" />
                </template>
              </NButton>
              <CopyButton :text="msg.content" />
            </div>
          </div>

          <!-- After a user message: render associated run artifacts -->
          <template v-if="msg.role === 'user' && runForMessage(msg.id)">
            <RunArtifacts :run="runForMessage(msg.id)!" />
          </template>
        </template>
      </div>
    </NScrollbar>

    <NAlert v-if="store.error" type="error" style="margin: 8px 40px" closable>
      {{ store.error }}
    </NAlert>
    <div :class="['settings-tray', { open: showSettings }]">
      <div class="settings-tray-inner">
        <div class="settings-row">
          <label class="settings-label">Budget</label>
          <NSlider
            :value="store.runSettings.budget ?? DEFAULT_BUDGET"
            :min="35"
            :max="300"
            :step="1"
            :tooltip="false"
            style="flex: 1"
            @update:value="(v: number) => (store.runSettings.budget = v)"
          />
          <span class="settings-value">{{
            store.runSettings.budget ?? DEFAULT_BUDGET
          }}</span>
        </div>
        <div class="settings-row">
          <label class="settings-label">Review Retries</label>
          <NSlider
            :value="store.runSettings.max_review ?? DEFAULT_MAX_REVIEW"
            :min="1"
            :max="10"
            :step="1"
            :tooltip="false"
            style="flex: 1"
            @update:value="(v: number) => (store.runSettings.max_review = v)"
          />
          <span class="settings-value">{{
            store.runSettings.max_review ?? DEFAULT_MAX_REVIEW
          }}</span>
        </div>
        <div class="settings-row">
          <label class="settings-label">Eval Retries</label>
          <NSlider
            :value="store.runSettings.max_evaluation ?? DEFAULT_MAX_EVALUATION"
            :min="1"
            :max="10"
            :step="1"
            :tooltip="false"
            style="flex: 1"
            @update:value="
              (v: number) => (store.runSettings.max_evaluation = v)
            "
          />
          <span class="settings-value">{{
            store.runSettings.max_evaluation ?? DEFAULT_MAX_EVALUATION
          }}</span>
        </div>
      </div>
    </div>
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
        quaternary
        circle
        :type="showSettings ? 'primary' : 'default'"
        @click="showSettings = !showSettings"
        title="Run settings"
      >
        <template #icon>
          <IconAdjustments :size="18" />
        </template>
      </NButton>
      <NButton v-if="store.loading" type="warning" @click="store.stopRun()">
        <template #icon>
          <IconHandStop :size="16" />
        </template>
        Stop
      </NButton>
      <NButton
        v-else
        type="primary"
        @click="send"
        :disabled="!inputText.trim()"
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

.chat-header {
  padding: 20px 40px 12px;
  border-bottom: 1px solid var(--moira-border, #e0e0e0);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 16px;
}

.chat-title {
  font-size: 1.2em;
  font-weight: 600;
}

.header-model-pill {
  flex-shrink: 0;
}

.messages-area {
  flex: 1;
  min-height: 0;
  padding: 24px 40px;
}

.message {
  margin-top: 16px;
  margin-bottom: 16px;
  padding: 12px 16px;
  border-radius: 8px;
  max-width: 80%;
  margin-left: 16px;
  margin-right: 16px;
  position: relative;
}

.message.user {
  background-color: var(--moira-user-message-bg, #e0e0e0);
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
}

.message-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 4px;
}

.input-area {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: var(--bottom-bar-height, 66px);
  box-sizing: border-box;
  padding: 0 40px;
  border-top: 1px solid var(--moira-border, #e0e0e0);
}

.settings-tray {
  overflow: hidden;
  max-height: 0;
  opacity: 0;
  flex-shrink: 0;
  transition:
    max-height 200ms ease,
    opacity 150ms ease,
    padding 200ms ease;
  padding: 0 40px;
  border-top: 1px solid transparent;
}

.settings-tray.open {
  max-height: 300px;
  opacity: 1;
  padding: 12px 40px;
  border-top-color: var(--moira-border, #e0e0e0);
}

.settings-tray-inner {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.settings-row {
  display: flex;
  align-items: center;
  gap: 12px;
  max-width: 320px;
}

.settings-label {
  font-size: 0.85em;
  font-weight: 500;
  opacity: 0.7;
  min-width: 50px;
}

.settings-value {
  font-size: 0.85em;
  font-weight: 600;
  min-width: 28px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
</style>
