<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { NButton } from "naive-ui";
import {
  IconCircleCheck,
  IconCircleX,
  IconLoader,
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

const nowMs = ref(Date.now());
let clockInterval: ReturnType<typeof setInterval> | null = null;

onMounted(() => {
  clockInterval = setInterval(() => {
    nowMs.value = Date.now();
  }, 1000);
});

onUnmounted(() => {
  if (clockInterval) {
    clearInterval(clockInterval);
  }
});

const hasRunningStep = computed(() =>
  props.run.execution_steps.some((s) => s.status === "running"),
);

type TimelineRow =
  | { kind: "step"; key: string; step: ExecutionStep; stepIndex: number }
  | { kind: "boundary"; key: string; label: string };

function stepRunId(step: ExecutionStep): string {
  return step.detail_run_id || props.run.id;
}

function boundaryLabel(previousRunId: string): string {
  const attempts = props.run.attempts || [];
  const previousAttempt = attempts.find((attempt) => attempt.run_id === previousRunId);
  if (!previousAttempt) return "Resumed";
  if (previousAttempt.status === "error") return "Restarted after error";
  return "Resumed";
}

const timelineRows = computed<TimelineRow[]>(() => {
  const rows: TimelineRow[] = [];
  let previousRunId: string | null = null;

  for (let idx = 0; idx < props.run.execution_steps.length; idx += 1) {
    const step = props.run.execution_steps[idx];
    if (!step) continue;
    const currentRunId = stepRunId(step);
    if (previousRunId && currentRunId !== previousRunId) {
      rows.push({
        kind: "boundary",
        key: `boundary:${previousRunId}:${currentRunId}:${idx}`,
        label: boundaryLabel(previousRunId),
      });
    }
    rows.push({
      kind: "step",
      key: `step:${currentRunId}:${step.id || idx}`,
      step,
      stepIndex: idx,
    });
    previousRunId = currentRunId;
  }

  return rows;
});

function toggleStep(index: number) {
  const next = new Set(expandedSteps.value);
  if (next.has(index)) {
    next.delete(index);
  } else {
    next.add(index);
  }
  expandedSteps.value = next;

  if (next.has(index)) {
    const step = props.run.execution_steps[index];
    if (step) {
      void ensureStepDetailLoaded(step);
    }
  }
}

function stepHasDetail(step: ExecutionStep): boolean {
  if (step.detail && Object.keys(step.detail).length > 0) return true;
  return step.has_detail === true;
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
  return so?.route === "retry_research" || so?.route === "retry_synthesis";
}

function hasStoppedStep(): boolean {
  return props.run.execution_steps.some((s) => s.status === "stopped");
}

function needsStopMarker(): boolean {
  return props.run.status === "stopped" && !hasStoppedStep();
}

function stepIsLoading(step: ExecutionStep): boolean {
  const stepId = step.id;
  const stepVersion = step.step_version;
  const detailRunId = stepRunId(step);
  if (!stepId || typeof stepVersion !== "number") return false;
  return store.isStepDetailLoading(detailRunId, stepId, stepVersion);
}

function resolvedStepDetail(step: ExecutionStep): Record<string, unknown> | null {
  if (step.detail && Object.keys(step.detail).length > 0) return step.detail;
  const stepId = step.id;
  const stepVersion = step.step_version;
  const detailRunId = stepRunId(step);
  if (!stepId || typeof stepVersion !== "number") return null;
  const detail = store.getStepDetail(detailRunId, stepId, stepVersion);
  return detail?.detail ?? null;
}

async function ensureStepDetailLoaded(step: ExecutionStep): Promise<void> {
  if (step.detail && Object.keys(step.detail).length > 0) return;
  if (!step.has_detail) return;
  const detailRunId = stepRunId(step);
  if (!props.run.id || !step.id || typeof step.step_version !== "number") {
    return;
  }
  await store.loadStepDetail(detailRunId, step.id, step.step_version);
}

function liveElapsedMs(step: ExecutionStep): number | undefined {
  if (step.status !== "running") return step.elapsed_ms;
  if (!step.started_at) return step.elapsed_ms;
  const started = new Date(step.started_at).getTime();
  return nowMs.value - started;
}

function formatElapsed(ms: number | undefined): string {
  if (ms === undefined || ms === null) return "";
  const totalSec = Math.floor(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

function fmt(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
</script>

<template>
  <div>
    <div
      v-if="run.execution_steps.length > 0 || needsStopMarker()"
      class="steps-and-resume-wrapper"
    >
      <div class="initial-budget" v-if="run.budget_limit">
        Budget: {{ run.budget_limit }}
      </div>
      <div class="steps-container">
        <div v-for="row in timelineRows" :key="row.key">
          <div v-if="row.kind === 'boundary'" class="attempt-boundary-row">
            <span class="attempt-boundary-label">{{ row.label }}</span>
            <span class="attempt-boundary-line" />
          </div>
          <template v-else>
            <div :class="['step-row', row.step.status]">
              <IconRestore v-if="isRetryBranch(row.step)" :size="16" class="retry-branch-icon" />
              <IconLoader v-else-if="row.step.status === 'running'" :size="16" class="spinning" />
              <IconHandStop v-else-if="row.step.status === 'stopped'" :size="16" class="step-stopped-icon" />
              <IconCircleCheck v-else-if="row.step.status === 'completed'" :size="16" class="step-completed-icon" />
              <IconCircleX v-else :size="16" class="step-error-icon" />
              <span class="step-label">{{ row.step.label }}</span>
              <span v-if="toolCallCount(row.step) > 0" class="step-tool-indicators">
                <template v-if="toolCallCount(row.step) <= 10">
                  <IconTool
                    v-for="ti in toolCallCount(row.step)"
                    :key="ti"
                    :size="13"
                    class="tool-indicator-icon"
                  />
                </template>
                <template v-else>
                  <IconTool :size="13" class="tool-indicator-icon" />
                  <span class="tool-indicator-count"
                    >&times;{{ toolCallCount(row.step) }}</span
                  >
                </template>
              </span>
              <span v-if="row.step.status === 'completed'" class="step-cost"
                >-{{ Math.abs(row.step.cost) }}</span
              >
              <span v-else-if="row.step.elapsed_ms != null || row.step.status === 'running'" class="step-cost step-cost-placeholder"
              ></span>
              <span v-if="row.step.elapsed_ms != null || row.step.status === 'running'" class="step-elapsed">{{
                formatElapsed(liveElapsedMs(row.step))
              }}</span>
              <span v-if="row.step.status === 'completed' || row.step.status === 'running'" class="step-budget"
                >{{ row.step.budget_remaining }} remaining</span
              >
              <span v-else-if="row.step.elapsed_ms != null" class="step-budget step-budget-placeholder"
              ></span>
              <span
                v-if="row.step.status === 'error' && row.step.error"
                class="step-error-msg"
                >{{ row.step.error }}</span
              >
              <button
                v-if="stepHasDetail(row.step)"
                class="step-toggle"
                @click="toggleStep(row.stepIndex)"
              >
                <IconChevronDown v-if="expandedSteps.has(row.stepIndex)" :size="18" />
                <IconChevronRight v-else :size="18" />
              </button>
              <span v-else class="step-toggle-placeholder" />
            </div>
            <div
              v-if="expandedSteps.has(row.stepIndex) && stepHasDetail(row.step)"
              class="step-detail"
            >
              <div v-if="stepIsLoading(row.step)" class="step-detail-loading">
                <IconLoader :size="14" class="spinning" />
                <span>Loading step details...</span>
              </div>
              <StepDetailContent
                v-else-if="resolvedStepDetail(row.step)"
                :detail="resolvedStepDetail(row.step)!"
              />
            </div>
          </template>
        </div>
        <div v-if="run.status === 'running' && !hasRunningStep" class="step-row running">
          <IconLoader :size="16" class="spinning" />
          <span class="step-label">Starting...</span>
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
      Time: {{ formatElapsed(run.total_elapsed_ms) }}
      <span v-if="(run.input_tokens ?? 0) > 0" class="token-stats">
        &middot; Tokens: 
        {{ fmt(run.input_tokens ?? 0) }} in /
        {{ fmt(run.output_tokens ?? 0) }} out
        <template v-if="(run.thinking_tokens ?? 0) > 0">
          / {{ fmt(run.thinking_tokens ?? 0) }} thinking
        </template>
      </span>
    </div>

    <div v-if="run.error && run.status === 'error'" class="run-error">
      Run failed: {{ run.error }}
    </div>
  </div>
</template>
