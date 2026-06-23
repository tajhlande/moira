<script setup lang="ts">
import { ref, onMounted } from "vue";
import {
  NText,
  NDivider,
  NButton,
  NTooltip,
  NScrollbar,
  NEmpty,
  useDialog,
  useMessage,
} from "naive-ui";
import { IconTools, IconTrash, IconLock } from "@tabler/icons-vue";
import { useToolsStore } from "../stores/tools";
import { api, type ApiSourceInfo } from "../api/client";

const store = useToolsStore();
const message = useMessage();
const dialog = useDialog();

const apiSources = ref<ApiSourceInfo[]>([]);

onMounted(async () => {
  store.fetchTools();
  try {
    const resp = await api.listIngestSources();
    apiSources.value = resp.sources;
  } catch {
    // Sources not available yet
  }
});

function deleteSource(source: ApiSourceInfo) {
  dialog.warning({
    title: "Delete API Source",
    content: `Delete "${source.name}" and all ${source.tool_count} tool(s)?`,
    positiveText: "Delete",
    negativeText: "Cancel",
    onPositiveClick: async () => {
      try {
        await api.deleteIngestSource(source.id);
        apiSources.value = apiSources.value.filter((s) => s.id !== source.id);
        message.success(`Deleted ${source.name}`);
        store.refreshTools();
      } catch (e) {
        message.error(e instanceof Error ? e.message : "Failed to delete");
      }
    },
  });
}

function authLabel(authType: string | null): string {
  if (!authType) return "";
  const labels: Record<string, string> = {
    bearer: "Bearer Token",
    api_key_header: "API Key (header)",
    api_key_query: "API Key (query)",
    basic: "Basic Auth",
  };
  return labels[authType] || authType;
}

const enabledCount = () => store.tools.filter((t) => t.enabled).length;
</script>

<template>
  <NScrollbar class="catalog-scroll">
    <div class="catalog-view">
      <div class="catalog-header">
        <IconTools :size="28" class="header-icon" />
        <NText class="header-title">Tools</NText>
      </div>

      <div class="catalog-summary">
        <div class="summary-card">
          <NText class="summary-value">{{ store.toolCount }}</NText>
          <NText depth="3" class="summary-label">Registered</NText>
        </div>
        <div class="summary-card">
          <NText class="summary-value">{{ enabledCount() }}</NText>
          <NText depth="3" class="summary-label">Enabled</NText>
        </div>
        <div class="summary-card">
          <NText class="summary-value">{{ apiSources.length }}</NText>
          <NText depth="3" class="summary-label">API Sources</NText>
        </div>
      </div>

      <NText depth="3" class="catalog-hint">
        Select a tool from the sidebar to view details and configure it, or
        click "Add API" to discover and register new tools from an external API.
      </NText>

      <!-- API Sources section -->
      <template v-if="apiSources.length > 0">
        <NDivider class="section-divider">API Sources</NDivider>
        <div class="sources-list">
          <div
            v-for="source in apiSources"
            :key="source.id"
            class="source-card"
          >
            <div class="source-header">
              <div class="source-title-row">
                <NText strong class="source-name">{{ source.name }}</NText>
                <NText depth="3" class="source-url">{{
                  source.base_url
                }}</NText>
              </div>
              <div class="source-actions">
                <NText depth="3" class="source-count"
                  >{{ source.tool_count }} tool{{
                    source.tool_count !== 1 ? "s" : ""
                  }}</NText
                >
                <NTooltip>
                  <template #trigger>
                    <NButton
                      quaternary
                      circle
                      size="small"
                      @click="deleteSource(source)"
                    >
                      <template #icon>
                        <IconTrash :size="16" />
                      </template>
                    </NButton>
                  </template>
                  Delete source
                </NTooltip>
              </div>
            </div>
            <div class="source-meta">
              <NText v-if="source.auth_type" depth="3" class="source-auth">
                <IconLock :size="14" class="auth-icon" />
                {{ authLabel(source.auth_type) }}
              </NText>
              <NText v-else depth="3" class="source-auth no-auth">
                No authentication required
              </NText>
              <NText depth="3" class="source-format">{{
                source.spec_format
              }}</NText>
            </div>
          </div>
        </div>
      </template>

      <NEmpty
        v-else
        description="No external APIs registered yet."
        class="empty-sources"
      />
    </div>
  </NScrollbar>
</template>

<style scoped>
.catalog-scroll {
  flex: 1;
  min-height: 0;
}

.catalog-view {
  padding: 32px;
  max-width: 700px;
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
  margin-bottom: 16px;
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

.catalog-hint {
  font-size: 0.9em;
  display: block;
  margin-bottom: 8px;
}

.section-divider {
  margin: 16px 0 12px;
}

/* API Sources */
.sources-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.source-card {
  border: 1px solid var(--n-border-color, #e0e0e0);
  border-radius: 8px;
  padding: 12px 16px;
}

.source-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 6px;
}

.source-title-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.source-name {
  font-size: 0.95em;
}

.source-url {
  font-size: 0.82em;
  font-family: monospace;
}

.source-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.source-count {
  font-size: 0.82em;
}

.source-meta {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 0.82em;
}

.source-auth {
  display: flex;
  align-items: center;
  gap: 4px;
}

.source-auth.no-auth {
  color: var(--n-success-color, #18a058);
}

.auth-icon {
  flex-shrink: 0;
}

.source-format {
  font-family: monospace;
  font-size: 0.78em;
  padding: 2px 6px;
  border-radius: 3px;
  background-color: var(--n-action-color, #f5f5f5);
}

.empty-sources {
  margin-top: 16px;
}
</style>
