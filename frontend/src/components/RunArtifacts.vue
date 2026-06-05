<script setup lang="ts">
import { ref } from "vue";
import { NButton } from "naive-ui";
import {
  IconCircleCheck,
  IconCircleX,
  IconChevronRight,
  IconChevronDown,
  IconTool,
  IconRestore,
  IconHandStop,
} from "@tabler/icons-vue";
import type { WorkflowRunInfo, ExecutionStep } from "../api/client";
import { useChatStore } from "../stores/chat";
import StepDetailContent from "./StepDetailContent.vue";
import ReportPanel from "./ReportPanel.vue";
import "./workflow-artifacts.css";

const props = defineProps<{ run: WorkflowRunInfo }>();
const store = useChatStore();
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

function toolCallCount(step: ExecutionStep): number {
  const tr = step.detail?.tool_results;
  if (Array.isArray(tr)) return tr.length;
  if (typeof step.tool_call_count === "number") return step.tool_call_count;
  return 0;
}

function isRetryBranch(step: ExecutionStep): boolean {
  if (step.node !== "verification" || step.status !== "completed") return false;
  const so = step.detail?.structured_output as
    | Record<string, unknown>
    | undefined;
  return so?.outcome === "retry_plan" || so?.outcome === "retry_draft";
}

function hasStoppedStep(): boolean {
  return props.run.execution_steps.some((s) => s.status === "stopped");
}

function needsStopMarker(): boolean {
  return props.run.status === "stopped" && !hasStoppedStep();
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
    <div
      v-if="run.execution_steps.length > 0 || needsStopMarker()"
      class="steps-and-resume-wrapper"
    >
      <div class="steps-container">
        <div v-for="(step, si) in run.execution_steps" :key="'rs-' + si">
          <div :class="['step-row', step.status]">
            <IconRestore v-if="isRetryBranch(step)" :size="16" class="retry-branch-icon" />
            <IconHandStop v-else-if="step.status === 'stopped'" :size="16" class="step-stopped-icon" />
            <IconCircleCheck v-else-if="step.status === 'completed'" :size="16" class="step-completed-icon" />
            <IconCircleX v-else :size="16" class="step-error-icon" />
            <span class="step-label">{{ step.label }}</span>
            <span v-if="toolCallCount(step) > 0" class="step-tool-indicators">
              <template v-if="toolCallCount(step) <= 10">
                <IconTool
                  v-for="ti in toolCallCount(step)"
                  :key="ti"
                  :size="13"
                  class="tool-indicator-icon"
                />
              </template>
              <template v-else>
                <IconTool :size="13" class="tool-indicator-icon" />
                <span class="tool-indicator-count"
                  >&times;{{ toolCallCount(step) }}</span
                >
              </template>
            </span>
            <span v-if="step.status === 'completed'" class="step-cost"
              >-{{ step.cost }}</span
            >
            <span v-if="step.elapsed_ms != null" class="step-elapsed">{{
              formatElapsed(step.elapsed_ms)
            }}</span>
            <span v-if="step.status === 'completed'" class="step-budget"
              >{{ step.budget_remaining }} remaining</span
            >
            <span
              v-if="step.status === 'error' && step.error"
              class="step-error-msg"
              >{{ step.error }}</span
            >
            <button
              v-if="stepHasDetail(step)"
              class="step-toggle"
              @click="toggleStep(si)"
            >
              <IconChevronDown v-if="expandedSteps.has(si)" :size="14" />
              <IconChevronRight v-else :size="14" />
            </button>
            <span v-else class="step-toggle-placeholder" />
          </div>
          <div
            v-if="expandedSteps.has(si) && stepHasDetail(step)"
            class="step-detail"
          >
            <StepDetailContent :detail="step.detail!" />
          </div>
        </div>
        <div v-if="needsStopMarker()" class="stop-marker-row">
          <IconHandStop :size="16" class="step-stopped-icon" />
          <span class="stop-marker-label">Stopped</span>
          <span class="stop-marker-line" />
        </div>
      </div>
      <NButton
        v-if="run.status === 'stopped' || run.status === 'error'"
        type="primary"
        ghost
        size="small"
        class="resume-button"
        @click="store.resumeRun()"
      >
        <template #icon>
          <IconRestore :size="16" />
        </template>
        {{ run.status === 'error' ? 'Retry' : 'Resume' }}
      </NButton>
    </div>

    <ReportPanel v-if="run.report" :report="run.report" />

    <div v-if="run.total_elapsed_ms != null" class="total-elapsed">
      Total: {{ formatElapsed(run.total_elapsed_ms) }}
    </div>

    <div v-if="run.error && run.status === 'error'" class="run-error">
      Run failed: {{ run.error }}
    </div>
  </div>
</template>
