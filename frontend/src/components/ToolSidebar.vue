<script setup lang="ts">
import { NScrollbar, NButton, NText, NSwitch } from "naive-ui";
import { IconPlus, IconChevronRight, IconChevronDown, IconTool } from "@tabler/icons-vue";
import { useToolsStore } from "../stores/tools";
import { useRouter } from "vue-router";
import { ref, onMounted } from "vue";

const store = useToolsStore();
const router = useRouter();

onMounted(() => {
  store.fetchTools();
});

const collapsed = ref<Set<string>>(new Set());

function toggleGroup(group: string) {
  const next = new Set(collapsed.value);
  if (next.has(group)) {
    next.delete(group);
  } else {
    next.add(group);
  }
  collapsed.value = next;
}

function selectTool(name: string) {
  store.selectTool(name);
  router.push({ name: "tool-detail", params: { name } });
}

async function onToggleEnabled(name: string, enabled: boolean) {
  await store.toggleEnabled(name, enabled);
}
</script>

<template>
  <NScrollbar>
    <div style="padding: 16px">
      <NButton
        type="primary"
        block
        @click="router.push({ name: 'tool-new' })"
        style="margin-bottom: 16px"
      >
        <template #icon>
          <IconPlus />
        </template>
        Add Tool
      </NButton>

      <div
        v-for="[group, tools] of store.groups"
        :key="group"
        class="tool-group"
      >
        <div class="group-header" @click="toggleGroup(group)">
          <IconChevronDown v-if="!collapsed.has(group)" :size="16" class="group-chevron" />
          <IconChevronRight v-else :size="16" class="group-chevron" />
          <NText strong>{{ tools[0]?.groupDisplayName || group }}</NText>
          <NText depth="3" class="group-count">({{ tools.length }})</NText>
        </div>
        <div v-if="!collapsed.has(group)" class="group-tools">
          <div
            v-for="tool in tools"
            :key="tool.name"
            :class="[
              'tool-item',
              { active: store.selectedToolName === tool.name },
            ]"
          >
            <IconTool :size="16" class="tool-icon" />
            <span class="tool-name" @click="selectTool(tool.name)">{{
              tool.name
            }}</span>
            <NSwitch
              :value="tool.enabled"
              @update:value="(v: boolean) => onToggleEnabled(tool.name, v)"
              size="small"
              class="tool-switch"
              @click.stop
            />
          </div>
        </div>
      </div>
    </div>
  </NScrollbar>
</template>

<style scoped>
.tool-group {
  margin-bottom: 4px;
}

.group-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 8px;
  cursor: pointer;
  border-radius: 4px;
  user-select: none;
}

.group-header:hover {
  background-color: var(--moira-border, #e0e0e0);
}

.group-chevron {
  flex-shrink: 0;
}

.group-count {
  margin-left: auto;
  font-size: 0.85em;
}

.group-tools {
  padding-left: 12px;
}

.tool-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 4px;
  cursor: pointer;
}

.tool-item:hover {
  background-color: var(--moira-border, #e0e0e0);
}

.tool-item.active {
  background-color: var(--moira-border, #e0e0e0);
}

.tool-icon {
  flex-shrink: 0;
  opacity: 0.6;
}

.tool-name {
  font-family: monospace;
  font-size: 0.9em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  cursor: pointer;
}

.tool-switch {
  flex-shrink: 0;
  --n-height-small: 14px;
  --n-rail-height-small: 14px;
}
</style>
