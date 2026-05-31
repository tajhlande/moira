<script setup lang="ts">
import { ref } from "vue";
import { NCollapse, NCollapseItem, NIcon } from "naive-ui";
import { CircleCheck, CircleX, ChevronRight, ChevronDown } from "@vicons/tabler";
import type { WorkflowRunInfo, ExecutionStep } from "../api/client";
import StepDetailContent from "./StepDetailContent.vue";
import ReportPanel from "./ReportPanel.vue";
import "./workflow-artifacts.css";

const props = defineProps<{ run: WorkflowRunInfo }>();

const expandedSteps = ref<Set<number>>(new Set());

function toggleStep(index: number) {
  const next = new Set(expandedSteps.value);
  if (next.has(index)) {
    next.delete(index);
  } else {
    next.add(index);
  }
  expandedSteps.value = next;
}

function stepHasDetail(step: ExecutionStep): boolean {
  return !!step.detail && Object.keys(step.detail).length > 0;
}

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
      >
        <div :class="['step-row', step.status]">
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
          <button
            v-if="stepHasDetail(step)"
            class="step-toggle"
            @click="toggleStep(si)"
          >
            <NIcon :size="14">
              <ChevronDown v-if="expandedSteps.has(si)" />
              <ChevronRight v-else />
            </NIcon>
          </button>
          <span v-else class="step-toggle-placeholder" />
        </div>
        <div v-if="expandedSteps.has(si) && stepHasDetail(step)" class="step-detail">
          <StepDetailContent :detail="step.detail!" />
        </div>
      </div>
    </div>

    <NCollapse
      v-if="run.tool_executions.length > 0 && !run.execution_steps.some(s => s.detail?.tool_results?.length)"
      class="tool-calls-panel"
    >
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
