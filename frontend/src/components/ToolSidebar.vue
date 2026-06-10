<script setup lang="ts">
import { NScrollbar, NButton, NText, NSwitch } from "naive-ui";
import {
  IconPlus,
  IconChevronRight,
  IconChevronDown,
  IconTool,
  IconCheck,
  IconX,
  IconMinus,
} from "@tabler/icons-vue";
import { useToolsStore } from "../stores/tools";
import { useRouter } from "vue-router";
import { ref, onMounted, computed } from "vue";

const store = useToolsStore();
const router = useRouter();

onMounted(() => {
  store.fetchTools();
});

const collapsed = ref<Set<string>>(new Set());
const togglingGroup = ref<string | null>(null);

function toggleGroupCollapse(group: string) {
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

function openGroup(group: string) {
  router.push({ name: "tool-group", params: { name: group } });
}

async function onToggleEnabled(name: string, enabled: boolean) {
  await store.toggleEnabled(name, enabled);
}

function groupEnabledCount(tools: { enabled: boolean }[]): number {
  return tools.filter((t) => t.enabled).length;
}

async function onToggleGroup(
  group: string,
  tools: { enabled: boolean; name: string }[],
) {
  const enabledCount = tools.filter((t) => t.enabled).length;
  const enable = enabledCount === 0;
  const names = tools.filter((t) => t.enabled !== enable).map((t) => t.name);
  togglingGroup.value = group;
  try {
    if (names.length > 0) {
      await store.bulkToggleEnabled(names, enable);
    }
  } finally {
    togglingGroup.value = null;
  }
}

const groupCounts = computed(() => {
  const counts: Record<string, { enabled: number; total: number }> = {};
  for (const [group, tools] of store.groups) {
    counts[group] = {
      enabled: tools.filter((t) => t.enabled).length,
      total: tools.length,
    };
  }
  return counts;
});
</script>

<template>
  <NScrollbar>
    <div style="padding: 16px">
      <NButton
        type="primary"
        block
        @click="router.push({ name: 'tool-ingest' })"
        style="margin-bottom: 16px"
      >
        <template #icon>
          <IconPlus />
        </template>
        Import tools from API
      </NButton>

      <div
        v-for="[group, tools] of store.groups"
        :key="group"
        class="tool-group"
      >
        <div class="group-header" @click="openGroup(group)">
          <IconChevronDown
            v-if="!collapsed.has(group)"
            :size="16"
            class="group-chevron"
            @click.stop="toggleGroupCollapse(group)"
          />
          <IconChevronRight
            v-else
            :size="16"
            class="group-chevron"
            @click.stop="toggleGroupCollapse(group)"
          />
          <NText
            strong
            class="group-name"
            >{{ tools[0]?.groupDisplayName || group }}</NText
          >
          <NText depth="3" class="group-count">
            ({{ groupCounts[group]?.enabled ?? 0 }}/{{
              groupCounts[group]?.total ?? 0
            }})
          </NText>
          <NSwitch
            :value="(groupCounts[group]?.enabled ?? 0) > 0"
            :loading="togglingGroup === group"
            size="small"
            class="group-toggle"
            @update:value="onToggleGroup(group, tools)"
            @click.stop
          >
            <template #icon>
              <IconCheck v-if="groupCounts[group]?.enabled === groupCounts[group]?.total" :size="10" />
              <IconX v-else-if="(groupCounts[group]?.enabled ?? 0) === 0" :size="10" />
              <IconMinus v-else :size="10" />
            </template>
          </NSwitch>
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
  border-radius: 4px;
  user-select: none;
  cursor: pointer;
  transition: background-color 0.15s;
}

.group-header:hover {
  background-color: var(--moira-border, #e0e0e0);
}

.group-chevron {
  flex-shrink: 0;
  cursor: pointer;
  padding: 4px;
  border-radius: 50%;
  transition: background-color 0.15s;
}

.group-chevron:hover {
  background-color: rgba(0, 0, 0, 0.08);
}

.group-name {
  flex-shrink: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.group-count {
  font-size: 0.85em;
  flex-shrink: 0;
  margin-left: auto;
}

.group-toggle {
  flex-shrink: 0;
  --n-height-small: 14px;
  --n-rail-height-small: 14px;
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
