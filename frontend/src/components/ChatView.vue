<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from "vue";
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
} from "@vicons/tabler";
import { NIcon } from "naive-ui";
import { useChatStore } from "../stores/chat";
import type { WorkflowRunInfo, ExecutionStep, ResearchReport } from "../api/client";

const store = useChatStore();
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
          <div class="message-content">{{ msg.content }}</div>
        </div>

        <!-- After a user message: render associated run artifacts -->
        <template v-if="msg.role === 'user'">
          <!-- Persisted run (from DB via selectConversation) -->
          <template v-if="store.getRunForMessage(msg.id)" as="template">
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

<!-- Sub-component: renders persisted run artifacts (steps, tool calls, report) -->
<script lang="ts">
import { defineComponent, type PropType } from "vue";
import {
  NCollapse,
  NCollapseItem,
  NIcon,
} from "naive-ui";
import { CircleCheck, CircleX } from "@vicons/tabler";

const RunArtifacts = defineComponent({
  name: "RunArtifacts",
  components: { NCollapse, NCollapseItem, NIcon, CircleCheck, CircleX },
  props: {
    run: { type: Object as PropType<WorkflowRunInfo>, required: true },
  },
  methods: {
    formatElapsed(ms: number | undefined): string {
      if (ms === undefined || ms === null) return "";
      const totalSec = Math.floor(ms / 1000);
      const min = Math.floor(totalSec / 60);
      const sec = totalSec % 60;
      return `${min}:${sec.toString().padStart(2, "0")}`;
    },
  },
  template: `
    <div>
      <div v-if="run.execution_steps.length > 0" class="steps-container">
        <div
          v-for="(step, si) in run.execution_steps"
          :key="'rs-' + si"
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
      </div>

      <NCollapse v-if="run.tool_executions.length > 0" class="tool-calls-panel">
        <NCollapse-item :title="'Tool Executions (' + run.tool_executions.length + ')'" name="tools">
          <div v-for="(tc, tci) in run.tool_executions" :key="tci" class="tool-call">
            <span :class="['tool-name', tc.success ? 'success' : 'error']">
              {{ tc.tool }}
            </span>
            <span class="tool-duration">{{ tc.duration_ms }}ms</span>
            <pre class="tool-output">{{ tc.result?.slice(0, 200) }}</pre>
          </div>
        </NCollapse-item>
      </NCollapse>

      <ReportPanel v-if="run.report" :report="run.report" />

      <div v-if="run.total_elapsed_ms != null" class="total-elapsed">
        Total: {{ formatElapsed(run.total_elapsed_ms) }}
      </div>

      <div v-if="run.error && run.status === 'error'" class="run-error">
        Run failed: {{ run.error }}
      </div>
    </div>
  `,
});

const ReportPanel = defineComponent({
  name: "ReportPanel",
  props: {
    report: { type: Object as PropType<ResearchReport>, required: true },
  },
  template: `
    <div class="report-panel">
      <h3>Research Report</h3>
      <div class="report-answer">{{ report.answer }}</div>

      <div v-if="report.citations.length > 0" class="report-section">
        <h4>Citations</h4>
        <ul>
          <li v-for="(c, ci) in report.citations" :key="ci">
            {{ c.source }}{{ c.url ? ' — ' + c.url : '' }}
            <span v-if="c.excerpt" class="citation-excerpt">{{ c.excerpt }}</span>
          </li>
        </ul>
      </div>

      <div v-if="report.critiques.length > 0" class="report-section">
        <h4>Critiques</h4>
        <ul>
          <li v-for="(c, ci) in report.critiques" :key="ci">{{ c }}</li>
        </ul>
      </div>

      <div v-if="report.unverified_claims.length > 0" class="report-section">
        <h4>Unverified Claims</h4>
        <ul>
          <li v-for="(c, ci) in report.unverified_claims" :key="ci" class="unverified">
            {{ c }}
          </li>
        </ul>
      </div>

      <div class="budget-consumed">
        Budget consumed: {{ report.budget_consumed.toFixed(0) }}
      </div>
    </div>
  `,
});

export { RunArtifacts, ReportPanel };
</script>

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
  margin-bottom: 16px;
  padding: 12px 16px;
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

.steps-container {
  margin: 12px 0;
  padding: 12px 16px;
  background-color: var(--moira-sidebar-bg, #f5f5f5);
  border-radius: 8px;
  max-width: 80%;
}

.step-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 0.9em;
  border-bottom: 1px solid var(--moira-border, #e0e0e0);
}

.step-row:last-child {
  border-bottom: none;
}

.step-row.completed {
  opacity: 0.75;
}

.step-row.running {
  font-weight: 500;
}

.step-row.error {
  color: #d03050;
}

.step-label {
  flex: 1;
}

.step-cost {
  font-family: monospace;
  font-size: 0.85em;
  color: #d03050;
}

.step-budget {
  font-family: monospace;
  font-size: 0.8em;
  opacity: 0.6;
}

.step-elapsed {
  font-family: monospace;
  font-size: 0.8em;
  opacity: 0.7;
  min-width: 3.5em;
  text-align: right;
}

.total-elapsed {
  margin-top: 8px;
  padding: 6px 0;
  font-family: monospace;
  font-size: 0.85em;
  opacity: 0.7;
  max-width: 80%;
}

.step-error-msg {
  font-size: 0.8em;
  opacity: 0.8;
  margin-left: auto;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.tool-calls-panel {
  border-top: none;
  padding: 0 0;
  max-width: 80%;
}

.tool-call {
  padding: 4px 0;
  border-bottom: 1px solid var(--moira-border, #e0e0e0);
}

.tool-name {
  font-weight: 600;
  font-family: monospace;
}

.tool-name.success {
  color: #18a058;
}

.tool-name.error {
  color: #d03050;
}

.tool-duration {
  margin-left: 8px;
  font-size: 0.85em;
  opacity: 0.6;
}

.tool-output {
  font-size: 0.85em;
  margin: 4px 0 0 0;
  max-height: 60px;
  overflow: hidden;
  white-space: pre-wrap;
}

.report-panel {
  padding: 16px 0;
  max-width: 80%;
}

.report-panel h3 {
  margin: 0 0 8px 0;
}

.report-answer {
  white-space: pre-wrap;
  line-height: 1.5;
}

.report-section {
  margin-top: 12px;
}

.report-section h4 {
  margin: 0 0 4px 0;
  font-size: 0.9em;
  opacity: 0.7;
}

.report-section ul {
  margin: 0;
  padding-left: 20px;
  font-size: 0.9em;
}

.citation-excerpt {
  display: block;
  opacity: 0.7;
  font-style: italic;
  font-size: 0.9em;
}

.unverified {
  color: #d03050;
}

.budget-consumed {
  margin-top: 8px;
  font-size: 0.85em;
  opacity: 0.6;
}

.run-error {
  padding: 8px 16px;
  color: #d03050;
  font-size: 0.9em;
  max-width: 80%;
}

.input-area {
  display: flex;
  gap: 8px;
  padding: 16px 40px;
  border-top: 1px solid var(--moira-border, #e0e0e0);
}
</style>
