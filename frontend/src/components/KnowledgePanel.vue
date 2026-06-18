<script setup lang="ts">
import { computed, ref } from "vue";
import { NCollapse, NCollapseItem, NText } from "naive-ui";
import {
  IconChevronDown,
  IconChevronRight,
  IconDatabase,
} from "@tabler/icons-vue";
import type {
  KnowledgeSummary,
  FactRecord,
  ConclusionRecord,
  CitationRecord,
} from "../api/client";

const props = defineProps<{
  knowledge: KnowledgeSummary;
}>();

const expanded = ref(false);

// Flatten facts from subject-grouped dict into a single array
const allFacts = computed<FactRecord[]>(() => {
  const grouped = props.knowledge.facts;
  if (!grouped || typeof grouped !== "object") return [];
  return Object.values(grouped).flat();
});

const allConclusions = computed<ConclusionRecord[]>(() => {
  const grouped = props.knowledge.conclusions;
  if (!grouped || typeof grouped !== "object") return [];
  return Object.values(grouped).flat();
});

// Status counts for the summary strip
const factCounts = computed(() => {
  const counts = { verified: 0, unverified: 0, contradicted: 0, unknown: 0 };
  for (const f of allFacts.value) {
    const s = f.status as keyof typeof counts;
    if (s in counts) counts[s]++;
  }
  return counts;
});

const conclusionCounts = computed(() => {
  const counts = { verified: 0, unverified: 0, contradicted: 0 };
  for (const c of allConclusions.value) {
    const s = c.status as keyof typeof counts;
    if (s in counts) counts[s]++;
  }
  return counts;
});

const citationCount = computed(
  () => props.knowledge.citations?.length ?? 0,
);

const hasContent = computed(
  () =>
    allFacts.value.length > 0 ||
    allConclusions.value.length > 0 ||
    citationCount.value > 0,
);

// Subject groups for display
const subjectGroups = computed(() => {
  const grouped = props.knowledge.facts;
  if (!grouped || typeof grouped !== "object") return [];
  return Object.entries(grouped).map(([subject, facts]) => ({ subject, facts }));
});

// Conclusion status groups
const conclusionGroups = computed(() => {
  const grouped = props.knowledge.conclusions;
  if (!grouped || typeof grouped !== "object") return [];
  return Object.entries(grouped).map(([status, conclusions]) => ({
    status,
    conclusions,
  }));
});

// Status filter
type StatusFilter = "all" | "verified" | "unverified" | "contradicted" | "unknown";
const factFilter = ref<StatusFilter>("all");

const filteredSubjectGroups = computed(() => {
  if (factFilter.value === "all") return subjectGroups.value;
  return subjectGroups.value
    .map((g) => ({
      subject: g.subject,
      facts: g.facts.filter((f) => f.status === factFilter.value),
    }))
    .filter((g) => g.facts.length > 0);
});

const filterOptions: { label: string; value: StatusFilter }[] = [
  { label: "All", value: "all" },
  { label: "Verified", value: "verified" },
  { label: "Unverified", value: "unverified" },
  { label: "Contradicted", value: "contradicted" },
  { label: "Unknown", value: "unknown" },
];

function statusClass(status: string): string {
  if (status === "verified") return "verified";
  if (status === "contradicted") return "contradicted";
  if (status === "unknown") return "unknown";
  return "unverified";
}

function toggleExpand() {
  expanded.value = !expanded.value;
}
</script>

<template>
  <div class="knowledge-panel">
    <!-- Summary strip — always visible, sticky -->
    <div class="knowledge-summary-strip" @click="toggleExpand">
      <div class="summary-left">
        <IconDatabase :size="16" class="summary-icon" />
        <span v-if="knowledge.topic" class="summary-topic">{{ knowledge.topic }}</span>
        <div v-if="knowledge.entities?.length" class="summary-entities">
          <span
            v-for="e in knowledge.entities.slice(0, 4)"
            :key="e"
            class="entity-pill"
          >{{ e }}</span>
          <span v-if="knowledge.entities.length > 4" class="entity-pill more">
            +{{ knowledge.entities.length - 4 }}
          </span>
        </div>
      </div>
      <div class="summary-right">
        <div class="status-counts">
          <span class="count-group">
            <span class="count-label">Facts:</span>
            <span v-if="factCounts.verified" class="count-badge verified">
              {{ factCounts.verified }} verified
            </span>
            <span v-if="factCounts.unverified" class="count-badge unverified">
              {{ factCounts.unverified }} unverified
            </span>
            <span v-if="factCounts.contradicted" class="count-badge contradicted">
              {{ factCounts.contradicted }} contradicted
            </span>
            <span v-if="factCounts.unknown" class="count-badge unknown">
              {{ factCounts.unknown }} unknown
            </span>
          </span>
          <span v-if="allConclusions.length" class="count-group">
            <span class="count-label">Conclusions:</span>
            <span v-if="conclusionCounts.verified" class="count-badge verified">
              {{ conclusionCounts.verified }} verified
            </span>
            <span v-if="conclusionCounts.unverified" class="count-badge unverified">
              {{ conclusionCounts.unverified }} unverified
            </span>
            <span v-if="conclusionCounts.contradicted" class="count-badge contradicted">
              {{ conclusionCounts.contradicted }} contradicted
            </span>
          </span>
          <span v-if="citationCount" class="count-group">
            <span class="count-badge neutral">{{ citationCount }} sources</span>
          </span>
        </div>
        <component :is="expanded ? IconChevronDown : IconChevronRight" :size="18" class="expand-icon" />
      </div>
    </div>

    <!-- Expandable body -->
    <div v-if="expanded && hasContent" class="knowledge-body">
      <!-- Analysis section -->
      <div v-if="knowledge.user_goal || knowledge.concepts?.length" class="knowledge-section">
        <h4 class="section-title">Analysis</h4>
        <div v-if="knowledge.user_goal" class="analysis-field">
          <span class="field-label">Goal:</span>
          <span>{{ knowledge.user_goal }}</span>
        </div>
        <div v-if="knowledge.concepts?.length" class="analysis-field">
          <span class="field-label">Concepts:</span>
          <div class="pill-row">
            <span v-for="c in knowledge.concepts" :key="c" class="entity-pill">{{ c }}</span>
          </div>
        </div>
      </div>

      <!-- Facts section -->
      <div v-if="allFacts.length" class="knowledge-section">
        <div class="section-header-row">
          <h4 class="section-title">Facts ({{ allFacts.length }})</h4>
          <div class="filter-row">
            <button
              v-for="opt in filterOptions"
              :key="opt.value"
              :class="['filter-btn', { active: factFilter === opt.value }]"
              @click="factFilter = opt.value"
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
        <div class="facts-by-subject">
          <div
            v-for="group in filteredSubjectGroups"
            :key="group.subject"
            class="subject-group"
          >
            <div class="subject-label">{{ group.subject }}</div>
            <div class="subject-facts">
              <div v-for="f in group.facts" :key="f.id" class="fact-card">
                <div class="fact-header">
                  <span :class="['fact-status', statusClass(f.status)]">{{ f.status }}</span>
                  <span class="fact-id">{{ f.id }}</span>
                </div>
                <div class="fact-needed">{{ f.fact_needed }}</div>
                <div v-if="f.claim" class="fact-claim">{{ f.claim }}</div>
                <div v-if="f.relation || f.value" class="fact-structured">
                  <span v-if="f.relation" class="structured-field">{{ f.relation }}</span>
                  <span v-if="f.value" class="structured-value">{{ f.value }}</span>
                </div>
                <div v-if="f.verification_note" class="fact-note">{{ f.verification_note }}</div>
                <div v-if="f.citation_ids?.length" class="fact-citations">
                  <span
                    v-for="cid in f.citation_ids"
                    :key="cid"
                    class="citation-ref"
                  >{{ cid }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Conclusions section -->
      <div v-if="allConclusions.length" class="knowledge-section">
        <h4 class="section-title">Conclusions ({{ allConclusions.length }})</h4>
        <div class="conclusions-list">
          <div v-for="c in allConclusions" :key="c.id" class="conclusion-card">
            <div class="fact-header">
              <span :class="['fact-status', statusClass(c.status)]">{{ c.status }}</span>
              <span class="fact-id">{{ c.id }}</span>
            </div>
            <div class="conclusion-text">{{ c.conclusion }}</div>
            <div v-if="c.supporting_fact_ids?.length" class="supporting-facts">
              <span class="field-label">Supports:</span>
              <span
                v-for="fid in c.supporting_fact_ids"
                :key="fid"
                class="fact-ref"
              >{{ fid }}</span>
            </div>
            <div v-if="c.reasoning" class="conclusion-reasoning">{{ c.reasoning }}</div>
          </div>
        </div>
      </div>

      <!-- Sources section -->
      <div v-if="citationCount" class="knowledge-section">
        <h4 class="section-title">Sources ({{ citationCount }})</h4>
        <ol class="sources-list">
          <li v-for="(cit, ci) in knowledge.citations" :key="cit.id || ci" class="source-item">
            <span class="source-name">{{ cit.source }}</span>
            <a v-if="cit.url" :href="cit.url" target="_blank" rel="noopener" class="source-url">{{ cit.url }}</a>
            <span v-if="cit.title" class="source-title">{{ cit.title }}</span>
            <span v-if="cit.id" class="source-id">{{ cit.id }}</span>
          </li>
        </ol>
      </div>
    </div>
  </div>
</template>

<style scoped>
.knowledge-panel {
  margin-top: 8px;
  margin-bottom: 8px;
  max-width: 80%;
  margin-left: 16px;
  border: 1px solid var(--moira-border, #e0e0e0);
  border-radius: 8px;
  background-color: var(--moira-sidebar-bg, #f9f9f9);
  overflow: hidden;
}

/* Summary strip — sticky */
.knowledge-summary-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  cursor: pointer;
  background-color: var(--moira-sidebar-bg, #f9f9f9);
  position: sticky;
  top: 0;
  z-index: 10;
  transition: background-color 150ms ease;
}

.knowledge-summary-strip:hover {
  background-color: var(--moira-border, #f0f0f0);
}

.summary-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-shrink: 1;
}

.summary-icon {
  color: var(--n-text-color-3, #999);
  flex-shrink: 0;
}

.summary-topic {
  font-weight: 600;
  font-size: 0.9em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.summary-entities {
  display: flex;
  gap: 4px;
  flex-shrink: 1;
  overflow: hidden;
}

.entity-pill {
  font-size: 0.75em;
  padding: 1px 6px;
  border-radius: 3px;
  background-color: var(--n-primary-color-suppl, #e8f0ff);
  color: var(--n-primary-color, #2080f0);
  white-space: nowrap;
}

.entity-pill.more {
  background-color: var(--moira-border, #e0e0e0);
  color: var(--n-text-color-3, #999);
}

.summary-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.status-counts {
  display: flex;
  align-items: center;
  gap: 12px;
}

.count-group {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.8em;
}

.count-label {
  font-weight: 600;
  opacity: 0.7;
}

.count-badge {
  font-size: 0.85em;
  padding: 1px 6px;
  border-radius: 3px;
  font-weight: 500;
  white-space: nowrap;
}

.count-badge.verified {
  background-color: var(--n-success-color-suppl, #e8f5e9);
  color: var(--n-success-color, #18a058);
}

.count-badge.unverified {
  background-color: var(--n-warning-color-suppl, #fff8e1);
  color: var(--n-warning-color, #f0a020);
}

.count-badge.contradicted {
  background-color: var(--n-error-color-suppl, #fbe9e7);
  color: var(--n-error-color, #d03050);
}

.count-badge.unknown {
  background-color: var(--moira-border, #eee);
  color: var(--n-text-color-3, #999);
}

.count-badge.neutral {
  background-color: var(--moira-sidebar-bg, #f0f0f0);
  color: var(--n-text-color-2, #666);
}

.expand-icon {
  color: var(--n-text-color-3, #999);
  flex-shrink: 0;
}

/* Expandable body */
.knowledge-body {
  padding: 4px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.knowledge-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.section-title {
  font-size: 0.85em;
  font-weight: 600;
  margin: 0;
  padding-bottom: 2px;
  border-bottom: 1px solid var(--moira-border, #e0e0e0);
}

.section-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

/* Analysis section */
.analysis-field {
  font-size: 0.85em;
  display: flex;
  gap: 6px;
  align-items: baseline;
}

.field-label {
  font-weight: 600;
  opacity: 0.7;
  white-space: nowrap;
}

.pill-row {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

/* Facts section */
.filter-row {
  display: flex;
  gap: 2px;
}

.filter-btn {
  font-size: 0.75em;
  padding: 2px 8px;
  border: 1px solid transparent;
  border-radius: 3px;
  background: none;
  cursor: pointer;
  color: var(--n-text-color-3, #999);
  transition: all 150ms ease;
}

.filter-btn:hover {
  color: var(--n-text-color, #333);
}

.filter-btn.active {
  background-color: var(--n-primary-color-suppl, #e8f0ff);
  color: var(--n-primary-color, #2080f0);
  border-color: var(--n-primary-color, #2080f0);
}

.facts-by-subject {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.subject-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.subject-label {
  font-size: 0.8em;
  font-weight: 600;
  opacity: 0.8;
}

.subject-facts {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-left: 8px;
}

.fact-card {
  padding: 6px 8px;
  background-color: var(--moira-user-message-bg, #f5f5f5);
  border-radius: 4px;
  font-size: 0.85em;
}

.fact-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 2px;
}

.fact-status {
  font-size: 0.8em;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 3px;
  text-transform: capitalize;
}

.fact-status.verified {
  background-color: var(--n-success-color-suppl, #e8f5e9);
  color: var(--n-success-color, #18a058);
}

.fact-status.unverified {
  background-color: var(--n-warning-color-suppl, #fff8e1);
  color: var(--n-warning-color, #f0a020);
}

.fact-status.contradicted {
  background-color: var(--n-error-color-suppl, #fbe9e7);
  color: var(--n-error-color, #d03050);
}

.fact-status.unknown {
  background-color: var(--moira-border, #eee);
  color: var(--n-text-color-3, #999);
}

.fact-id {
  font-family: monospace;
  font-size: 0.8em;
  opacity: 0.6;
}

.fact-needed {
  opacity: 0.8;
}

.fact-claim {
  font-weight: 500;
  margin-top: 2px;
}

.fact-structured {
  display: flex;
  gap: 6px;
  margin-top: 2px;
  font-size: 0.9em;
}

.structured-field {
  font-style: italic;
  opacity: 0.7;
}

.structured-value {
  font-weight: 500;
}

.fact-note {
  margin-top: 4px;
  font-size: 0.9em;
  opacity: 0.7;
  font-style: italic;
}

.fact-citations {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}

.citation-ref {
  font-family: monospace;
  font-size: 0.75em;
  padding: 1px 4px;
  border-radius: 2px;
  background-color: var(--moira-border, #e0e0e0);
  color: var(--n-text-color-2, #666);
}

/* Conclusions section */
.conclusions-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.conclusion-card {
  padding: 6px 8px;
  background-color: var(--moira-user-message-bg, #f5f5f5);
  border-radius: 4px;
  font-size: 0.85em;
}

.conclusion-text {
  font-weight: 500;
}

.supporting-facts {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
  flex-wrap: wrap;
}

.fact-ref {
  font-family: monospace;
  font-size: 0.75em;
  padding: 1px 4px;
  border-radius: 2px;
  background-color: var(--n-primary-color-suppl, #e8f0ff);
  color: var(--n-primary-color, #2080f0);
}

.conclusion-reasoning {
  margin-top: 4px;
  font-size: 0.9em;
  opacity: 0.7;
}

/* Sources section */
.sources-list {
  padding-left: 20px;
  margin: 0;
}

.source-item {
  font-size: 0.85em;
  padding: 2px 0;
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
}

.source-name {
  font-weight: 500;
}

.source-url {
  color: var(--n-primary-color, #2080f0);
  text-decoration: none;
  font-size: 0.9em;
  word-break: break-all;
}

.source-url:hover {
  text-decoration: underline;
}

.source-title {
  opacity: 0.7;
  font-size: 0.9em;
}

.source-id {
  font-family: monospace;
  font-size: 0.75em;
  opacity: 0.5;
}
</style>
