<script setup lang="ts">
import { NCollapse, NCollapseItem, NIcon } from "naive-ui";
import { CircleCheck, CircleX } from "@vicons/tabler";
import type { WorkflowRunInfo } from "../api/client";
import ReportPanel from "./ReportPanel.vue";

defineProps<{ run: WorkflowRunInfo }>();

function formatElapsed(ms: number | undefined): string {
  if (ms === undefined || ms === null) return "";
  const totalSec = Math.floor(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${sec.toString().padStart(2, "0")}`;
}
</script>

<template>
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
</template>

<style scoped>
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

.step-error-msg {
  font-size: 0.8em;
  opacity: 0.8;
  margin-left: auto;
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

.total-elapsed {
  margin-top: 8px;
  padding: 6px 0;
  font-family: monospace;
  font-size: 0.85em;
  opacity: 0.7;
  max-width: 80%;
}

.run-error {
  padding: 8px 16px;
  color: #d03050;
  font-size: 0.9em;
  max-width: 80%;
}
</style>
