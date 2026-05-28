<script setup lang="ts">
import type { ResearchReport } from "../api/client";

defineProps<{ report: ResearchReport }>();
</script>

<template>
  <div class="report-panel">
    <h3>Research Report</h3>
    <div class="report-answer">{{ report.answer }}</div>

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

<style scoped>
.report-panel {
  padding: 16px 0;
  max-width: 80%;
}

.report-panel h3 {
  margin: 0 0 8px 0;
}

.report-answer {
  white-space: pre-wrap;
  line-height: 1.5;
}

.report-section {
  margin-top: 12px;
}

.report-section h4 {
  margin: 0 0 4px 0;
  font-size: 0.9em;
  opacity: 0.7;
}

.report-section ul {
  margin: 0;
  padding-left: 20px;
  font-size: 0.9em;
}

.citation-excerpt {
  display: block;
  opacity: 0.7;
  font-style: italic;
  font-size: 0.9em;
}

.unverified {
  color: #d03050;
}

.budget-consumed {
  margin-top: 8px;
  font-size: 0.85em;
  opacity: 0.6;
}
</style>
