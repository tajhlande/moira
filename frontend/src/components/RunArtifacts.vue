<script setup lang="ts">
import { NCollapse, NCollapseItem, NIcon } from "naive-ui";
import { CircleCheck, CircleX } from "@vicons/tabler";
import type { WorkflowRunInfo } from "../api/client";
import ReportPanel from "./ReportPanel.vue";
import "./workflow-artifacts.css";

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
