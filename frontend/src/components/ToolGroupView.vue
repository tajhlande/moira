<script setup lang="ts">
import {
  NText,
  NButton,
  NInput,
  NSwitch,
  NDivider,
  NScrollbar,
  useMessage,
  useDialog,
} from "naive-ui";
import { IconArrowLeft, IconTool, IconTrash, IconCheck, IconX, IconMinus } from "@tabler/icons-vue";
import { useToolsStore, type ToolDefinition } from "../stores/tools";
import { useRoute, useRouter } from "vue-router";
import { computed, ref } from "vue";
import { api } from "../api/client";

const store = useToolsStore();
const route = useRoute();
const router = useRouter();
const message = useMessage();
const dialog = useDialog();

const groupName = computed(() => route.params.name as string);
const isProtected = computed(() => groupName.value === "standard");
const groupTools = computed(() => store.groups.get(groupName.value) ?? []);
const groupDisplayName = computed(
  () => groupTools.value[0]?.groupDisplayName || groupName.value,
);

const enabledCount = computed(() =>
  groupTools.value.filter((t) => t.enabled).length,
);

const editingName = ref(false);
const nameInput = ref("");
const saving = ref(false);
const deleting = ref(false);

function selectTool(name: string) {
  router.push({ name: "tool-detail", params: { name } });
}

async function onToggleTool(name: string, enabled: boolean) {
  try {
    await store.toggleEnabled(name, enabled);
  } catch {
    // Tool may have been removed
  }
}

async function onToggleAll() {
  const enable = enabledCount.value === 0;
  const names = groupTools.value
    .filter((t) => t.enabled !== enable)
    .map((t) => t.name);
  if (names.length > 0) {
    await store.bulkToggleEnabled(names, enable);
  }
}

function startRename() {
  nameInput.value = groupDisplayName.value;
  editingName.value = true;
}

function cancelRename() {
  editingName.value = false;
  nameInput.value = "";
}

async function confirmRename() {
  if (!nameInput.value.trim()) return;
  saving.value = true;
  try {
    const result = await api.renameToolGroup(
      groupName.value,
      nameInput.value.trim(),
    );
    editingName.value = false;
    await store.refreshTools();
    if (result.name !== groupName.value) {
      router.replace({ name: "tool-group", params: { name: result.name } });
    }
  } catch (e: any) {
    const detail = e instanceof Error ? e.message : "Failed to rename group";
    if (e?.message?.includes("409") || detail.includes("merge")) {
      dialog.warning({
        title: "Merge groups?",
        content: `Renaming to "${nameInput.value.trim()}" would merge these tools with an existing group. Continue?`,
        positiveText: "Merge",
        negativeText: "Cancel",
        onPositiveClick: async () => {
          try {
            const result = await api.renameToolGroup(
              groupName.value,
              nameInput.value.trim(),
            );
            editingName.value = false;
            await store.refreshTools();
            if (result.name !== groupName.value) {
              router.replace({
                name: "tool-group",
                params: { name: result.name },
              });
            }
          } catch (e2) {
            message.error(
              e2 instanceof Error ? e2.message : "Merge failed",
            );
          }
        },
      });
    } else {
      message.error(detail);
    }
  } finally {
    saving.value = false;
  }
}

function confirmDelete() {
  dialog.error({
    title: "Delete group?",
    content: `This will permanently delete all ${groupTools.value.length} tools in "${groupDisplayName.value}" and remove them from the search index. Restoring them will require reimporting them from the source API. This cannot be undone.`,
    positiveText: "Delete all",
    negativeText: "Cancel",
    onPositiveClick: doDelete,
  });
}

async function doDelete() {
  deleting.value = true;
  try {
    await api.deleteToolGroup(groupName.value);
    message.success("Group deleted");
    await store.refreshTools();
    router.push({ name: "tools" });
  } catch (e) {
    message.error(e instanceof Error ? e.message : "Failed to delete group");
  } finally {
    deleting.value = false;
  }
}

function confirmDeleteTool(tool: ToolDefinition) {
  dialog.error({
    title: "Delete tool?",
    content: `Permanently delete "${tool.name}"? Restoring it will require reimporting from the source API. This cannot be undone.`,
    positiveText: "Delete",
    negativeText: "Cancel",
    onPositiveClick: () => doDeleteTool(tool.name),
  });
}

async function doDeleteTool(name: string) {
  try {
    await api.deleteTool(name);
    message.success("Tool deleted");
    await store.refreshTools();
  } catch (e) {
    message.error(e instanceof Error ? e.message : "Failed to delete tool");
  }
}
</script>

<template>
  <NScrollbar class="group-scroll">
    <div class="group-view" v-if="groupTools.length > 0">
      <div class="group-header">
        <NButton quaternary circle @click="router.push({ name: 'tools' })">
          <template #icon>
            <IconArrowLeft />
          </template>
        </NButton>
        <div class="group-title-area">
          <div v-if="!editingName" class="group-title-row">
            <NText class="group-title">Tool Group: </NText>
            <NText class="group-title group-name-text">{{ groupDisplayName }}</NText>
            <NButton v-if="!isProtected" strong secondary type="primary" size="tiny" @click="startRename">Rename</NButton>
          </div>
          <div v-else class="group-rename-row">
            <NText class="group-title">Tool Group: </NText>
            <NInput
              v-model:value="nameInput"
              size="small"
              placeholder="Group name"
              class="rename-input"
              @keyup.enter="confirmRename"
              @keyup.escape="cancelRename"
            />
            <NButton size="small" type="primary" :loading="saving" @click="confirmRename"
              >Save</NButton
            >
            <NButton size="small" @click="cancelRename">Cancel</NButton>
          </div>
          <div class="group-controls">
            <div class="control-item">
              <NText depth="3" class="control-label">All enabled</NText>
              <NSwitch
                :value="enabledCount > 0"
                @update:value="onToggleAll"
              >
                <template #icon>
                  <IconCheck v-if="enabledCount === groupTools.length" :size="12" />
                  <IconX v-else-if="enabledCount === 0" :size="12" />
                  <IconMinus v-else :size="12" />
                </template>
              </NSwitch>
            </div>
            <NText depth="3" class="control-stats">
              {{ enabledCount }}/{{ groupTools.length }} enabled
            </NText>
          </div>
        </div>
      </div>

      <div class="tool-list">
        <div
          v-for="tool in groupTools"
          :key="tool.name"
          class="tool-row"
          @click="selectTool(tool.name)"
        >
          <IconTool :size="16" class="tool-icon" />
          <div class="tool-info">
            <NText class="tool-name">{{ tool.name }}</NText>
            <NText depth="3" class="tool-desc">{{
              tool.description.slice(0, 1000)
            }}</NText>
          </div>
          <NSwitch
            :value="tool.enabled"
            @update:value="(v: boolean) => onToggleTool(tool.name, v)"
            size="small"
            class="tool-switch"
            @click.stop
          />
          <NButton
            v-if="!isProtected"
            quaternary
            circle
            size="tiny"
            type="error"
            class="tool-delete-btn"
            @click.stop="confirmDeleteTool(tool)"
          >
            <template #icon>
              <IconTrash :size="14" />
            </template>
          </NButton>
        </div>
      </div>

      <NDivider v-if="!isProtected" />

      <div v-if="!isProtected" class="danger-section">
        <NButton
          type="error"
          ghost
          :loading="deleting"
          @click="confirmDelete"
        >
          <template #icon>
            <IconTrash :size="16" />
          </template>
          Delete group and all tools
        </NButton>
      </div>
    </div>

    <div class="group-view" v-else>
      <NText>Group "{{ groupName }}" not found.</NText>
      <NButton @click="router.push({ name: 'tools' })"
        >Back to catalog</NButton
      >
    </div>
  </NScrollbar>
</template>

<style scoped>
.group-scroll {
  flex: 1;
  overflow: hidden;
}

.group-view {
  padding: 32px;
  max-width: 800px;
  width: 100%;
  box-sizing: border-box;
}

.group-header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 16px;
}

.group-title-area {
  flex: 1;
}

.group-title {
  font-size: 1.4em;
  font-weight: 700;
}

.group-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.group-title-row .n-button {
  margin-left: 8px;
}

.group-name-text {
  color: var(--n-text-color);
}

.group-rename-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.rename-input {
  max-width: 300px;
}

.group-controls {
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

.control-stats {
  font-size: 0.85em;
}

.tool-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tool-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid var(--n-border-color, #e0e0e0);
  border-radius: 6px;
  cursor: pointer;
  overflow: hidden;
}

.tool-row:hover {
  background-color: var(--moira-border, #e0e0e0);
}

.tool-icon {
  flex-shrink: 0;
  opacity: 0.6;
}

.tool-info {
  flex: 1;
  min-width: 0;
}

.tool-name {
  font-family: monospace;
  font-weight: 600;
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-desc {
  font-size: 0.85em;
  display: block;
  overflow: hidden;
  word-break: break-word;
}

.tool-switch {
  flex-shrink: 0;
  --n-height-small: 14px;
  --n-rail-height-small: 14px;
}

.tool-delete-btn {
  flex-shrink: 0;
}

.danger-section {
  display: flex;
  justify-content: flex-end;
}
</style>
