<script setup lang="ts">
import { ref } from "vue";
import {
  NText,
  NDivider,
  NButton,
  NSwitch,
  NScrollbar,
  NInput,
  useMessage,
  useDialog,
} from "naive-ui";
import { IconArrowLeft, IconTrash, IconPencil, IconArrowBackUp } from "@tabler/icons-vue";
import { useToolsStore } from "../stores/tools";
import { useRoute, useRouter } from "vue-router";
import { computed } from "vue";
import { api } from "../api/client";

const store = useToolsStore();
const route = useRoute();
const router = useRouter();
const message = useMessage();
const dialog = useDialog();

const toolName = computed(() => route.params.name as string);
const tool = computed(() => store.tools.find((t) => t.name === toolName.value));
const isProtected = computed(() => tool.value?.groupName === "standard");

const editingDesc = ref(false);
const descEdit = ref("");
const descSaving = ref(false);

const hasOriginalDesc = computed(() => {
  if (!tool.value) return false;
  return (
    tool.value.originalDescription !== "" &&
    tool.value.description !== tool.value.originalDescription
  );
});

function startEditDesc() {
  if (!tool.value) return;
  descEdit.value = tool.value.description;
  editingDesc.value = true;
}

function cancelEditDesc() {
  editingDesc.value = false;
  descEdit.value = "";
}

async function saveDesc() {
  if (!toolName.value || !descEdit.value.trim()) return;
  descSaving.value = true;
  try {
    await store.patchTool(toolName.value, { description: descEdit.value.trim() });
    editingDesc.value = false;
    descEdit.value = "";
  } finally {
    descSaving.value = false;
  }
}

async function resetDesc() {
  if (!toolName.value) return;
  descSaving.value = true;
  try {
    await store.patchTool(toolName.value, { description: null });
  } finally {
    descSaving.value = false;
  }
}

const configSchema = ref<Record<string, unknown> | null>(null);
const configSchemaLoading = ref(false);
const configEdits = ref<Record<string, string>>({});
const configSaving = ref(false);

function confirmDelete() {
  if (!tool.value) return;
  dialog.error({
    title: "Delete tool?",
    content: `Permanently delete "${tool.value.name}"? Restoring it will require reimporting from the source API. This cannot be undone.`,
    positiveText: "Delete",
    negativeText: "Cancel",
    onPositiveClick: doDelete,
  });
}

async function doDelete() {
  if (!toolName.value) return;
  try {
    await api.deleteTool(toolName.value);
    message.success("Tool deleted");
    await store.refreshTools();
    router.push({ name: "tools" });
  } catch (e) {
    message.error(e instanceof Error ? e.message : "Failed to delete tool");
  }
}

async function fetchSpec() {
  if (!toolName.value) return;
  configSchemaLoading.value = true;
  try {
    const spec = await api.getToolSpec(toolName.value);
    configSchema.value = spec.config_schema ?? null;
  } catch (e) {
    console.warn("Failed to fetch tool spec:", e);
    configSchema.value = null;
  } finally {
    configSchemaLoading.value = false;
  }
}

fetchSpec();

interface SchemaProperty {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
}

const configProperties = computed(() => {
  if (!configSchema.value) return [];
  const props = (
    configSchema.value as { properties?: Record<string, SchemaProperty> }
  ).properties;
  if (!props) return [];
  return Object.entries(props).map(([key, schema]) => ({
    key,
    title: schema.title || key,
    description: schema.description || "",
    type: schema.type || "string",
    required: (
      (configSchema.value as { required?: string[] }).required ?? []
    ).includes(key),
    currentValue: tool.value?.config?.[key]?.toString() ?? "",
  }));
});

async function saveConfig() {
  if (!toolName.value) return;
  configSaving.value = true;
  try {
    const newConfig: Record<string, unknown> = { ...tool.value?.config };
    for (const [key, val] of Object.entries(configEdits.value)) {
      newConfig[key] = val;
    }
    await store.patchTool(toolName.value, { config: newConfig });
    configEdits.value = {};
  } finally {
    configSaving.value = false;
  }
}

async function onToggleEnabled(enabled: boolean) {
  if (tool.value) {
    await store.toggleEnabled(tool.value.name, enabled);
  }
}

async function onToggleDefault(isDefault: boolean) {
  if (tool.value) {
    await store.toggleDefault(tool.value.name, isDefault);
  }
}

const requiredParams = computed(() => {
  if (!tool.value) return [];
  return tool.value.parameters.filter((p) => p.required);
});

const optionalParams = computed(() => {
  if (!tool.value) return [];
  return tool.value.parameters.filter((p) => !p.required);
});

type FlatEntry = { path: string; value: string };

function flattenConfig(obj: Record<string, unknown>, prefix = ""): FlatEntry[] {
  const entries: FlatEntry[] = [];
  for (const [key, val] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (val === null || val === undefined) {
      entries.push({ path, value: String(val) });
    } else if (Array.isArray(val)) {
      val.forEach((item, i) => {
        if (typeof item === "object" && item !== null) {
          entries.push(
            ...flattenConfig(item as Record<string, unknown>, `${path}[${i}]`),
          );
        } else {
          entries.push({ path: `${path}[${i}]`, value: String(item) });
        }
      });
    } else if (typeof val === "object") {
      entries.push(...flattenConfig(val as Record<string, unknown>, path));
    } else {
      entries.push({ path, value: String(val) });
    }
  }
  return entries;
}

const configEntries = computed(() => {
  if (configProperties.value.length > 0) return [];
  if (!tool.value?.config || Object.keys(tool.value.config).length === 0)
    return [];
  return flattenConfig(tool.value.config);
});
</script>

<template>
  <NScrollbar class="detail-scroll">
    <div class="detail-view" v-if="tool">
    <div class="detail-header">
      <NButton quaternary circle @click="router.push({ name: 'tools' })">
        <template #icon>
          <IconArrowLeft />
        </template>
      </NButton>
      <div class="detail-title-area">
        <div class="detail-title-line">
          <NText class="detail-name">{{ tool.name }}</NText>
          <NText v-if="tool.builtIn" depth="3" class="detail-badge"
            >Built-in</NText
          >
        </div>
        <div class="detail-controls">
          <div class="control-item">
            <NText depth="3" class="control-label">Enabled</NText>
            <NSwitch :value="tool.enabled" @update:value="onToggleEnabled" />
          </div>
          <div class="control-item">
            <NText depth="3" class="control-label">Default</NText>
            <NSwitch :value="tool.isDefault" @update:value="onToggleDefault" />
          </div>
          <NButton
            v-if="!isProtected"
            type="error"
            ghost
            size="small"
            @click="confirmDelete"
          >
            <template #icon>
              <IconTrash :size="16" />
            </template>
            Delete
          </NButton>
        </div>
      </div>
    </div>

    <div v-if="!editingDesc" class="desc-section">
      <div class="desc-display">
        <NText class="detail-desc">{{ tool.description }}</NText>
        <div class="desc-actions">
          <NButton
            quaternary
            circle
            size="tiny"
            class="desc-edit-btn"
            @click="startEditDesc"
            title="Edit description"
          >
            <template #icon>
              <IconPencil :size="18" />
            </template>
          </NButton>
          <NButton
            v-if="hasOriginalDesc"
            quaternary
            circle
            size="tiny"
            class="desc-edit-btn"
            @click="resetDesc"
            title="Reset to original"
            :loading="descSaving"
          >
            <template #icon>
              <IconArrowBackUp :size="18" />
            </template>
          </NButton>
        </div>
      </div>
    </div>
    <div v-else class="desc-edit-section">
      <NInput
        v-model:value="descEdit"
        type="textarea"
        :autosize="{ minRows: 3, maxRows: 10 }"
        placeholder="Tool description"
      />
      <div class="desc-edit-actions">
        <NButton size="small" type="primary" :loading="descSaving" @click="saveDesc">
          Save
        </NButton>
        <NButton size="small" @click="cancelEditDesc">Cancel</NButton>
      </div>
    </div>

    <NDivider />

    <div v-if="requiredParams.length > 0" class="param-section">
      <NText strong>Required Parameters</NText>
      <div class="param-list">
        <div v-for="p in requiredParams" :key="p.name" class="param-card">
          <NText strong class="param-name">{{ p.name }}</NText>
          <NText depth="3" class="param-type">{{ p.type }}</NText>
          <NText class="param-desc">{{ p.description }}</NText>
        </div>
      </div>
    </div>

    <div v-if="optionalParams.length > 0" class="param-section">
      <NText strong>Optional Parameters</NText>
      <div class="param-list">
        <div v-for="p in optionalParams" :key="p.name" class="param-card">
          <NText strong class="param-name">{{ p.name }}</NText>
          <NText depth="3" class="param-type">{{ p.type }}</NText>
          <NText class="param-desc">{{ p.description }}</NText>
          <NText v-if="p.default !== undefined" depth="3" class="param-default">
            Default: {{ p.default }}
          </NText>
        </div>
      </div>
    </div>

    <NDivider />

    <div v-if="configProperties.length > 0" class="config-section">
      <NText strong class="config-heading">Configuration</NText>
      <div class="config-form">
        <div
          v-for="prop in configProperties"
          :key="prop.key"
          class="config-field"
        >
          <div class="config-field-header">
            <NText strong class="config-field-title">{{ prop.title }}</NText>
            <NText v-if="prop.required" depth="3" class="config-field-required"
              >required</NText
            >
          </div>
          <NText v-if="prop.description" depth="3" class="config-field-desc">{{
            prop.description
          }}</NText>
          <NInput
            :value="configEdits[prop.key] ?? prop.currentValue"
            @update:value="(v: string) => (configEdits[prop.key] = v)"
            :placeholder="prop.currentValue || `Enter ${prop.title}`"
            size="small"
          />
        </div>
        <NButton
          type="primary"
          size="small"
          :loading="configSaving"
          :disabled="Object.keys(configEdits).length === 0"
          @click="saveConfig"
        >
          Save Config
        </NButton>
      </div>
    </div>

    <div class="info-section">
      <NText strong class="info-heading">Implementation</NText>
      <div class="info-box">
        <div class="info-row">
          <NText depth="3" class="info-key">Class</NText>
          <NText code class="info-val">{{ tool.implementation || "—" }}</NText>
        </div>
        <template v-if="configEntries.length > 0">
          <NDivider style="margin: 8px 0" />
          <NText depth="3" class="info-key">Configuration</NText>
          <NScrollbar class="config-scroll">
            <div class="config-entries">
              <div
                v-for="entry in configEntries"
                :key="entry.path"
                class="config-row"
              >
                <NText code class="config-path">{{ entry.path }}</NText>
                <NText class="config-value">{{ entry.value }}</NText>
              </div>
            </div>
          </NScrollbar>
        </template>
      </div>
    </div>
  </div>

  <div class="detail-view" v-else>
    <NText>Tool "{{ toolName }}" not found.</NText>
    <NButton @click="router.push({ name: 'tools' })">Back to catalog</NButton>
  </div>
  </NScrollbar>
</template>

<style scoped>
.detail-scroll {
  flex: 1;
  overflow: hidden;
}

.detail-view {
  padding: 32px;
  max-width: 800px;
  width: 100%;
}

.detail-header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 16px;
}

.detail-title-area {
  flex: 1;
}

.detail-title-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 10px;
}

.detail-name {
  font-family: monospace;
  font-size: 1.4em;
  font-weight: 700;
}

.detail-controls {
  display: flex;
  align-items: center;
  gap: 20px;
}

.control-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.control-label {
  font-size: 0.85em;
}

.detail-badge {
  font-size: 0.75em;
  padding: 2px 6px;
  border-radius: 3px;
  background-color: var(--n-primary-color-suppl, #e8f5e9);
  color: var(--n-primary-color);
  margin-left: 8px;
}

.detail-desc {
  font-size: 1.05em;
  line-height: 1.5;
  display: block;
}

.desc-section {
  margin-top: 8px;
}

.desc-display {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.desc-display .detail-desc {
  flex: 1;
}

.desc-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.desc-edit-btn {
  color: var(--n-text-color-3, #999);
}

.desc-edit-btn:hover {
  color: var(--n-primary-color, #18a058);
}

.desc-edit-section {
  margin-top: 8px;
}

.desc-edit-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.param-section {
  margin-bottom: 20px;
}

.param-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}

.param-card {
  padding: 10px 14px;
  border: 1px solid var(--n-border-color, #e0e0e0);
  border-radius: 6px;
}

.param-name {
  font-family: monospace;
  margin-right: 8px;
}

.param-type {
  font-size: 0.85em;
  font-family: monospace;
}

.param-desc {
  display: block;
  font-size: 0.9em;
  margin-top: 4px;
}

.param-default {
  display: block;
  font-size: 0.85em;
  margin-top: 2px;
}

.config-section {
  margin-bottom: 20px;
}

.config-heading {
  display: block;
  margin-bottom: 10px;
}

.config-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.config-field-header {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.config-field-title {
  font-size: 0.9em;
}

.config-field-required {
  font-size: 0.75em;
  font-style: italic;
}

.config-field-desc {
  display: block;
  font-size: 0.85em;
  margin-bottom: 4px;
}

.info-section {
  margin-bottom: 20px;
}

.info-heading {
  display: block;
  margin-bottom: 8px;
}

.info-box {
  padding: 14px 16px;
  border: 1px solid var(--n-border-color, #e0e0e0);
  border-radius: 6px;
  background-color: var(--n-body-color, #fafafa);
}

.info-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.info-key {
  font-size: 0.85em;
  min-width: 90px;
  flex-shrink: 0;
}

.info-val {
  font-size: 0.9em;
  word-break: break-all;
}

.config-scroll {
  max-height: 200px;
  margin-top: 6px;
}

.config-entries {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.config-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.config-path {
  font-size: 0.85em;
  min-width: 140px;
  flex-shrink: 0;
  color: var(--n-text-color-3);
}

.config-value {
  font-size: 0.85em;
  word-break: break-all;
}
</style>
