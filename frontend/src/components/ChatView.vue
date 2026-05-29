<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted } from "vue";
import {
  NInput,
  NButton,
  NAlert,
  NScrollbar,
  NCollapse,
  NCollapseItem,
  NText,
} from "naive-ui";
import {
  CircleCheck,
  CircleX,
  Loader,
  Copy,
} from "@vicons/tabler";
import { NIcon } from "naive-ui";
import { useRoute, useRouter } from "vue-router";
import { useChatStore } from "../stores/chat";
import type { ExecutionStep } from "../api/client";
import RunArtifacts from "./RunArtifacts.vue";
import ReportPanel from "./ReportPanel.vue";
import MarkdownContent from "./MarkdownContent.vue";
import "./workflow-artifacts.css";

const store = useChatStore();
const route = useRoute();
const router = useRouter();
const inputText = ref("");

// Live clock: updates every second while a step is running
const nowMs = ref(Date.now());
let clockInterval: ReturnType<typeof setInterval> | null = null;

onMounted(() => {
  clockInterval = setInterval(() => { nowMs.value = Date.now(); }, 1000);
});
onUnmounted(() => {
  if (clockInterval) clearInterval(clockInterval);
});

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
  { immediate: true }
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
  }
);

function formatElapsed(ms: number | undefined): string {
  if (ms === undefined || ms === null) return "";
  const totalSec = Math.floor(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

// Compute live elapsed for the currently running step
function liveElapsedMs(step: ExecutionStep): number | undefined {
  if (step.status !== "running" || !step.started_at) return step.elapsed_ms;
  const start = new Date(step.started_at).getTime();
  return nowMs.value - start;
}

const currentTitle = computed(() => {
  if (store.isNewChat) return "New Chat";
  const c = store.conversations.find(
    (c) => c.id === store.currentConversationId
  );
  return c?.title || "Chat";
});

// Find the index of the last user message in the messages array.
// Live streaming state belongs to this message.
const lastUserMessageIndex = computed(() => {
  for (let i = store.messages.length - 1; i >= 0; i--) {
    if (store.messages[i].role === "user") return i;
  }
  return -1;
});

function hasLiveData(): boolean {
  return (
    store.executionSteps.length > 0 ||
    store.currentStep !== null ||
    store.toolExecutions.length > 0 ||
    store.currentReport !== null
  );
}

function send() {
  const text = inputText.value.trim();
  if (!text || store.loading) return;
  inputText.value = "";
  store.sendMessage(text);
}

// Check if the last message is a user message that has an active live stream
// (no persisted run yet). If so, the live workflowSteps/currentStep/toolCalls/
// currentReport belong to that message.
function isLiveRun(messageId: number | undefined): boolean {
  if (!messageId || messageId < 0) return true;
  return !store.runs.has(messageId);
}

const copiedMsgIndex = ref<number | null>(null);

async function copyMessage(content: string, index: number) {
  await navigator.clipboard.writeText(content);
  copiedMsgIndex.value = index;
  setTimeout(() => { copiedMsgIndex.value = null; }, 1500);
}
</script>

<template>
  <div class="chat-container">
    <div class="chat-header">
      <NText class="chat-title">{{ currentTitle }}</NText>
    </div>

    <NScrollbar class="messages-area">
      <template
        v-for="(msg, i) in store.messages"
        :key="i"
      >
        <!-- Message bubble -->
        <div :class="['message', msg.role]">
          <div class="message-role">
            {{ msg.role === "user" ? "You" : "MOiRA" }}
          </div>
          <MarkdownContent class="message-content" :content="msg.content" />
          <div class="message-actions">
            <NButton
              quaternary
              circle
              size="tiny"
              class="copy-btn"
              @click="copyMessage(msg.content, i)"
            >
              <template #icon>
                <NIcon size="14">
                  <Copy v-if="copiedMsgIndex !== i" />
                  <CircleCheck v-else />
                </NIcon>
              </template>
            </NButton>
          </div>
        </div>

        <!-- After a user message: render associated run artifacts -->
          <template v-if="msg.role === 'user'">
          <!-- Persisted run (completed — from DB via selectConversation) -->
          <template v-if="store.getRunForMessage(msg.id) && store.getRunForMessage(msg.id)!.status !== 'running'" as="template">
            <RunArtifacts :run="store.getRunForMessage(msg.id)!" />
          </template>

          <!-- Live streaming run (steps/report from current or completed stream) -->
          <template
            v-else-if="i === lastUserMessageIndex && hasLiveData()"
          >
            <!-- Steps -->
            <div v-if="store.executionSteps.length > 0 || store.currentStep" class="steps-container">
              <div
                v-for="(step, si) in store.executionSteps"
                :key="'ls-' + si"
                :class="['step-row', step.status]"
              >
                <NIcon v-if="step.status === 'completed'" :size="16" color="#18a058">
                  <CircleCheck />
                </NIcon>
                <NIcon v-else :size="16" color="#d03050">
                  <CircleX />
                </NIcon>
                <span class="step-label">{{ step.label }}</span>
                <span v-if="step.status === 'completed'" class="step-cost">-{{ step.cost }}</span>
                <span v-if="step.elapsed_ms != null" class="step-elapsed">{{ formatElapsed(step.elapsed_ms) }}</span>
                <span v-if="step.status === 'completed'" class="step-budget">{{ step.budget_remaining }} remaining</span>
                <span v-if="step.status === 'error' && step.error" class="step-error-msg">{{ step.error }}</span>
              </div>
              <div v-if="store.currentStep" class="step-row running">
                <NIcon :size="16" class="spinning">
                  <Loader />
                </NIcon>
                <span class="step-label">{{ store.currentStep.label }}</span>
                <span class="step-elapsed">{{ formatElapsed(liveElapsedMs(store.currentStep)) }}</span>
                <span class="step-budget">{{ store.currentStep.budget_remaining }} remaining</span>
              </div>
            </div>

            <!-- Tool executions -->
            <NCollapse v-if="store.toolExecutions.length > 0" class="tool-calls-panel">
              <NCollapse-item :title="`Tool Executions (${store.toolExecutions.length})`" name="tools">
                <div v-for="(tc, tci) in store.toolExecutions" :key="tci" class="tool-call">
                  <span :class="['tool-name', tc.success ? 'success' : 'error']">
                    {{ tc.tool }}
                  </span>
                  <span class="tool-duration">{{ tc.duration_ms }}ms</span>
                  <pre class="tool-output">{{ tc.result?.slice(0, 200) }}</pre>
                </div>
              </NCollapse-item>
            </NCollapse>

            <!-- Report -->
            <ReportPanel v-if="store.currentReport" :report="store.currentReport" />

            <!-- Total cycle time -->
            <div v-if="store.totalElapsedMs != null" class="total-elapsed">
              Total: {{ formatElapsed(store.totalElapsedMs) }}
            </div>
          </template>
        </template>
      </template>
    </NScrollbar>

    <NAlert v-if="store.error" type="error" style="margin: 8px 40px" closable>
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

.chat-header {
  padding: 20px 40px 12px;
  border-bottom: 1px solid var(--moira-border, #e0e0e0);
  flex-shrink: 0;
}

.chat-title {
  font-size: 1.2em;
  font-weight: 600;
}

.messages-area {
  flex: 1;
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

.message-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 4px;
}

.copy-btn {
  color: var(--n-text-color-3, #999);
}

.copy-btn:hover {
  color: var(--n-primary-color, #18a058);
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
</style>
