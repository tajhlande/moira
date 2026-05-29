<script setup lang="ts">
import { NText, NIcon, NDivider } from "naive-ui";
import { Tools } from "@vicons/tabler";
import { useToolsStore } from "../stores/tools";
import { useRouter } from "vue-router";

const store = useToolsStore();
const router = useRouter();
</script>

<template>
  <div class="catalog-view">
    <div class="catalog-header">
      <NIcon :size="28" class="header-icon"><Tools /></NIcon>
      <NText class="header-title">Tool Catalog</NText>
    </div>

    <div class="catalog-summary">
      <div class="summary-card">
        <NText class="summary-value">{{ store.toolCount }}</NText>
        <NText depth="3" class="summary-label">Tools</NText>
      </div>
      <div class="summary-card">
        <NText class="summary-value">{{ store.groupCount }}</NText>
        <NText depth="3" class="summary-label">Groups</NText>
      </div>
      <div class="summary-card">
        <NText class="summary-value">—</NText>
        <NText depth="3" class="summary-label">Used This Session</NText>
      </div>
    </div>

    <NDivider />

    <NText depth="3">
      Select a tool from the sidebar to view details, or click "Add Tool" to
      discover and register new tools.
    </NText>

    <div class="catalog-groups">
      <div v-for="[group, tools] of store.groups" :key="group" class="catalog-group">
        <NText strong class="catalog-group-title">{{ group }}</NText>
        <div class="catalog-tool-list">
          <div
            v-for="tool in tools"
            :key="tool.name"
            class="catalog-tool-card"
            @click="router.push({ name: 'tool-detail', params: { name: tool.name } })"
          >
            <NText strong class="catalog-tool-name">{{ tool.name }}</NText>
            <NText depth="3" class="catalog-tool-desc">{{ tool.description }}</NText>
            <div class="catalog-tool-meta">
              <NText depth="3" class="catalog-tool-params">
                {{ tool.parameters.length }} parameter{{ tool.parameters.length !== 1 ? 's' : '' }}
              </NText>
              <NText v-if="tool.builtIn" depth="3" class="catalog-tool-badge">Built-in</NText>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.catalog-view {
  padding: 32px;
  max-width: 800px;
  width: 100%;
}

.catalog-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}

.header-icon {
  color: var(--n-primary-color);
}

.header-title {
  font-size: 1.6em;
  font-weight: 700;
}

.catalog-summary {
  display: flex;
  gap: 16px;
}

.summary-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 16px 24px;
  border: 1px solid var(--n-border-color, #e0e0e0);
  border-radius: 8px;
  min-width: 120px;
}

.summary-value {
  font-size: 1.8em;
  font-weight: 700;
  color: var(--n-primary-color);
}

.summary-label {
  font-size: 0.85em;
  margin-top: 4px;
}

.catalog-groups {
  margin-top: 8px;
}

.catalog-group {
  margin-bottom: 24px;
}

.catalog-group-title {
  font-size: 1.1em;
  margin-bottom: 8px;
  display: block;
}

.catalog-tool-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.catalog-tool-card {
  padding: 12px 16px;
  border: 1px solid var(--n-border-color, #e0e0e0);
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.15s;
}

.catalog-tool-card:hover {
  border-color: var(--n-primary-color);
}

.catalog-tool-name {
  font-family: monospace;
  display: block;
  margin-bottom: 4px;
}

.catalog-tool-desc {
  font-size: 0.9em;
  display: block;
  margin-bottom: 8px;
}

.catalog-tool-meta {
  display: flex;
  gap: 12px;
  align-items: center;
}

.catalog-tool-params {
  font-size: 0.8em;
}

.catalog-tool-badge {
  font-size: 0.75em;
  padding: 2px 6px;
  border-radius: 3px;
  background-color: var(--n-primary-color-suppl, #e8f5e9);
  color: var(--n-primary-color);
}
</style>
