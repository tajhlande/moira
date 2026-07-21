<script setup lang="ts">
import { ref, onMounted, computed } from "vue";
import {
  NText,
  NSpin,
  NButton,
  NInput,
  NSelect,
  NSwitch,
  NIcon,
  NCard,
  NModal,
  NForm,
  NFormItem,
  NSpace,
  NTag,
  NAlert,
  NPopconfirm,
  useMessage,
} from "naive-ui";
import {
  IconCpu,
  IconRefresh,
  IconPlus,
  IconTrash,
  IconEdit,
  IconBolt,
  IconBoltOff,
} from "@tabler/icons-vue";
import type {
  InferenceProvider,
  InferenceModel,
  ModelAssignments,
} from "../api/client";
import { api } from "../api/client";

const message = useMessage();

const loading = ref(true);
const providers = ref<InferenceProvider[]>([]);
const models = ref<InferenceModel[]>([]);
const assignments = ref<ModelAssignments>({
  intelligence: { endpoint: "", model: "" },
  task: { endpoint: "", model: "" },
});
const refreshing = ref(false);

// Add/edit provider modal state
const showProviderModal = ref(false);
const editingProvider = ref<string | null>(null); // null = add mode, slug = edit mode
const providerForm = ref({
  display_name: "",
  base_url: "",
  api_key: "",
  provider_type: "completions",
});
const validating = ref(false);
const validationError = ref("");

const hasProviders = computed(() => providers.value.length > 0);

// Provider dropdown options (same for both roles)
const providerOptions = computed(() =>
  providers.value.map((p) => ({ label: p.display_name, value: p.slug })),
);

// Model dropdown options for a given provider
function modelOptionsFor(providerName: string) {
  return models.value
    .filter((m) => m.provider === providerName)
    .map((m) => ({ label: m.id, value: m.id }));
}

// The native_tool_calling flag for the currently selected intelligence model
const intelligenceToolCalling = computed(() => {
  const a = assignments.value.intelligence;
  return (
    models.value.find((m) => m.provider === a.endpoint && m.id === a.model)
      ?.native_tool_calling ?? false
  );
});

onMounted(async () => {
  await loadAll();
});

async function loadAll() {
  loading.value = true;
  try {
    const [providersResp, modelsResp] = await Promise.all([
      api.getInferenceProviders(),
      api.getInferenceModels(),
    ]);
    providers.value = providersResp.providers;
    models.value = modelsResp.models;
    assignments.value = modelsResp.assignments;
  } catch {
    message.error("Failed to load inference configuration");
  } finally {
    loading.value = false;
  }
}

function openAddProvider() {
  editingProvider.value = null;
  providerForm.value = {
    display_name: "",
    base_url: "",
    api_key: "",
    provider_type: "completions",
  };
  validationError.value = "";
  showProviderModal.value = true;
}

function openEditProvider(p: InferenceProvider) {
  editingProvider.value = p.slug;
  providerForm.value = {
    display_name: p.display_name,
    base_url: p.base_url,
    api_key: "",
    provider_type: p.provider_type,
  };
  validationError.value = "";
  showProviderModal.value = true;
}

async function saveProvider() {
  const form = providerForm.value;
  if (!form.display_name.trim() || !form.base_url.trim()) {
    message.warning("Display name and base URL are required");
    return;
  }

  validationError.value = "";
  validating.value = true;
  try {
    const result = await api.validateInferenceProvider({
      base_url: form.base_url,
      api_key: form.api_key || undefined,
      provider_type: form.provider_type,
      slug: editingProvider.value || undefined,
    });
    if (!result.valid) {
      validationError.value = result.error || "Connection validation failed";
      return;
    }
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "Validation request failed";
    validationError.value = msg;
    return;
  } finally {
    validating.value = false;
  }

  try {
    if (editingProvider.value) {
      await api.updateInferenceProvider(editingProvider.value, {
        display_name: form.display_name,
        base_url: form.base_url,
        api_key: form.api_key || undefined,
        provider_type: form.provider_type,
      });
      message.success(`Updated provider "${form.display_name}"`);
    } else {
      await api.createInferenceProvider({
        display_name: form.display_name,
        base_url: form.base_url,
        api_key: form.api_key || undefined,
        provider_type: form.provider_type,
      });
      message.success(`Added provider "${form.display_name}"`);
    }
    showProviderModal.value = false;
    await loadAll();
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "Failed to save provider";
    message.error(msg);
  }
}

async function removeProvider(slug: string) {
  try {
    await api.deleteInferenceProvider(slug);
    message.success(`Removed provider`);
    await loadAll();
  } catch {
    message.error(`Failed to remove provider`);
  }
}

async function refreshAll() {
  refreshing.value = true;
  try {
    const resp = await api.refreshAllModels();
    await loadAll();
    const errors = resp.errors ?? {};
    const errorCount = Object.keys(errors).length;
    if (errorCount > 0) {
      const names = Object.keys(errors).join(", ");
      message.warning(
        `Discovery failed for ${errorCount} provider(s): ${names}`,
      );
    } else {
      message.success("Model discovery complete");
    }
  } catch {
    message.error("Failed to refresh models");
  } finally {
    refreshing.value = false;
  }
}

async function refreshProvider(slug: string) {
  try {
    const resp = await api.refreshProviderModels(slug);
    await loadAll();
    if (resp.error) {
      message.error(`${slug}: ${resp.error}`);
    } else {
      message.success(`Refreshed models`);
    }
  } catch {
    message.error(`Failed to refresh models`);
  }
}

async function updateProvider(
  role: "intelligence" | "task",
  providerSlug: string,
) {
  // When provider changes, reset model selection
  const newAssignments = {
    ...assignments.value,
    [role]: { endpoint: providerSlug, model: "" },
  };
  try {
    await api.setModelAssignments(newAssignments);
    assignments.value = newAssignments;
  } catch {
    message.error("Failed to set model assignment");
  }
}

async function updateModel(role: "intelligence" | "task", modelId: string) {
  const newAssignments = {
    ...assignments.value,
    [role]: { ...assignments.value[role], model: modelId },
  };
  try {
    await api.setModelAssignments(newAssignments);
    assignments.value = newAssignments;
  } catch {
    message.error("Failed to set model assignment");
  }
}

async function toggleIntelligenceToolCalling(value: boolean) {
  const a = assignments.value.intelligence;
  if (!a.endpoint || !a.model) return;
  try {
    await api.setModelCapability(a.endpoint, a.model, value);
    // Update local state
    const m = models.value.find(
      (mm) => mm.provider === a.endpoint && mm.id === a.model,
    );
    if (m) m.native_tool_calling = value;
  } catch {
    message.error("Failed to update tool calling flag");
  }
}

const providerTypeOptions = [
  { label: "OpenAI Completions", value: "completions" },
];
</script>

<template>
  <div class="settings-inference">
    <div class="section-header">
      <IconCpu :size="24" class="section-icon" />
      <NText class="section-title">Inference</NText>
      <NButton
        size="small"
        quaternary
        :loading="refreshing"
        @click="refreshAll"
        v-if="hasProviders"
      >
        <template #icon><IconRefresh :size="16" /></template>
        Refresh All
      </NButton>
    </div>

    <NSpin :show="loading">
      <div v-if="!loading" class="inference-body">
        <!-- Providers -->
        <div class="providers-section">
          <div class="subsection-header">
            <NText class="subsection-title">Providers</NText>
            <NButton size="small" type="primary" @click="openAddProvider">
              <template #icon><IconPlus :size="16" /></template>
              Add Provider
            </NButton>
          </div>

          <NText v-if="!hasProviders" depth="3" class="empty-hint">
            No inference providers configured. Add one to get started.
          </NText>

          <div class="provider-list" v-for="p in providers" :key="p.slug">
            <NCard size="small" class="provider-card">
              <div class="provider-header">
                <div class="provider-info">
                  <NText class="provider-name">{{ p.display_name }}</NText>
                  <NTag size="small" :bordered="false">
                    {{ p.provider_type }}
                  </NTag>
                  <NText depth="3" class="provider-url">{{ p.base_url }}</NText>
                </div>
                <NSpace size="small">
                  <NButton
                    size="tiny"
                    quaternary
                    @click="refreshProvider(p.slug)"
                  >
                    <template #icon><IconRefresh :size="14" /></template>
                  </NButton>
                  <NButton size="tiny" quaternary @click="openEditProvider(p)">
                    <template #icon><IconEdit :size="14" /></template>
                  </NButton>
                  <NPopconfirm @positive-click="removeProvider(p.slug)">
                    <template #trigger>
                      <NButton size="tiny" quaternary type="error">
                        <template #icon><IconTrash :size="14" /></template>
                      </NButton>
                    </template>
                    Remove provider "{{ p.display_name }}" and all its
                    discovered models?
                  </NPopconfirm>
                </NSpace>
              </div>

              <NAlert
                v-if="p.last_error"
                type="error"
                :show-icon="true"
                class="provider-error"
                :bordered="false"
              >
                {{ p.last_error }}
              </NAlert>
            </NCard>
          </div>
        </div>

        <!-- Model Assignments -->
        <div class="assignments-section" v-if="hasProviders">
          <div class="subsection-header">
            <NText class="subsection-title">Model Assignments</NText>
          </div>

          <div class="assignment-block">
            <NText class="assignment-label">Intelligence</NText>
            <div class="assignment-selects">
              <NSelect
                :value="assignments.intelligence.endpoint"
                :options="providerOptions"
                placeholder="Provider"
                class="provider-select"
                @update:value="(v: string) => updateProvider('intelligence', v)"
              />
              <NSelect
                :value="assignments.intelligence.model"
                :options="modelOptionsFor(assignments.intelligence.endpoint)"
                placeholder="Model"
                filterable
                :disabled="!assignments.intelligence.endpoint"
                class="model-select"
                @update:value="(v: string) => updateModel('intelligence', v)"
              />
            </div>
            <div class="tool-calling-toggle">
              <NText depth="3" class="tool-calling-label"
                >Native Tool Calling</NText
              >
              <NSwitch
                :value="intelligenceToolCalling"
                size="small"
                :disabled="!assignments.intelligence.model"
                @update:value="toggleIntelligenceToolCalling"
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

          <div class="assignment-block">
            <NText class="assignment-label">Task</NText>
            <div class="assignment-selects">
              <NSelect
                :value="assignments.task.endpoint"
                :options="providerOptions"
                placeholder="Provider"
                class="provider-select"
                @update:value="(v: string) => updateProvider('task', v)"
              />
              <NSelect
                :value="assignments.task.model"
                :options="modelOptionsFor(assignments.task.endpoint)"
                placeholder="Model"
                filterable
                :disabled="!assignments.task.endpoint"
                class="model-select"
                @update:value="(v: string) => updateModel('task', v)"
              />
            </div>
          </div>
        </div>
      </div>
    </NSpin>

    <!-- Add/Edit Provider Modal -->
    <NModal
      v-model:show="showProviderModal"
      preset="card"
      :title="editingProvider ? 'Edit Provider' : 'Add Provider'"
      style="width: 500px"
    >
      <NForm label-placement="top">
        <NFormItem label="Display Name">
          <NInput
            v-model:value="providerForm.display_name"
            placeholder="e.g. Local Lab, OpenRouter"
          />
        </NFormItem>
        <NFormItem label="Base URL">
          <NInput
            v-model:value="providerForm.base_url"
            placeholder="http://localhost:8080/v1"
          />
        </NFormItem>
        <NFormItem label="API Key">
          <NInput
            v-model:value="providerForm.api_key"
            type="password"
            show-password-on="click"
            :placeholder="
              editingProvider ? 'Leave blank to keep existing' : 'Optional'
            "
          />
        </NFormItem>
        <NFormItem label="Provider Type">
          <NSelect
            v-model:value="providerForm.provider_type"
            :options="providerTypeOptions"
          />
        </NFormItem>

        <NAlert
          v-if="validationError"
          type="error"
          :show-icon="true"
          :bordered="false"
          class="validation-error"
        >
          {{ validationError }}
        </NAlert>
      </NForm>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="showProviderModal = false">Cancel</NButton>
          <NButton type="primary" :loading="validating" @click="saveProvider">
            <template v-if="validating">Validating...</template>
            <template v-else>Save</template>
          </NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.settings-inference {
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
  flex: 1;
}

.inference-body {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.subsection-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.subsection-title {
  font-size: 1em;
  font-weight: 600;
}

.empty-hint {
  display: block;
  padding: 16px 0;
  font-size: 0.9em;
}

.provider-list {
  margin-bottom: 8px;
}

.provider-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.provider-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.provider-name {
  font-weight: 600;
}

.provider-url {
  font-size: 0.85em;
  font-family: monospace;
}

.provider-error {
  margin-top: 8px;
}

.assignments-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.assignment-block {
  display: flex;
  align-items: center;
  gap: 12px;
}

.assignment-label {
  flex-shrink: 0;
  width: 100px;
  font-weight: 500;
}

.assignment-selects {
  flex: 1;
  display: flex;
  gap: 8px;
  min-width: 0;
}

.provider-select {
  flex-shrink: 0;
  width: 140px;
}

.model-select {
  flex: 1;
  min-width: 0;
}

.tool-calling-toggle {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.tool-calling-label {
  font-size: 0.85em;
  white-space: nowrap;
}

.validation-error {
  margin-top: 8px;
}
</style>
