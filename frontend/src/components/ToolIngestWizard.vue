<script setup lang="ts">
import { ref, computed } from "vue";
import {
  NText,
  NButton,
  NInput,
  NCheckbox,
  NCheckboxGroup,
  NAlert,
  NDivider,
  NSteps,
  NStep,
  NProgress,
  useMessage,
} from "naive-ui";
import { IconArrowLeft, IconArrowRight, IconCheck } from "@tabler/icons-vue";
import { useRouter } from "vue-router";
import { useToolsStore } from "../stores/tools";
import {
  api,
  type IngestPreview,
  type IngestOperation,
  type IngestCommitResponse,
} from "../api/client";

const router = useRouter();
const store = useToolsStore();
const message = useMessage();

const step = ref(0);
const loading = ref(false);
const committing = ref(false);
const commitProgress = ref(0);
const commitPhase = ref("");

const COMMIT_PHASES = [
  { label: "Generating tool descriptions", target: 30 },
  { label: "Registering tools", target: 70 },
  { label: "Building search index", target: 95 },
];

// Step 1 state
const baseUrl = ref("");
const specUrl = ref("");
const specFile: { value: File | null } = { value: null };

// Step 2 state
const preview = ref<IngestPreview | null>(null);
const serverUrl = ref("");

// Step 3 state
const selectedOps = ref<string[]>([]);

// Step 4 state
const groupNameOverride = ref("");

// Step 5 state
const commitResult = ref<IngestCommitResponse | null>(null);

const METHOD_COLORS: Record<string, string> = {
  GET: "#18a058",
  POST: "#2080f0",
  PUT: "#f0a020",
  PATCH: "#e0a000",
  DELETE: "#d03050",
};

const canProceed = computed(() => {
  if (step.value === 0) return !!(baseUrl.value || specUrl.value || specFile.value);
  if (step.value === 1) return !!preview.value;
  if (step.value === 2) return selectedOps.value.length > 0;
  if (step.value === 3) return true;
  return false;
});

const totalOps = computed(() => preview.value?.operations.length ?? 0);

const groupedOps = computed(() => {
  if (!preview.value) return new Map<string, IngestOperation[]>();
  const map = new Map<string, IngestOperation[]>();
  for (const op of preview.value.operations) {
    const tag = op.tags[0] || "Untagged";
    const list = map.get(tag) || [];
    list.push(op);
    map.set(tag, list);
  }
  return map;
});

function handleFileUpload(event: Event) {
  const input = event.target as HTMLInputElement;
  if (input.files?.length) {
    specFile.value = input.files[0] ?? null;
  }
}

async function analyzeSpec() {
  loading.value = true;
  preview.value = null;
  commitResult.value = null;

  try {
    const body: Record<string, string> = {};
    if (baseUrl.value) body.url = baseUrl.value;
    if (specUrl.value) body.spec_url = specUrl.value;

    if (specFile.value) {
      body.spec_content = await specFile.value.text();
    }

    if (!body.url && !body.spec_url && !body.spec_content) {
      message.error("Provide a base URL, spec URL, or upload a file");
      loading.value = false;
      return;
    }

    const resp = await api.ingestStart(body);
    preview.value = resp;
    serverUrl.value = resp.server_urls[0] || resp.base_url || baseUrl.value;
    groupNameOverride.value = resp.group_name;

    // Auto-select non-deprecated operations if <= 50 total
    if (resp.operations.length <= 50) {
      selectedOps.value = resp.operations
        .filter((op) => !op.deprecated)
        .map((op) => op.name);
    } else {
      selectedOps.value = [];
    }

    step.value = 1;
  } catch (e) {
    message.error(
      e instanceof Error ? e.message : "Failed to analyze spec",
    );
  } finally {
    loading.value = false;
  }
}

function nextStep() {
  if (step.value < 4) step.value++;
}

function prevStep() {
  if (step.value > 0) step.value--;
}

function toggleAll(ops: IngestOperation[], checked: boolean) {
  const names = ops.map((op) => op.name);
  if (checked) {
    const existing = new Set(selectedOps.value);
    for (const n of names) existing.add(n);
    selectedOps.value = [...existing];
  } else {
    selectedOps.value = selectedOps.value.filter((n) => !names.includes(n));
  }
}

let _progressTimer: ReturnType<typeof setTimeout> | null = null;

function startProgress() {
  commitProgress.value = 5;
  commitPhase.value = COMMIT_PHASES[0].label;
  let phaseIdx = 0;

  const tick = () => {
    if (!committing.value) return;
    const phase = COMMIT_PHASES[phaseIdx];
    if (commitProgress.value < phase.target) {
      commitProgress.value += 1;
    } else if (phaseIdx < COMMIT_PHASES.length - 1) {
      phaseIdx++;
      commitPhase.value = COMMIT_PHASES[phaseIdx].label;
    }
    _progressTimer = setTimeout(tick, 300);
  };
  _progressTimer = setTimeout(tick, 300);
}

function stopProgress() {
  if (_progressTimer) {
    clearTimeout(_progressTimer);
    _progressTimer = null;
  }
  commitProgress.value = 100;
  commitPhase.value = "Complete";
}

async function commitIngestion() {
  if (!preview.value) return;
  committing.value = true;
  commitProgress.value = 0;
  commitPhase.value = "";
  step.value = 4;

  startProgress();

  try {
    commitResult.value = await api.ingestCommit({
      source_id: preview.value.source_id,
      base_url: preview.value.base_url,
      spec_url: preview.value.spec_url,
      spec_format: preview.value.spec_format,
      group_name: groupNameOverride.value || preview.value.group_name,
      auth_type: preview.value.auth_type,
      selected_operations: selectedOps.value,
      operations: preview.value.operations.filter((op) =>
        selectedOps.value.includes(op.name),
      ),
      server_url: serverUrl.value,
    });
    stopProgress();
    store.refreshTools();
  } catch (e) {
    stopProgress();
    message.error(
      e instanceof Error ? e.message : "Provisioning failed",
    );
  } finally {
    committing.value = false;
  }
}

function done() {
  router.push({ name: "tools" });
}
</script>

<template>
  <div class="wizard-view">
    <div class="wizard-header">
      <NButton quaternary circle @click="done">
        <template #icon>
          <IconArrowLeft />
        </template>
      </NButton>
      <NText class="wizard-title">Import tools from API</NText>
    </div>

    <NSteps :current="step" class="wizard-steps" size="small">
      <NStep title="Source" />
      <NStep title="Review" />
      <NStep title="Select" />
      <NStep title="Configure" />
      <NStep title="Confirm" />
    </NSteps>

    <NDivider />

    <!-- Step 0: Source -->
    <div v-if="step === 0" class="step-content">
      <NText class="step-heading">Provide API details</NText>

      <div class="form-field">
        <NText depth="3" class="field-label">API Base URL</NText>
        <NInput
          v-model:value="baseUrl"
          placeholder="https://api.example.com"
          size="medium"
        />
      </div>

      <NText depth="3" class="field-hint"
        >The system will probe well-known paths for the spec automatically.</NText
      >

      <NDivider class="field-divider">or</NDivider>

      <div class="form-field">
        <NText depth="3" class="field-label">Spec URL</NText>
        <NInput
          v-model:value="specUrl"
          placeholder="https://api.example.com/openapi.json"
          size="medium"
        />
      </div>

      <NDivider class="field-divider">or</NDivider>

      <div class="form-field">
        <NText depth="3" class="field-label">Upload Spec File</NText>
        <input
          type="file"
          accept=".json,.yaml,.yml"
          @change="handleFileUpload"
          class="file-input"
        />
        <NText v-if="specFile.value" depth="3" class="file-name">{{
          specFile.value.name
        }}</NText>
      </div>

      <div class="step-actions">
        <NButton type="primary" :loading="loading" @click="analyzeSpec">
          Analyze
        </NButton>
      </div>
    </div>

    <!-- Step 1: Review -->
    <div v-if="step === 1 && preview" class="step-content">
      <NText class="step-heading">{{ preview.api_title }}</NText>
      <NText depth="3" class="step-sub">{{ preview.api_description }}</NText>

      <div class="review-grid">
        <div class="review-item">
          <NText depth="3" class="review-label">Format</NText>
          <NText class="review-value">{{
            preview.spec_format.replace("_", " ").replace(/\b\w/g, (c) => c.toUpperCase())
          }}</NText>
        </div>
        <div class="review-item">
          <NText depth="3" class="review-label">Operations</NText>
          <NText class="review-value">{{ preview.total_operations }}</NText>
        </div>
        <div class="review-item">
          <NText depth="3" class="review-label">Server</NText>
          <NText class="review-value review-mono">{{ serverUrl }}</NText>
        </div>
      </div>

      <NAlert
        v-if="preview.auth_required"
        type="warning"
        class="auth-alert"
      >
        This API requires authentication ({{ preview.auth_type }}).
        Tools will be registered as disabled until credentials are configured.
      </NAlert>

      <div class="step-actions">
        <NButton @click="prevStep">Back</NButton>
        <NButton type="primary" @click="nextStep">Continue</NButton>
      </div>
    </div>

    <!-- Step 2: Select Operations -->
    <div v-if="step === 2 && preview" class="step-content">
      <div class="select-header">
        <NText class="step-heading">Select Operations</NText>
        <NText depth="3"
          >{{ selectedOps.length }} of {{ totalOps }} selected</NText
        >
      </div>

      <NCheckboxGroup v-model:value="selectedOps" class="ops-list">
        <div
          v-for="[tag, ops] of groupedOps"
          :key="tag"
          class="ops-group"
        >
          <div class="ops-group-header">
            <NText strong>{{ tag }}</NText>
            <NText depth="3" class="ops-group-count"
              >{{ ops.length }} operations</NText
            >
            <NButton
              text
              size="tiny"
              @click="toggleAll(ops, true)"
              class="select-all-btn"
              >All</NButton
            >
            <NButton
              text
              size="tiny"
              @click="toggleAll(ops, false)"
              >None</NButton
            >
          </div>
          <div
            v-for="op in ops"
            :key="op.name"
            class="op-row"
          >
            <NCheckbox :value="op.name" :disabled="false">
              <div class="op-info">
                <span
                  class="method-badge"
                  :style="{ color: METHOD_COLORS[op.method] || '#666' }"
                  >{{ op.method }}</span
                >
                <span class="op-path">{{ op.path }}</span>
                <NText depth="3" class="op-desc">{{ op.description }}</NText>
              </div>
            </NCheckbox>
          </div>
        </div>
      </NCheckboxGroup>

      <div class="step-actions">
        <NButton @click="prevStep">Back</NButton>
        <NButton type="primary" :disabled="!canProceed" @click="nextStep">
          Continue
        </NButton>
      </div>
    </div>

    <!-- Step 3: Configure -->
    <div v-if="step === 3 && preview" class="step-content">
      <NText class="step-heading">Configure</NText>

      <div class="form-field">
        <NText depth="3" class="field-label">Group Name</NText>
        <NInput v-model:value="groupNameOverride" size="medium" />
      </div>

      <NText depth="3" class="field-hint"
        >This determines the tool name prefix. Tools will be named
        <code
          >{{ groupNameOverride.replace(/\s+/g, "_").toLowerCase().slice(0, 24) }}__{{
            preview.operations[0]?.name.split("__")[1] || "method"
          }}</code
        >
        etc.</NText
      >

      <div class="summary-card" v-if="preview">
        <NText
          >{{ selectedOps.length }} tools will be registered from
          {{ preview.api_title }}</NText
        >
        <NText v-if="preview.auth_required" depth="3">
          All tools will be disabled until credentials are configured.
        </NText>
      </div>

      <div class="step-actions">
        <NButton @click="prevStep">Back</NButton>
        <NButton
          type="primary"
          :loading="committing"
          @click="commitIngestion"
        >
          Register Tools
        </NButton>
      </div>
    </div>

    <!-- Step 4: Confirm -->
    <div v-if="step === 4" class="step-content">
      <div v-if="committing" class="progress-section">
        <NText class="step-heading">Importing tools...</NText>
        <NProgress
          type="line"
          :percentage="commitProgress"
          :show-indicator="true"
          :height="20"
          :border-radius="4"
          processing
        />
        <NText depth="3" class="progress-phase">{{ commitPhase }}</NText>
      </div>

      <div v-if="commitResult" class="confirm-content">
        <NText class="step-heading">Registration Complete</NText>

        <div class="confirm-stats">
          <div class="confirm-stat">
            <NText class="stat-value">{{
              commitResult.succeeded.length
            }}</NText>
            <NText depth="3">registered</NText>
          </div>
          <div v-if="commitResult.disabled.length" class="confirm-stat">
            <NText class="stat-value warn">{{
              commitResult.disabled.length
            }}</NText>
            <NText depth="3">disabled (auth)</NText>
          </div>
          <div v-if="commitResult.failed.length" class="confirm-stat">
            <NText class="stat-value error">{{
              commitResult.failed.length
            }}</NText>
            <NText depth="3">failed</NText>
          </div>
        </div>

        <div v-if="commitResult.failed.length" class="failed-list">
          <NText strong>Failed tools:</NText>
          <div
            v-for="f in commitResult.failed"
            :key="f.name"
            class="failed-item"
          >
            <NText depth="3">{{ f.name }}: {{ f.reason }}</NText>
          </div>
        </div>
      </div>

      <div v-if="commitResult" class="step-actions">
        <NButton type="primary" @click="done">Done</NButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wizard-view {
  padding: 32px;
  max-width: 800px;
  width: 100%;
}

.wizard-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.wizard-title {
  font-size: 1.4em;
  font-weight: 700;
}

.wizard-steps {
  margin-bottom: 8px;
}

.step-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.step-heading {
  font-size: 1.2em;
  font-weight: 600;
  display: block;
}

.step-sub {
  font-size: 0.9em;
  display: block;
  margin-bottom: 8px;
}

.step-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.field-label {
  font-size: 0.9em;
  font-weight: 500;
}

.field-hint {
  font-size: 0.85em;
}

.field-divider {
  margin: 4px 0;
}

.file-input {
  font-size: 0.9em;
}

.file-name {
  font-size: 0.85em;
  font-style: italic;
}

/* Review step */
.review-grid {
  display: flex;
  gap: 24px;
  margin: 8px 0;
}

.review-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.review-label {
  font-size: 0.8em;
}

.review-value {
  font-size: 0.95em;
}

.review-mono {
  font-family: monospace;
}

.auth-alert {
  margin: 8px 0;
}

/* Select step */
.select-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.ops-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: 50vh;
  overflow-y: auto;
  padding: 8px 0;
}

.ops-group-header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 4px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--n-border-color, #e0e0e0);
}

.ops-group-count {
  font-size: 0.85em;
}

.select-all-btn {
  margin-left: auto;
}

.op-row {
  padding: 4px 0;
}

.op-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.method-badge {
  font-family: monospace;
  font-size: 0.8em;
  font-weight: 700;
  min-width: 40px;
}

.op-path {
  font-family: monospace;
  font-size: 0.85em;
}

.op-desc {
  font-size: 0.85em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 300px;
}

/* Configure step */
.summary-card {
  padding: 12px 16px;
  border: 1px solid var(--n-border-color, #e0e0e0);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* Confirm step */
.progress-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 20px 0;
}

.progress-phase {
  font-size: 0.9em;
  text-align: center;
}

.confirm-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.confirm-stats {
  display: flex;
  gap: 24px;
}

.confirm-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.stat-value {
  font-size: 1.8em;
  font-weight: 700;
  color: var(--n-success-color, #18a058);
}

.stat-value.warn {
  color: var(--n-warning-color, #f0a020);
}

.stat-value.error {
  color: var(--n-error-color, #d03050);
}

.failed-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.failed-item {
  padding: 4px 0;
}
</style>
