<script setup lang="ts">
import { ref, onMounted, computed } from "vue";
import {
  NText,
  NSlider,
  NInputNumber,
  NSpin,
  NButton,
  NTooltip,
  NPopover,
  useDialog,
  useMessage,
} from "naive-ui";
import { IconSettings, IconRefresh, IconInfoCircle } from "@tabler/icons-vue";
import type { SettingDefinition, SettingEntry } from "../api/client";
import { api } from "../api/client";

const message = useMessage();
const dialog = useDialog();

const loading = ref(true);
const definitions = ref<SettingDefinition[]>([]);
const settings = ref<Map<string, SettingEntry>>(new Map());
const saving = ref<Map<string, boolean>>(new Map());
const errors = ref<Map<string, string>>(new Map());

const budgetGroup = ref<SettingDefinition[]>([]);

const isCostKey = (key: string) => key.startsWith("budget.cost.");

function getMax(key: string): number {
  const constraints = getConstraints(key);
  return (constraints.maximum as number) ?? 25;
}

onMounted(async () => {
  try {
    const [defResp, settingsResp] = await Promise.all([
      api.getSettingDefinitions(),
      api.getSettings("budget"),
    ]);
    definitions.value = defResp.definitions;
    budgetGroup.value = defResp.definitions.filter((d) => d.group === "budget");

    const map = new Map<string, SettingEntry>();
    for (const s of settingsResp.settings) {
      map.set(s.key, s);
    }
    settings.value = map;
  } catch (e) {
    message.error("Failed to load settings");
  } finally {
    loading.value = false;
  }
});

function getValue(key: string): number {
  const entry = settings.value.get(key);
  if (entry) return parseInt(entry.value, 10);
  const defn = definitions.value.find((d) => d.key === key);
  return defn ? parseInt(defn.default, 10) : 0;
}

function getConstraints(key: string) {
  const defn = definitions.value.find((d) => d.key === key);
  return defn?.constraints || {};
}

function shortLabel(label: string): string {
  return label.replace(" Cost Weight", "").replace("Default Budget", "Budget");
}

async function saveSetting(key: string, value: number) {
  const constraints = getConstraints(key);
  const min = (constraints.minimum as number) ?? 0;
  const max = (constraints.maximum as number) ?? Infinity;

  if (value < min || value > max) {
    errors.value.set(key, `Value must be between ${min} and ${max}`);
    return;
  }

  errors.value.delete(key);
  saving.value.set(key, true);

  try {
    const updated = await api.setSetting(key, value);
    settings.value.set(key, updated);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "Failed to save";
    errors.value.set(key, msg);
    message.error(`Failed to save ${key}: ${msg}`);
  } finally {
    saving.value.delete(key);
  }
}

async function resetBudget() {
  dialog.warning({
    title: "Reset Budget Settings",
    content: "Reset all budget settings to their default values?",
    positiveText: "Reset",
    negativeText: "Cancel",
    onPositiveClick: async () => {
      try {
        const resp = await api.resetSettings(
          budgetGroup.value.map((d) => d.key),
        );
        const map = new Map(settings.value);
        for (const s of resp.settings) {
          map.set(s.key, s);
        }
        settings.value = map;
        message.success("Budget settings reset to defaults");
      } catch {
        message.error("Failed to reset settings");
      }
    },
  });
}
</script>

<template>
  <div class="settings-system">
    <div class="section-header">
      <IconSettings :size="24" class="section-icon" />
      <NText class="section-title">System</NText>
    </div>

    <NSpin :show="loading">
      <div v-if="!loading" class="settings-body">
        <div class="settings-group">
          <div class="group-header">
            <NText class="group-title">Budget</NText>
            <NTooltip>
              <template #trigger>
                <NButton quaternary size="small" @click="resetBudget">
                  <template #icon>
                    <IconRefresh :size="16" />
                  </template>
                </NButton>
              </template>
              Reset to defaults
            </NTooltip>
          </div>

          <div class="setting-row" v-for="defn in budgetGroup" :key="defn.key">
            <NText class="setting-label">{{ shortLabel(defn.label) }}</NText>
            <NPopover trigger="click" :width="240" placement="top">
              <template #trigger>
                <IconInfoCircle :size="14" class="info-icon" />
              </template>
              {{ defn.description }}
            </NPopover>

            <div class="setting-slider">
              <NSlider
                :value="getValue(defn.key)"
                :min="(getConstraints(defn.key).minimum as number) ?? 0"
                :max="getMax(defn.key)"
                :step="1"
                @update:value="(v: number) => saveSetting(defn.key, v)"
              />
            </div>
            <NInputNumber
              :value="getValue(defn.key)"
              :min="(getConstraints(defn.key).minimum as number) ?? 0"
              :max="getMax(defn.key)"
              size="tiny"
              :show-button="false"
              class="setting-input"
              @update:value="(v: number | null) => v != null && saveSetting(defn.key, v)"
            />
          </div>
        </div>
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.settings-system {
  padding: 20px;
  max-width: 600px;
  width: 100%;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.section-icon {
  color: var(--n-primary-color);
}

.section-title {
  font-size: 1.2em;
  font-weight: 600;
}

.settings-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.settings-group {
  display: flex;
  flex-direction: column;
}

.group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 4px;
  margin-bottom: 4px;
  border-bottom: 1px solid var(--n-border-color);
}

.group-title {
  font-size: 1em;
  font-weight: 600;
}

.setting-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
}

.setting-label {
  flex-shrink: 0;
  width: 140px;
  font-size: 0.9em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.info-icon {
  flex-shrink: 0;
  color: var(--n-text-color-disabled);
  cursor: pointer;
}

.setting-slider {
  flex: 1;
  min-width: 0;
}

.setting-input {
  flex-shrink: 0;
  width: 56px;
}

.setting-input :deep(.n-input) {
  --n-padding-single: 0 6px;
  font-variant-numeric: tabular-nums;
  font-size: 0.85em;
  text-align: center;
}
</style>
