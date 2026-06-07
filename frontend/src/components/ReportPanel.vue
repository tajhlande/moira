<script setup lang="ts">
import { ref, computed } from "vue";
import { NButton } from "naive-ui";
import {
  IconCopy,
  IconCircleCheck,
  IconMarkdown,
  IconFileTypography,
} from "@tabler/icons-vue";
import type { ResearchReport } from "../api/client";
import MarkdownContent from "./MarkdownContent.vue";
import CitationMarkdown from "./CitationMarkdown.vue";
import "./workflow-artifacts.css";

const props = defineProps<{ report: ResearchReport }>();
const copiedAnswer = ref(false);
const copiedFull = ref(false);
const showRaw = ref(false);

const hoveredCitation = ref<{ index: number; x: number; y: number } | null>(
  null,
);
let hideTimer: ReturnType<typeof setTimeout> | null = null;

const fullReportMarkdown = computed(() => buildFullReport());

async function copyAnswer() {
  await navigator.clipboard.writeText(props.report.answer);
  copiedAnswer.value = true;
  setTimeout(() => {
    copiedAnswer.value = false;
  }, 1500);
}

function buildFullReport(): string {
  const parts: string[] = [props.report.answer];
  if (props.report.citations.length > 0) {
    parts.push("\n\n## Sources\n");
    for (const c of props.report.citations) {
      let line = `- ${c.source}`;
      if (c.url) line += ` — ${c.url}`;
      if (c.excerpt) line += `\n  > ${c.excerpt}`;
      parts.push(line);
    }
  }
  if (props.report.critiques.length > 0) {
    parts.push("\n\n## Critiques\n");
    for (const c of props.report.critiques) parts.push(`- ${c}`);
  }
  if (props.report.unverified_claims.length > 0) {
    parts.push("\n\n## Unverified Claims\n");
    for (const c of props.report.unverified_claims) parts.push(`- ${c}`);
  }
  return parts.join("\n");
}

async function copyFullReport() {
  await navigator.clipboard.writeText(buildFullReport());
  copiedFull.value = true;
  setTimeout(() => {
    copiedFull.value = false;
  }, 1500);
}

function handleAnswerMouseOver(e: MouseEvent) {
  const target = (e.target as HTMLElement).closest(".cite-ref");
  if (!target) return;
  const num = parseInt((target as HTMLElement).dataset.cite || "", 10);
  if (isNaN(num) || num < 1 || num > props.report.citations.length) return;

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
      <NButton
        quaternary
        circle
        size="tiny"
        class="icon-action-btn"
        @click="showRaw ? copyFullReport() : copyAnswer()"
      >
        <template #icon>
          <IconCopy v-if="!(showRaw ? copiedFull : copiedAnswer)" :size="14" />
          <IconCircleCheck v-else :size="14" />
        </template>
      </NButton>
    </div>

    <div v-if="report.citations.length > 0" class="report-secondary-section">
      <h4>Sources</h4>
      <ol>
        <li v-for="(c, ci) in report.citations" :key="ci" :id="'cite-' + (ci + 1)">
          {{ c.source }}
          <a
            v-if="c.url"
            :href="c.url"
            target="_blank"
            rel="noopener noreferrer"
            >{{ c.url }}</a
          >
          <span v-if="c.excerpt" class="citation-excerpt">{{ c.excerpt }}</span>
        </li>
      </ol>
    </div>

    <div v-if="report.critiques.length > 0" class="report-secondary-section">
      <h4>Critiques</h4>
      <ul>
        <li v-for="(c, ci) in report.critiques" :key="ci">
          <MarkdownContent :content="c" inline />
        </li>
      </ul>
    </div>

    <div v-if="report.unverified_claims.length > 0" class="report-secondary-section">
      <h4>Unverified Claims</h4>
      <ul>
        <li
          v-for="(c, ci) in report.unverified_claims"
          :key="ci"
          class="unverified"
        >
          <MarkdownContent :content="c" inline />
        </li>
      </ul>
    </div>

    <div class="report-footer">
      <span class="budget-consumed">
        Budget consumed: {{ report.budget_consumed.toFixed(0) }}
      </span>
      <NButton
        quaternary
        circle
        size="tiny"
        class="icon-action-btn"
        @click="copyFullReport"
      >
        <template #icon>
          <IconCopy v-if="!copiedFull" :size="14" />
          <IconCircleCheck v-else :size="14" />
        </template>
      </NButton>
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
        {{ report.citations[hoveredCitation.index].source }}
      </div>
      <a
        v-if="report.citations[hoveredCitation.index].url"
        :href="report.citations[hoveredCitation.index].url"
        target="_blank"
        rel="noopener noreferrer"
        class="citation-tooltip-url"
      >
        {{ report.citations[hoveredCitation.index].url }}
      </a>
      <div
        v-if="report.citations[hoveredCitation.index].excerpt"
        class="citation-tooltip-excerpt"
      >
        {{ report.citations[hoveredCitation.index].excerpt }}
      </div>
    </div>
  </div>
</template>
