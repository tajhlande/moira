<script setup lang="ts">
import type { ResearchReport } from "../api/client";
import MarkdownContent from "./MarkdownContent.vue";
import "./workflow-artifacts.css";

defineProps<{ report: ResearchReport }>();
</script>

<template>
  <div class="report-panel">
    <h3>Research Report</h3>
    <MarkdownContent class="report-answer" :content="report.answer" />

    <div v-if="report.citations.length > 0" class="report-section">
      <h4>Citations</h4>
      <ul>
        <li v-for="(c, ci) in report.citations" :key="ci">
          {{ c.source }}{{ c.url ? ' — ' + c.url : '' }}
          <span v-if="c.excerpt" class="citation-excerpt">{{ c.excerpt }}</span>
        </li>
      </ul>
    </div>

    <div v-if="report.critiques.length > 0" class="report-section">
      <h4>Critiques</h4>
      <ul>
        <li v-for="(c, ci) in report.critiques" :key="ci">{{ c }}</li>
      </ul>
    </div>

    <div v-if="report.unverified_claims.length > 0" class="report-section">
      <h4>Unverified Claims</h4>
      <ul>
        <li v-for="(c, ci) in report.unverified_claims" :key="ci" class="unverified">
          {{ c }}
        </li>
      </ul>
    </div>

    <div class="budget-consumed">
      Budget consumed: {{ report.budget_consumed.toFixed(0) }}
    </div>
  </div>
</template>
