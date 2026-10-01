<script setup lang="ts">
// Material-class badge for citations (UI parity rule, retrieval-quality.md):
// "what did the agent actually have" must be visible wherever a source is
// shown, never inferred from character counts. `depth` is the serialized
// Citation.depth flag; legacy citations without it render no badge —
// unknown, not guessed.
import { computed } from "vue";

interface DepthInfo {
  label: string;
  kind: string;
  title: string;
}

const props = defineProps<{
  depth?: string | null;
  // True size of the fetched body (Citation.byte_size) — the serving window
  // the model saw is a small slice of this for full-class sources.
  size?: number | null;
}>();

// "page" is the pre-store vocabulary; "clipped" is its post-store rename.
// They share a rendering so the badge never changes meaning mid-upgrade.
const CLASSES: Record<string, DepthInfo> = {
  snippet: {
    label: "snippet",
    kind: "snippet",
    title: "Search-result excerpt only — the page was never fetched",
  },
  page: {
    label: "page",
    kind: "page",
    title: "Fetched page body, stored clipped to the serving window",
  },
  clipped: {
    label: "clipped",
    kind: "page",
    title: "Fetched page body, stored clipped to the serving window",
  },
  full: {
    label: "full page",
    kind: "full",
    title: "Complete page body stored — beyond what the context window serves",
  },
  summary: {
    label: "summary",
    kind: "summary",
    title: "Model-generated condensation of a parent source",
  },
};

const info = computed(() => CLASSES[props.depth ?? ""] ?? null);

// Full/clipped bodies may be far larger than the served window — make the
// actual stored size visible on hover instead of letting it be inferred.
const title = computed(() => {
  if (!info.value) return "";
  if (props.size == null) return info.value.title;
  return `${info.value.title} — ${props.size.toLocaleString()} chars stored`;
});
</script>

<template>
  <span
    v-if="info"
    :class="['source-depth-badge', info.kind]"
    :title="title"
    >{{ info.label }}</span
  >
</template>

<style scoped>
.source-depth-badge {
  display: inline-block;
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
  padding: 2px 6px;
  border-radius: 8px;
  white-space: nowrap;
  vertical-align: middle;
  background-color: var(--moira-sidebar-bg, #f0f0f0);
  color: var(--n-text-color-3, #999);
}

.source-depth-badge.snippet {
  background-color: var(--n-warning-color-suppl, #fff8e1);
  color: var(--n-warning-color, #f0a020);
}

.source-depth-badge.full {
  background-color: var(--n-success-color-suppl, #e8f5e9);
  color: var(--n-success-color, #18a058);
}

.source-depth-badge.summary {
  background-color: var(--n-primary-color-suppl, #e8f0ff);
  color: var(--n-primary-color, #2080f0);
}
</style>
