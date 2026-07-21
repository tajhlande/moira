<script setup lang="ts">
import { ref, computed, watch } from "vue";
import {
  NPopover,
  NInput,
  NText,
  NButton,
  NScrollbar,
  NSpin,
  NSwitch,
  NIcon,
} from "naive-ui";
import {
  IconArrowBackUp,
  IconCircleCheck,
  IconBolt,
  IconBoltOff,
  IconInfoCircle,
} from "@tabler/icons-vue";
import {
  api,
  type InferenceProvider,
  type InferenceModel,
} from "../api/client";
import ModelPill from "./ModelPill.vue";

const props = defineProps<{ conversationId: string }>();

const emit = defineEmits<{ (e: "changed"): void }>();

/** When true, we're on the new-chat screen with no conversation yet.
 *  Model selection edits the global default instead of a per-conversation
 *  override. */
const isGlobal = computed(() => !props.conversationId);

const loading = ref(false);
const saving = ref(false);
const showPopover = ref(false);
const searchText = ref("");

const providers = ref<InferenceProvider[]>([]);
const models = ref<InferenceModel[]>([]);
const currentEndpoint = ref("");
const currentModel = ref("");
const overridden = ref(false);
const nativeToolCalling = ref(false);

/** Preserved so global-default mode can round-trip task assignment. */
const taskEndpoint = ref("");
const taskModel = ref("");

/** Whether the current model exists in the discovered models list. */
const currentModelExists = computed(() => {
  if (!currentEndpoint.value || !currentModel.value) return false;
  return models.value.some(
    (m) => m.provider === currentEndpoint.value && m.id === currentModel.value,
  );
});

const providerMap = computed(() => {
  const map = new Map<string, string>();
  for (const p of providers.value) {
    map.set(p.slug, p.display_name);
  }
  return map;
});

const currentProviderName = computed(
  () => providerMap.value.get(currentEndpoint.value) || currentEndpoint.value,
);

interface GroupedModel {
  providerSlug: string;
  providerName: string;
  models: InferenceModel[];
}

const filteredGroups = computed<GroupedModel[]>(() => {
  const q = searchText.value.toLowerCase().trim();
  const tokens = q ? q.split(/\s+/) : [];
  const byProvider = new Map<string, InferenceModel[]>();
  for (const m of models.value) {
    if (tokens.length > 0) {
      const pName = (
        providerMap.value.get(m.provider) || m.provider
      ).toLowerCase();
      const combined = `${pName} ${m.id.toLowerCase()}`;
      if (!tokens.every((t) => combined.includes(t))) {
        continue;
      }
    }
    if (!byProvider.has(m.provider)) {
      byProvider.set(m.provider, []);
    }
    byProvider.get(m.provider)!.push(m);
  }

  const groups: GroupedModel[] = [];
  for (const [slug, mods] of byProvider) {
    groups.push({
      providerSlug: slug,
      providerName: providerMap.value.get(slug) || slug,
      models: mods,
    });
  }
  groups.sort((a, b) => a.providerName.localeCompare(b.providerName));
  return groups;
});

function isSelected(providerSlug: string, modelId: string): boolean {
  return (
    providerSlug === currentEndpoint.value && modelId === currentModel.value
  );
}

function readCapability(providerSlug: string, modelId: string): boolean {
  const m = models.value.find(
    (m) => m.provider === providerSlug && m.id === modelId,
  );
  return m?.native_tool_calling ?? false;
}

async function loadData() {
  loading.value = true;
  try {
    const [provResp, modelResp] = await Promise.all([
      api.getInferenceProviders(),
      api.getInferenceModels(),
    ]);
    providers.value = provResp.providers;
    models.value = modelResp.models;
    taskEndpoint.value = modelResp.assignments.task.endpoint;
    taskModel.value = modelResp.assignments.task.model;

    if (isGlobal.value) {
      // New-chat screen: use global default from model assignments
      currentEndpoint.value = modelResp.assignments.intelligence.endpoint;
      currentModel.value = modelResp.assignments.intelligence.model;
      overridden.value = false;
    } else {
      // Existing conversation: check per-conversation override
      const convModel = await api.getConversationModel(props.conversationId);
      currentEndpoint.value = convModel.endpoint;
      currentModel.value = convModel.model;
      overridden.value = convModel.overridden;
    }
    nativeToolCalling.value = readCapability(
      currentEndpoint.value,
      currentModel.value,
    );
  } finally {
    loading.value = false;
  }
}

async function selectModel(providerSlug: string, modelId: string) {
  saving.value = true;
  try {
    if (isGlobal.value) {
      // Set global default — preserve existing task assignment
      await api.setModelAssignments({
        intelligence: { endpoint: providerSlug, model: modelId },
        task: { endpoint: taskEndpoint.value, model: taskModel.value },
      });
    } else {
      await api.setConversationModel(
        props.conversationId,
        providerSlug,
        modelId,
      );
      overridden.value = true;
    }
    currentEndpoint.value = providerSlug;
    currentModel.value = modelId;
    nativeToolCalling.value = readCapability(providerSlug, modelId);
    searchText.value = "";
    emit("changed");
  } finally {
    saving.value = false;
  }
}

async function toggleNativeToolCalling(value: boolean) {
  // Note: we intentionally do NOT set saving=true here. The saving flag
  // triggers a v-if/v-else swap (NSpin ↔ NScrollbar) that destroys and
  // recreates the switch mid-animation, causing a visual blink. The toggle
  // is a lightweight capability update — the switch's :value binding handles
  // optimistic update / revert naturally.
  try {
    await api.setModelCapability(
      currentEndpoint.value,
      currentModel.value,
      value,
    );
    nativeToolCalling.value = value;
    // Update the models list entry so the list below stays in sync
    const m = models.value.find(
      (m) =>
        m.provider === currentEndpoint.value && m.id === currentModel.value,
    );
    if (m) m.native_tool_calling = value;
    emit("changed");
  } catch {
    // On failure, nativeToolCalling is unchanged — the switch reverts.
  }
}

async function resetToDefault() {
  saving.value = true;
  try {
    await api.resetConversationModel(props.conversationId);
    overridden.value = false;
    const convModel = await api.getConversationModel(props.conversationId);
    currentEndpoint.value = convModel.endpoint;
    currentModel.value = convModel.model;
    nativeToolCalling.value = readCapability(
      convModel.endpoint,
      convModel.model,
    );
    showPopover.value = false;
    emit("changed");
  } finally {
    saving.value = false;
  }
}

watch(
  () => props.conversationId,
  () => {
    loadData();
  },
  { immediate: true },
);
</script>

<template>
  <NPopover
    v-model:show="showPopover"
    trigger="click"
    placement="bottom"
    :width="340"
  >
    <template #trigger>
      <ModelPill
        :provider-name="currentProviderName"
        :model-id="currentModel"
        :overridden="overridden"
        :native-tool-calling="nativeToolCalling"
      />
    </template>

    <div class="model-popover">
      <NInput
        v-model:value="searchText"
        placeholder="Search models..."
        size="small"
        clearable
        class="search-input"
      />
      <NSpin v-if="loading || saving" size="small" class="popover-spin" />
      <NScrollbar v-else style="max-height: 340px">
        <!-- Pinned current selection with native tool calling toggle -->
        <template v-if="currentModelExists">
          <div class="group-header">{{ currentProviderName }}</div>
          <div class="model-option active current-selection">
            <span class="model-id">{{ currentModel }}</span>
            <div class="toggle-group">
              <NPopover trigger="hover" placement="top" :width="260">
                <template #trigger>
                  <IconInfoCircle :size="14" class="info-icon" />
                </template>
                <div class="toggle-help">
                  Choose between native
                  <IconBolt :size="14" class="help-icon-native" />
                  and emulated
                  <IconBoltOff :size="14" class="help-icon-emulated" />
                  tool calling
                </div>
              </NPopover>
              <NSwitch
                :value="nativeToolCalling"
                size="small"
                @update:value="toggleNativeToolCalling"
              >
                <template #checked-icon>
                  <NIcon :component="IconBolt" :size="14" />
                </template>
                <template #unchecked-icon>
                  <NIcon :component="IconBoltOff" :size="14" />
                </template>
              </NSwitch>
            </div>
          </div>
          <div class="section-divider" />
        </template>

        <!-- Full model list grouped by provider -->
        <template v-for="group in filteredGroups" :key="group.providerSlug">
          <div class="group-header">{{ group.providerName }}</div>
          <div
            v-for="m in group.models"
            :key="`${group.providerSlug}::${m.id}`"
            class="model-option"
            :class="{ active: isSelected(group.providerSlug, m.id) }"
            @click="selectModel(group.providerSlug, m.id)"
          >
            <span class="model-id">{{ m.id }}</span>
            <IconCircleCheck
              v-if="isSelected(group.providerSlug, m.id)"
              :size="14"
              class="check-icon"
            />
          </div>
        </template>
        <NText v-if="filteredGroups.length === 0" depth="3" class="empty-text">
          No models found.
        </NText>
      </NScrollbar>
      <div v-if="overridden" class="reset-row">
        <NButton
          size="tiny"
          quaternary
          :loading="saving"
          @click="resetToDefault"
        >
          <template #icon>
            <IconArrowBackUp :size="14" />
          </template>
          Reset to default
        </NButton>
      </div>
    </div>
  </NPopover>
</template>

<style scoped>
.model-popover {
  padding: 4px;
}

.search-input {
  margin-bottom: 8px;
}

.popover-spin {
  display: flex;
  justify-content: center;
  padding: 24px;
}

.group-header {
  font-size: 0.75em;
  font-weight: 600;
  opacity: 0.6;
  padding: 8px 8px 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.model-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.85em;
}

.model-option:hover {
  background: var(--n-color-hover, rgba(0, 0, 0, 0.04));
}

.model-option.active {
  font-weight: 600;
  color: var(--n-primary-color, #18a058);
}

.model-id {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.check-icon {
  flex-shrink: 0;
  color: var(--n-primary-color, #18a058);
}

.empty-text {
  display: block;
  text-align: center;
  padding: 16px;
  font-size: 0.85em;
}

.reset-row {
  border-top: 1px solid var(--moira-border, #e0e0e0);
  padding-top: 4px;
  margin-top: 4px;
  display: flex;
  justify-content: center;
}

.section-divider {
  height: 1px;
  background: var(--moira-border, #e0e0e0);
  margin: 6px 0;
}

.toggle-group {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.info-icon {
  flex-shrink: 0;
  color: var(--n-text-color-disabled, #999);
  cursor: help;
}

.toggle-help {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.help-icon-native {
  color: var(--n-primary-color, #18a058);
}

.help-icon-emulated {
  color: var(--n-text-color-3, #999);
}
</style>
