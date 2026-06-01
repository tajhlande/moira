<script setup lang="ts">
import { ref } from "vue";
import { NButton, NIcon } from "naive-ui";
import { Copy, CircleCheck } from "@vicons/tabler";
import type { ResearchReport } from "../api/client";
import MarkdownContent from "./MarkdownContent.vue";
import "./workflow-artifacts.css";

const props = defineProps<{ report: ResearchReport }>();
const copiedAnswer = ref(false);
const copiedFull = ref(false);

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
    parts.push("\n\n## Citations\n");
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
</script>

<template>
  <div class="report-panel">
    <h3>Research Report</h3>
    <MarkdownContent class="report-answer" :content="report.answer" />
    <div class="answer-footer">
      <NButton
        quaternary
        circle
        size="tiny"
        class="copy-btn"
        @click="copyAnswer"
      >
        <template #icon>
          <NIcon size="14">
            <Copy v-if="!copiedAnswer" />
            <CircleCheck v-else />
          </NIcon>
        </template>
      </NButton>
    </div>

    <div v-if="report.citations.length > 0" class="report-section">
      <h4>Citations</h4>
      <ul>
        <li v-for="(c, ci) in report.citations" :key="ci">
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
      </ul>
    </div>

    <div v-if="report.critiques.length > 0" class="report-section">
      <h4>Critiques</h4>
      <ul>
        <li v-for="(c, ci) in report.critiques" :key="ci">
          <MarkdownContent :content="c" inline />
        </li>
      </ul>
    </div>

    <div v-if="report.unverified_claims.length > 0" class="report-section">
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
        class="copy-btn"
        @click="copyFullReport"
      >
        <template #icon>
          <NIcon size="14">
            <Copy v-if="!copiedFull" />
            <CircleCheck v-else />
          </NIcon>
        </template>
      </NButton>
    </div>
  </div>
</template>
