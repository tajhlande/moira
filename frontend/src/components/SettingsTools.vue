<script setup lang="ts">
import { ref, onMounted } from "vue";
import {
  NText,
  NButton,
  NSpin,
  NSwitch,
  NEmpty,
  NTooltip,
  useDialog,
  useMessage,
} from "naive-ui";
import { IconTools, IconPlus, IconTrash, IconLock } from "@tabler/icons-vue";
import { useRouter } from "vue-router";
import { api, type ApiSourceInfo } from "../api/client";

const message = useMessage();
const dialog = useDialog();
const router = useRouter();

const loading = ref(true);
const sources = ref<ApiSourceInfo[]>([]);

onMounted(async () => {
  try {
    const resp = await api.listIngestSources();
    sources.value = resp.sources;
  } catch {
    message.error("Failed to load API sources");
  } finally {
    loading.value = false;
  }
});

function addApi() {
  router.push({ name: "settings-tools-ingest" });
}

function deleteSource(source: ApiSourceInfo) {
  dialog.warning({
    title: "Delete API Source",
    content: `Delete "${source.name}" and all ${source.tool_count} tool(s)?`,
    positiveText: "Delete",
    negativeText: "Cancel",
    onPositiveClick: async () => {
      try {
        await api.deleteIngestSource(source.id);
        sources.value = sources.value.filter((s) => s.id !== source.id);
        message.success(`Deleted ${source.name}`);
      } catch (e) {
        message.error(
          e instanceof Error ? e.message : "Failed to delete source",
        );
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
</script>

<template>
  <div class="settings-tools">
    <div class="section-header">
      <IconTools :size="24" class="section-icon" />
      <NText class="section-title">Tools</NText>
      <NButton type="primary" size="small" @click="addApi" class="add-btn">
        <template #icon>
          <IconPlus :size="16" />
        </template>
        Add API
      </NButton>
    </div>

    <NSpin :show="loading">
      <div v-if="!loading">
        <NEmpty
          v-if="sources.length === 0"
          description="No external APIs registered yet. Add an API to expand your agent's capabilities."
        />

        <div v-else class="sources-list">
          <div
            v-for="source in sources"
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
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.settings-tools {
  padding: 20px;
  max-width: 700px;
  width: 100%;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.section-icon {
  color: var(--n-primary-color);
}

.section-title {
  font-size: 1.2em;
  font-weight: 600;
}

.add-btn {
  margin-left: auto;
}

.sources-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.source-card {
  border: 1px solid var(--n-border-color, #e0e0e0);
  border-radius: 8px;
  padding: 16px;
}

.source-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 8px;
}

.source-title-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.source-name {
  font-size: 1em;
}

.source-url {
  font-size: 0.85em;
  font-family: monospace;
}

.source-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.source-count {
  font-size: 0.85em;
}

.source-meta {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 0.85em;
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
  font-size: 0.8em;
  padding: 2px 6px;
  border-radius: 3px;
  background-color: var(--n-action-color, #f5f5f5);
}
</style>
