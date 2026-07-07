<script setup lang="ts">
import { ref, computed } from "vue";
import { NButton } from "naive-ui";
import {
  IconMarkdown,
  IconFileTypography,
  IconAlertTriangle,
  IconChevronDown,
  IconChevronRight,
} from "@tabler/icons-vue";
import type { ResearchReport } from "../api/client";
import MarkdownContent from "./MarkdownContent.vue";
import CitationMarkdown from "./CitationMarkdown.vue";
import CopyButton from "./CopyButton.vue";
import "./workflow-artifacts.css";

const props = defineProps<{ report: ResearchReport }>();

const report = computed(() => ({
  ...props.report,
  citations: props.report.citations ?? [],
  uncited_sources: props.report.uncited_sources ?? [],
  verified_facts: props.report.verified_facts ?? [],
  verified_conclusions: props.report.verified_conclusions ?? [],
  contradicted: props.report.contradicted ?? [],
  unknown_facts: props.report.unknown_facts ?? [],
  critiques: props.report.critiques ?? [],
  total_cost:
    typeof props.report.total_cost === "number" ? props.report.total_cost : 0,
  tool_call_total_cost:
    typeof props.report.tool_call_total_cost === "number"
      ? props.report.tool_call_total_cost
      : 0,
}));

const warningMessage = computed(() => {
  const reason = report.value.generation_reason;
  if (reason === "budget_exhausted") {
    return "Research was limited by insufficient budget. The answer below may be incomplete.";
  }
  if (reason === "retries_exhausted") {
    return "Evaluation identified gaps but the configured retry limit was reached. The answer below may be incomplete.";
  }
  if (reason === "incomplete") {
    return "Evaluation accepted the research but some conclusions remain unverified. The answer below may be incomplete.";
  }
  if (reason === "error") {
    return "An error occurred during research. The answer below may be incomplete.";
  }
  return null;
});
const showRaw = ref(false);

const hoveredCitation = ref<{ index: number; x: number; y: number } | null>(
  null,
);
let hideTimer: ReturnType<typeof setTimeout> | null = null;

const expandedCitations = ref<Set<number>>(new Set());

const showUncited = ref(false);

function toggleCitation(index: number) {
  if (expandedCitations.value.has(index)) {
    expandedCitations.value.delete(index);
  } else {
    expandedCitations.value.add(index);
  }
  // Trigger reactivity for Set mutation
  expandedCitations.value = new Set(expandedCitations.value);
}

const fullReportMarkdown = computed(() => buildFullReport());

// Safe accessor for the currently hovered citation — avoids repeated
// nullable indexing in the template.
const activeCitation = computed(() => {
  if (!hoveredCitation.value) return null;
  return report.value.citations[hoveredCitation.value.index] ?? null;
});

function buildFullReport(): string {
  const parts: string[] = [report.value.answer];
  if (report.value.citations.length > 0) {
    parts.push("\n\n## Sources\n");
    for (const c of report.value.citations) {
      let line = `- ${c.source}`;
      if (c.url) line += ` — ${c.url}`;
      const snippets = c.snippets ?? (c.excerpt ? [c.excerpt] : []);
      for (const s of snippets) line += `\n  > ${s}`;
      parts.push(line);
    }
  }
  if (report.value.uncited_sources.length > 0) {
    parts.push("\n\n## Additional Sources (consulted but not cited)\n");
    for (const c of report.value.uncited_sources) {
      let line = `- ${c.source}`;
      if (c.url) line += ` — ${c.url}`;
      parts.push(line);
    }
  }
  if (report.value.verified_facts.length > 0) {
    parts.push("\n\n## Verified Facts\n");
    for (const f of report.value.verified_facts)
      parts.push(`- ${f.subject}: ${f.claim}`);
  }
  if (report.value.verified_conclusions.length > 0) {
    parts.push("\n\n## Verified Conclusions\n");
    for (const c of report.value.verified_conclusions)
      parts.push(`- ${c.conclusion}`);
  }
  if (report.value.contradicted.length > 0) {
    parts.push("\n\n## Contradicted\n");
    for (const c of report.value.contradicted) {
      const main = c.claim
        ? `${c.subject ? c.subject + ": " : ""}${c.claim}`
        : (c.conclusion ?? c.id);
      let line = `- ~~${main}~~`;
      if (c.verification_note) line += `\n  → ${c.verification_note}`;
      parts.push(line);
    }
  }
  if (report.value.critiques.length > 0) {
    parts.push("\n\n## Critiques\n");
    for (const c of report.value.critiques) parts.push(`- ${c}`);
  }
  if (report.value.unknown_facts.length > 0) {
    parts.push("\n\n## Unresolved Facts\n");
    for (const f of report.value.unknown_facts)
      parts.push(`- ${f.subject}: ${f.fact_needed}`);
  }
  return parts.join("\n");
}

function handleAnswerMouseOver(e: MouseEvent) {
  const target = (e.target as HTMLElement).closest(".cite-ref");
  if (!target) return;
  const num = parseInt((target as HTMLElement).dataset.cite || "", 10);
  if (isNaN(num) || num < 1 || num > report.value.citations.length) return;

  if (hideTimer !== null) {
    clearTimeout(hideTimer);
    hideTimer = null;
  }

  const rect = (target as HTMLElement).getBoundingClientRect();
  hoveredCitation.value = {
    index: num - 1,
    x: rect.left,
    y: rect.bottom + 6,
  };
}

function handleAnswerMouseOut(e: MouseEvent) {
  const target = (e.target as HTMLElement).closest(".cite-ref");
  if (!target) return;
  hideTimer = setTimeout(() => {
    hoveredCitation.value = null;
  }, 150);
}

function handleTooltipEnter() {
  if (hideTimer !== null) {
    clearTimeout(hideTimer);
    hideTimer = null;
  }
}

function handleTooltipLeave() {
  hoveredCitation.value = null;
}
</script>

<template>
  <div class="report-panel">
    <div v-if="warningMessage" class="report-warning">
      <IconAlertTriangle :size="16" class="warning-icon" />
      <span>{{ warningMessage }}</span>
    </div>
    <div
      v-if="!showRaw"
      class="report-answer"
      @mouseover="handleAnswerMouseOver"
      @mouseout="handleAnswerMouseOut"
    >
      <CitationMarkdown
        :content="report.answer"
        :citations="report.citations"
      />
    </div>
    <pre v-else class="report-raw">{{ report.answer }}</pre>
    <div class="answer-footer">
      <NButton
        quaternary
        circle
        size="tiny"
        class="icon-action-btn"
        :title="showRaw ? 'Rendered view' : 'Raw markdown'"
        @click="showRaw = !showRaw"
      >
        <template #icon>
          <IconFileTypography v-if="showRaw" :size="14" />
          <IconMarkdown v-else :size="14" />
        </template>
      </NButton>
      <CopyButton :text="showRaw ? buildFullReport() : report.answer" />
    </div>

    <div v-if="report.citations.length > 0" class="report-secondary-section">
      <h4>Sources</h4>
      <ol>
        <li
          v-for="(c, ci) in report.citations"
          :key="ci"
          :id="'cite-' + (ci + 1)"
        >
          <div class="citation-row">
            <span class="citation-label">
              {{ c.source }}
              <a
                v-if="c.url"
                :href="c.url"
                target="_blank"
                rel="noopener noreferrer"
                >{{ c.url }}</a
              >
            </span>
            <button
              v-if="(c.snippets && c.snippets.length) || c.excerpt"
              class="citation-toggle"
              @click="toggleCitation(ci)"
            >
              <IconChevronDown v-if="expandedCitations.has(ci)" :size="16" />
              <IconChevronRight v-else :size="16" />
            </button>
          </div>
          <div
            v-if="
              expandedCitations.has(ci) &&
              ((c.snippets && c.snippets.length) || c.excerpt)
            "
            class="citation-snippet-area"
          >
            <span
              v-if="c.snippets && c.snippets.length"
              class="citation-snippets"
            >
              <span
                v-for="(s, si) in c.snippets"
                :key="si"
                class="citation-snippet"
                >{{ s }}</span
              >
            </span>
            <span v-else-if="c.excerpt" class="citation-excerpt">{{
              c.excerpt
            }}</span>
          </div>
        </li>
      </ol>
    </div>

    <div
      v-if="report.uncited_sources.length > 0"
      class="report-secondary-section report-uncited-sources"
    >
      <h4 class="collapsible-header" @click="showUncited = !showUncited">
        <span class="collapsible-header-label">
          Additional Sources
          <span class="uncited-count"
            >({{ report.uncited_sources.length }} consulted, not cited)</span
          >
        </span>
        <button class="section-toggle">
          <IconChevronDown v-if="showUncited" :size="18" />
          <IconChevronRight v-else :size="18" />
        </button>
      </h4>
      <ul v-if="showUncited">
        <li
          v-for="(c, ci) in report.uncited_sources"
          :key="ci"
          class="uncited-source"
        >
          {{ c.source }}
          <a
            v-if="c.url"
            :href="c.url"
            target="_blank"
            rel="noopener noreferrer"
            >{{ c.url }}</a
          >
        </li>
      </ul>
    </div>

    <div
      v-if="report.verified_facts.length > 0"
      class="report-secondary-section"
    >
      <h4>Verified Facts</h4>
      <ul>
        <li v-for="(f, fi) in report.verified_facts" :key="fi" class="verified">
          <MarkdownContent :content="`${f.subject}: ${f.claim}`" inline />
        </li>
      </ul>
    </div>

    <div
      v-if="report.verified_conclusions.length > 0"
      class="report-secondary-section"
    >
      <h4>Verified Conclusions</h4>
      <ul>
        <li
          v-for="(c, ci) in report.verified_conclusions"
          :key="ci"
          class="verified"
        >
          <MarkdownContent :content="c.conclusion" inline />
        </li>
      </ul>
    </div>

    <div v-if="report.contradicted.length > 0" class="report-secondary-section">
      <h4>Contradicted</h4>
      <ul>
        <li
          v-for="(c, ci) in report.contradicted"
          :key="ci"
          class="contradicted"
        >
          <MarkdownContent
            :content="
              c.claim
                ? `${c.subject ? c.subject + ': ' : ''}${c.claim}`
                : (c.conclusion ?? c.id)
            "
            inline
          />
          <span v-if="c.verification_note" class="contradiction-note">{{
            c.verification_note
          }}</span>
        </li>
      </ul>
    </div>

    <div v-if="report.critiques.length > 0" class="report-secondary-section">
      <h4>Critiques</h4>
      <ul>
        <li v-for="(c, ci) in report.critiques" :key="ci">
          <MarkdownContent :content="c" inline />
        </li>
      </ul>
    </div>

    <div
      v-if="report.unknown_facts.length > 0"
      class="report-secondary-section"
    >
      <h4>Unresolved Facts</h4>
      <ul>
        <li
          v-for="(f, fi) in report.unknown_facts"
          :key="fi"
          class="unverified"
        >
          <MarkdownContent :content="`${f.subject}: ${f.fact_needed}`" inline />
        </li>
      </ul>
    </div>

    <div class="report-footer">
      <span class="budget-consumed">
        Budget consumed: {{ report.total_cost.toFixed(0) }}
      </span>
      <CopyButton :text="buildFullReport()" />
    </div>

    <div
      v-if="hoveredCitation !== null"
      class="citation-tooltip"
      :style="{
        left: hoveredCitation.x + 'px',
        top: hoveredCitation.y + 'px',
      }"
      @mouseenter="handleTooltipEnter"
      @mouseleave="handleTooltipLeave"
    >
      <div class="citation-tooltip-source">
        {{ activeCitation?.source }}
      </div>
      <a
        v-if="activeCitation?.url"
        :href="activeCitation.url"
        target="_blank"
        rel="noopener noreferrer"
        class="citation-tooltip-url"
      >
        {{ activeCitation.url }}
      </a>
      <div v-if="activeCitation?.excerpt" class="citation-tooltip-excerpt">
        {{ activeCitation.excerpt }}
      </div>
    </div>
  </div>
</template>
