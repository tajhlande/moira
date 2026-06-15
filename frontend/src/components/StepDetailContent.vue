<script setup lang="ts">
import { computed, ref } from "vue";
import { NCollapse, NCollapseItem } from "naive-ui";
import {
  IconChevronDown,
  IconChevronRight,
  IconCircleCheck,
  IconAlertCircle,
} from "@tabler/icons-vue";
import type { ToolExecution } from "../api/client";
import StructuredOutputRenderer from "./StructuredOutputRenderer.vue";
import { useToolsStore } from "../stores/tools";
import "./workflow-artifacts.css";

interface PromptMessage {
  role: string;
  content: string;
}

const props = defineProps<{ detail: Record<string, unknown> }>();
const toolsStore = useToolsStore();

const promptText = computed<string | null>(() => {
  const p = props.detail.prompt;
  if (typeof p === "string" && p.length > 0) return p;
  return null;
});

const promptMessages = computed<PromptMessage[]>(() => {
  const p = props.detail.prompt as { messages?: PromptMessage[] } | undefined;
  if (p && typeof p === "object" && "messages" in p) return p.messages ?? [];
  return [];
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

const candidateTools = computed<string[]>(() => {
  const v = props.detail.candidate_tools;
  return Array.isArray(v) ? v : [];
});

const queries = computed<{ fact_id: string; query: string }[]>(() => {
  const v = props.detail.queries;
  return Array.isArray(v) ? v : [];
});

const generationPath = computed<string | null>(() => {
  const v = props.detail.generation_path;
  return typeof v === "string" ? v : null;
});

const hasStructuredOutput = computed(() => {
  return so.value && Object.keys(so.value).length > 0;
});

const expandedToolResults = ref<Set<number>>(new Set());

function toggleToolResult(index: number) {
  const next = new Set(expandedToolResults.value);
  if (next.has(index)) {
    next.delete(index);
  } else {
    next.add(index);
  }
  expandedToolResults.value = next;
}

function kvPairs(obj: Record<string, unknown>): [string, string][] {
  return Object.entries(obj).map(([k, v]) => [
    k,
    typeof v === "string" || typeof v === "number" || typeof v === "boolean"
      ? String(v)
      : JSON.stringify(v),
  ]);
}
</script>

<template>
  <div class="step-detail-content">
    <!-- Prompt section (string) -->
    <NCollapse
      v-if="promptText"
      :default-expanded-names="[]"
      arrow-placement="right"
      class="detail-section"
    >
      <NCollapseItem title="Prompt" name="prompt">
        <pre class="detail-text-block">{{ promptText }}</pre>
      </NCollapseItem>
    </NCollapse>

    <!-- Prompt section (messages) -->
    <NCollapse
      v-if="promptMessages.length > 0"
      :default-expanded-names="[]"
      arrow-placement="right"
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
      arrow-placement="right"
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
      arrow-placement="right"
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
      arrow-placement="right"
      class="detail-section"
    >
      <NCollapseItem title="Structured Output" name="structured">
        <StructuredOutputRenderer v-if="so" :so="so" />
      </NCollapseItem>
    </NCollapse>

    <!-- Tool identification detail -->
    <div v-if="candidateTools.length > 0 || queries.length > 0" class="detail-section">
      <div v-if="candidateTools.length > 0" class="structured-section">
        <div class="detail-label">Candidate Tools</div>
        <div class="tool-tags">
          <span
            v-for="name in candidateTools"
            :key="name"
            :class="['tool-tag', toolsStore.defaultToolNames.includes(name) ? 'default' : 'discovered']"
          >{{ name }}</span>
        </div>
      </div>
      <div v-if="queries.length > 0" class="structured-section">
        <div class="detail-label">Fact Queries</div>
        <ul>
          <li v-for="(q, qi) in queries" :key="qi">
            <strong>{{ q.fact_id }}</strong>: {{ q.query }}
          </li>
        </ul>
      </div>
    </div>

    <!-- Report generation path -->
    <div v-if="generationPath" class="detail-section">
      <div class="structured-section">
        <div class="detail-label">Generation Path</div>
        <span :class="['generation-path-badge', generationPath]">{{ generationPath }}</span>
      </div>
    </div>

    <!-- Tool executions -->
    <NCollapse
      v-if="toolResults.length > 0"
      :default-expanded-names="[]"
      arrow-placement="right"
      class="detail-section"
    >
      <NCollapseItem
        :title="'Tool Executions (' + toolResults.length + ')'"
        name="tool-executions"
      >
        <div class="tool-results-scroll">
          <div
            v-for="(tr, tri) in toolResults"
            :key="tri"
            class="step-tool-result"
          >
            <div class="step-tool-result-header">
              <IconCircleCheck
                v-if="tr.success"
                :size="14"
                class="tool-status-icon success"
              />
              <IconAlertCircle
                v-else
                :size="14"
                class="tool-status-icon error"
              />
              <span :class="['tool-name', tr.success ? 'success' : 'error']">{{
                tr.tool
              }}</span>
              <span class="tool-duration">{{ tr.duration_ms }}ms</span>
              <button
                class="tool-result-toggle"
                @click="toggleToolResult(tri)"
              >
                <IconChevronDown
                  v-if="expandedToolResults.has(tri)"
                  :size="18"
                />
                <IconChevronRight v-else :size="18" />
              </button>
            </div>
            <div
              v-if="expandedToolResults.has(tri)"
              class="step-tool-result-body"
            >
              <div v-if="tr.args && Object.keys(tr.args).length > 0" class="so-kv-list">
                <template v-for="([kvKey, kvVal], kvi) in kvPairs(tr.args)" :key="kvi">
                  <span class="so-kv-key">{{ kvKey }}</span>
                  <span class="so-kv-val">{{ kvVal }}</span>
                </template>
              </div>
              <div class="so-kv-list">
                <span class="so-kv-key">Output</span>
                <pre class="so-kv-val tool-output-full">{{ tr.result }}</pre>
              </div>
            </div>
          </div>
        </div>
      </NCollapseItem>
    </NCollapse>
  </div>
</template>
