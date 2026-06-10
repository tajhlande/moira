<script setup lang="ts">
import { ref } from "vue";
import { NInput, NButton, NText, NSpin, NDataTable } from "naive-ui";
import { IconSearch } from "@tabler/icons-vue";
import { api } from "../api/client";

const query = ref("");
const loading = ref(false);
const results = ref<{ name: string; description: string; enabled: boolean; distance: number }[]>([]);

const columns = [
  { title: "Rank", key: "rank", width: 55 },
  { title: "Distance", key: "distance", width: 90 },
  { title: "Enabled", key: "enabled", width: 75 },
  { title: "Tool", key: "name", width: 240 },
  { title: "Description", key: "description" },
];

const tableData = ref<{ rank: number; distance: string; enabled: string; name: string; description: string }[]>([]);

async function search() {
  if (!query.value.trim()) return;
  loading.value = true;
  try {
    const resp = await api.embeddingSearch(query.value.trim());
    results.value = resp.results;
    tableData.value = resp.results.map((r, i) => ({
      rank: i + 1,
      distance: r.distance.toFixed(4),
      enabled: r.enabled ? "Yes" : "No",
      name: r.name,
      description: r.description.slice(0, 200),
    }));
  } catch {
    results.value = [];
    tableData.value = [];
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="debug-view">
    <NText tag="h2" class="page-title">Embedding Search Debug</NText>
    <NText depth="3" class="page-desc">
      Query the LanceDB vector index to inspect which tools are returned for a
      given query and at what distance scores. Lower distance = better match.
    </NText>

    <div class="search-row">
      <NInput
        v-model:value="query"
        placeholder="Search query (e.g. 'weather forecast' or 'pokemon stats')"
        @keyup.enter="search"
      />
      <NButton type="primary" :loading="loading" @click="search">
        <template #icon>
          <IconSearch :size="16" />
        </template>
        Search
      </NButton>
    </div>

    <NSpin v-if="loading" class="results-spinner" />

    <NDataTable
      v-else-if="tableData.length > 0"
      :columns="columns"
      :data="tableData"
      :bordered="true"
      :single-line="false"
      size="small"
      class="results-table"
    />

    <NText v-else-if="query" depth="3" class="no-results">No results.</NText>
  </div>
</template>

<style scoped>
.debug-view {
  padding: 32px;
  max-width: 900px;
  width: 100%;
  box-sizing: border-box;
}

.page-title {
  font-size: 1.4em;
  font-weight: 700;
  display: block;
  margin-bottom: 6px;
}

.page-desc {
  display: block;
  font-size: 0.9em;
  margin-bottom: 20px;
  line-height: 1.5;
}

.search-row {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
}

.search-row .n-input {
  flex: 1;
}

.results-table {
  margin-top: 12px;
}

.no-results {
  display: block;
  margin-top: 20px;
}
</style>
