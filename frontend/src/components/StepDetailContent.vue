<script setup lang="ts">
import { computed } from "vue";
import { NCollapse, NCollapseItem } from "naive-ui";
import type { ToolExecution } from "../api/client";
import { useToolsStore } from "../stores/tools";
import "./workflow-artifacts.css";

interface PromptMessage {
  role: string;
  content: string;
}

interface ToolCallEntry {
  tool: string;
  args: Record<string, unknown>;
}

const props = defineProps<{ detail: Record<string, unknown> }>();
const toolsStore = useToolsStore();

const promptMessages = computed<PromptMessage[]>(() => {
  const p = props.detail.prompt as { messages?: PromptMessage[] } | undefined;
  return p?.messages ?? [];
});

const thinking = computed<string | null>(() => {
  const v = props.detail.thinking;
  return v ? String(v) : null;
});

const response = computed<string | null>(() => {
  const v = props.detail.response;
  return v ? String(v) : null;
});

const so = computed<Record<string, unknown> | null>(() => {
  const v = props.detail.structured_output;
  if (v && typeof v === "object") return v as Record<string, unknown>;
  return null;
});

const toolResults = computed<ToolExecution[]>(() => {
  if (!props.detail.tool_results) return [];
  return props.detail.tool_results as ToolExecution[];
});

const toolCallsFromOutput = computed<ToolCallEntry[]>(() => {
  if (!so.value?.tool_calls) return [];
  return so.value.tool_calls as ToolCallEntry[];
});

const toolListKeys = computed<string[]>(() => {
  if (!so.value) return [];
  return Object.keys(so.value).filter(
    (k) =>
      k === "selected_tools" ||
      k === "default_tools" ||
      k === "discovered_tools",
  );
});

const isVerification = computed(() => {
  return (
    so.value &&
    "outcome" in so.value &&
    "case" in so.value &&
    "assessment" in so.value
  );
});

const isReport = computed(() => {
  return so.value && "answer" in so.value && "citations" in so.value;
});

const hasStructuredOutput = computed(() => {
  return so.value && Object.keys(so.value).length > 0;
});

function getToolNames(key: string): string[] {
  return (so.value?.[key] as string[]) ?? [];
}

function prettyLabel(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function claimList(key: string): unknown[] {
  const arr = so.value?.[key];
  return Array.isArray(arr) ? arr : [];
}
</script>

<template>
  <div class="step-detail-content">
    <!-- Prompt section -->
    <NCollapse
      v-if="promptMessages.length > 0"
      :default-expanded-names="[]"
      class="detail-section"
    >
      <NCollapseItem title="Prompt" name="prompt">
        <div
          v-for="(msg, mi) in promptMessages"
          :key="mi"
          class="detail-message"
        >
          <div class="detail-message-role">{{ msg.role }}</div>
          <pre class="detail-message-content">{{ msg.content }}</pre>
        </div>
      </NCollapseItem>
    </NCollapse>

    <!-- Thinking section -->
    <NCollapse
      v-if="thinking"
      :default-expanded-names="[]"
      class="detail-section"
    >
      <NCollapseItem title="Thinking" name="thinking">
        <pre class="detail-text-block">{{ thinking }}</pre>
      </NCollapseItem>
    </NCollapse>

    <!-- Response section -->
    <NCollapse
      v-if="response"
      :default-expanded-names="[]"
      class="detail-section"
    >
      <NCollapseItem title="Response" name="response">
        <pre class="detail-text-block">{{ response }}</pre>
      </NCollapseItem>
    </NCollapse>

    <!-- Structured output section -->
    <NCollapse
      v-if="hasStructuredOutput"
      :default-expanded-names="[]"
      class="detail-section"
    >
      <NCollapseItem title="Structured Output" name="structured">
        <!-- Tool list pills -->
        <div v-for="key in toolListKeys" :key="key" class="structured-section">
          <div class="detail-label">{{ prettyLabel(key) }}</div>
          <div class="tool-tags">
            <span
              v-for="name in getToolNames(key)"
              :key="name"
              :class="[
                'tool-tag',
                toolsStore.defaultToolNames.includes(name)
                  ? 'default'
                  : 'discovered',
              ]"
            >
              {{ name }}
            </span>
            <span v-if="getToolNames(key).length === 0" class="tool-tag none"
              >None</span
            >
          </div>
        </div>

        <!-- Tool calls list -->
        <!-- (moved to dedicated section below) -->

        <!-- Verification report -->
        <div v-if="isVerification" class="structured-section">
          <div class="verification-summary">
            <span :class="['verification-outcome', String(so!.outcome)]">
              {{ so!.outcome }}
            </span>
            <span v-if="so!.case" class="verification-case"
              >Case {{ so!.case }}</span
            >
          </div>
          <div v-if="so!.retry_declined" class="retry-declined-note">
            {{ so!.retry_declined_reason }}
          </div>
          <div v-if="so!.assessment" class="verification-assessment">
            {{ so!.assessment }}
          </div>
          <div
            v-if="claimList('supported_claims').length"
            class="verification-claims"
          >
            <div class="detail-label">Supported Claims</div>
            <ul>
              <li v-for="(c, ci) in claimList('supported_claims')" :key="ci">
                {{ c }}
              </li>
            </ul>
          </div>
          <div
            v-if="claimList('unsupported_claims').length"
            class="verification-claims"
          >
            <div class="detail-label">Unsupported Claims</div>
            <ul>
              <li v-for="(c, ci) in claimList('unsupported_claims')" :key="ci">
                {{ c }}
              </li>
            </ul>
          </div>
          <div
            v-if="claimList('contradictions').length"
            class="verification-claims"
          >
            <div class="detail-label">Contradictions</div>
            <ul>
              <li v-for="(c, ci) in claimList('contradictions')" :key="ci">
                {{ c }}
              </li>
            </ul>
          </div>
        </div>

        <!-- Report output -->
        <div v-if="isReport" class="structured-section">
          <div class="detail-label">Answer Preview</div>
          <pre class="detail-text-block">{{ so!.answer }}</pre>
        </div>
      </NCollapseItem>
    </NCollapse>

    <!-- Tool calls (from structured_output) -->
    <NCollapse
      v-if="toolCallsFromOutput.length > 0"
      :default-expanded-names="['tool-calls']"
      class="detail-section"
    >
      <NCollapseItem
        :title="'Tool Calls (' + toolCallsFromOutput.length + ')'"
        name="tool-calls"
      >
        <div class="tool-results-scroll">
          <div
            v-for="(tc, tci) in toolCallsFromOutput"
            :key="tci"
            class="step-tool-result"
          >
            <span class="tool-name success">{{ tc.tool }}</span>
            <pre class="tool-output-full">{{
              JSON.stringify(tc.args, null, 2)
            }}</pre>
          </div>
        </div>
      </NCollapseItem>
    </NCollapse>

    <!-- Tool results (from run_manager attachment) -->
    <NCollapse
      v-if="toolResults.length > 0"
      :default-expanded-names="['tool-results']"
      class="detail-section"
    >
      <NCollapseItem
        :title="'Tool Executions (' + toolResults.length + ')'"
        name="tool-results"
      >
        <div class="tool-results-scroll">
          <div
            v-for="(tr, tri) in toolResults"
            :key="tri"
            class="step-tool-result"
          >
            <span :class="['tool-name', tr.success ? 'success' : 'error']">{{
              tr.tool
            }}</span>
            <span class="tool-duration">{{ tr.duration_ms }}ms</span>
            <pre class="tool-output-full">{{ tr.result }}</pre>
          </div>
        </div>
      </NCollapseItem>
    </NCollapse>
  </div>
</template>
