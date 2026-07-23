<script setup lang="ts">
import "./workflow-artifacts.css";

type RenderType =
  | "text"
  | "pill-list"
  | "string-list"
  | "badge"
  | "fact-cards"
  | "object-list"
  | "key-value"
  | "code";

interface ItemFieldSpec {
  key: string;
  label: string;
  type: RenderType;
}

interface FieldSpec {
  type: RenderType;
  label: string;
  itemFields?: ItemFieldSpec[];
  variants?: Record<string, string>;
}

const FIELD_REGISTRY: Record<string, FieldSpec> = {
  user_goal: { type: "text", label: "User Goal" },
  topic: { type: "text", label: "Topic" },
  goal_assessment: { type: "text", label: "Goal Assessment" },
  coverage_assessment: { type: "text", label: "Coverage Assessment" },
  missing_areas: { type: "string-list", label: "Missing Areas" },
  entities: { type: "pill-list", label: "Entities" },
  concepts: { type: "pill-list", label: "Concepts" },
  unknown_facts: { type: "fact-cards", label: "Unknown Facts" },
  new_unknown_facts: { type: "string-list", label: "New Unknown Facts" },
  evidence_requests: {
    type: "object-list",
    label: "Evidence Requests",
    itemFields: [
      { key: "target_fact_ids", label: "Target Facts", type: "pill-list" },
      { key: "evidence_needed", label: "Evidence Needed", type: "text" },
      { key: "candidate_tools", label: "Candidate Tools", type: "pill-list" },
      { key: "fallback", label: "Fallback", type: "badge" },
    ],
  },
  conclusions: {
    type: "object-list",
    label: "Conclusions",
    itemFields: [
      { key: "conclusion", label: "Conclusion", type: "text" },
      {
        key: "supporting_fact_ids",
        label: "Supporting Facts",
        type: "pill-list",
      },
      { key: "reasoning", label: "Reasoning", type: "text" },
      { key: "status", label: "Status", type: "badge" },
    ],
  },
  fact_results: {
    type: "object-list",
    label: "Fact Verification",
    itemFields: [
      { key: "fact_id", label: "Fact", type: "text" },
      { key: "result", label: "Result", type: "badge" },
      { key: "evidence", label: "Evidence", type: "text" },
    ],
  },
  conclusion_results: {
    type: "object-list",
    label: "Conclusion Verification",
    itemFields: [
      { key: "conclusion_id", label: "Conclusion", type: "text" },
      { key: "result", label: "Result", type: "badge" },
      { key: "reason", label: "Reason", type: "text" },
    ],
  },
  goal_met: { type: "badge", label: "Goal Met" },
  route: {
    type: "badge",
    label: "Route",
    variants: {
      accept: "success",
      continue: "success",
      retry: "warning",
    },
  },
  selected_tools: { type: "pill-list", label: "Selected Tools" },
  default_tools: { type: "pill-list", label: "Default Tools" },
  discovered_tools: { type: "pill-list", label: "Discovered Tools" },
};

const props = defineProps<{ so: Record<string, unknown> }>();

function resolveSpec(key: string, value: unknown): FieldSpec {
  if (key in FIELD_REGISTRY) return FIELD_REGISTRY[key]!;
  return inferSpec(value);
}

function inferSpec(value: unknown): FieldSpec {
  if (typeof value === "string") return { type: "text", label: "" };
  if (typeof value === "boolean") return { type: "badge", label: "" };
  if (typeof value === "number") return { type: "text", label: "" };
  if (Array.isArray(value)) {
    if (value.length === 0) return { type: "pill-list", label: "" };
    if (value.every((v) => typeof v === "string"))
      return { type: "pill-list", label: "" };
    if (value.every((v) => typeof v === "object" && v !== null)) {
      const fields = inferItemFields(value as Record<string, unknown>[]);
      return { type: "object-list", label: "", itemFields: fields };
    }
    return { type: "string-list", label: "" };
  }
  if (typeof value === "object" && value !== null)
    return { type: "key-value", label: "" };
  return { type: "text", label: "" };
}

function inferItemFields(items: Record<string, unknown>[]): ItemFieldSpec[] {
  const keySet = new Set<string>();
  for (const item of items) {
    for (const k of Object.keys(item)) keySet.add(k);
  }
  return [...keySet].map((k) => {
    const sample = items.find((item) => k in item)?.[k];
    let type: RenderType = "text";
    if (Array.isArray(sample) && sample.every((v) => typeof v === "string"))
      type = "pill-list";
    else if (typeof sample === "boolean") type = "badge";
    else if (typeof sample === "object" && sample !== null) type = "code";
    return { key: k, label: prettyLabel(k), type };
  });
}

function prettyLabel(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function badgeClass(spec: FieldSpec, value: unknown): string {
  if (spec.variants && typeof value === "string" && value in spec.variants)
    return `so-badge ${spec.variants[value]}`;
  if (typeof value === "boolean")
    return `so-badge ${value ? "success" : "error"}`;
  return "so-badge neutral";
}

function badgeText(value: unknown): string {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function asStringArray(val: unknown): string[] {
  if (Array.isArray(val) && val.every((v) => typeof v === "string"))
    return val as string[];
  return [];
}

function asObjectArray(val: unknown): Record<string, unknown>[] {
  if (
    Array.isArray(val) &&
    val.every((v) => typeof v === "object" && v !== null)
  )
    return val as Record<string, unknown>[];
  return [];
}

function asKvPairs(val: unknown): [string, string][] {
  if (val == null || typeof val !== "object" || Array.isArray(val)) return [];
  return Object.entries(val as Record<string, unknown>).map(([k, v]) => [
    k,
    typeof v === "string" || typeof v === "number" || typeof v === "boolean"
      ? String(v)
      : JSON.stringify(v),
  ]);
}

function renderSubValue(val: unknown, type: RenderType): string {
  if (val == null) return "";
  if (type === "code") return JSON.stringify(val, null, 2);
  return String(val);
}

const entries = () => Object.entries(props.so);
</script>

<template>
  <div class="so-renderer">
    <div v-for="([key, value], idx) in entries()" :key="idx" class="so-field">
      <template v-if="resolveSpec(key, value).type === 'text'">
        <div class="detail-label">
          {{ resolveSpec(key, value).label || prettyLabel(key) }}
        </div>
        <div class="so-text-value">{{ value }}</div>
      </template>

      <template v-else-if="resolveSpec(key, value).type === 'pill-list'">
        <div class="detail-label">
          {{ resolveSpec(key, value).label || prettyLabel(key) }}
        </div>
        <div class="so-pill-list">
          <span
            v-for="item in asStringArray(value)"
            :key="item"
            class="tool-tag default"
            >{{ item }}</span
          >
          <span v-if="asStringArray(value).length === 0" class="tool-tag none"
            >None</span
          >
        </div>
      </template>

      <template v-else-if="resolveSpec(key, value).type === 'string-list'">
        <div class="detail-label">
          {{ resolveSpec(key, value).label || prettyLabel(key) }}
        </div>
        <ul class="so-string-list">
          <li v-for="(item, i) in value as string[]" :key="i">{{ item }}</li>
        </ul>
        <div v-if="(value as string[]).length === 0" class="so-empty">None</div>
      </template>

      <template v-else-if="resolveSpec(key, value).type === 'badge'">
        <div class="detail-label">
          {{ resolveSpec(key, value).label || prettyLabel(key) }}
        </div>
        <span :class="badgeClass(resolveSpec(key, value), value)">{{
          badgeText(value)
        }}</span>
      </template>

      <template v-else-if="resolveSpec(key, value).type === 'fact-cards'">
        <div class="detail-label">
          {{ resolveSpec(key, value).label || prettyLabel(key) }}
        </div>
        <div class="so-card-list">
          <div
            v-for="(fact, fi) in asObjectArray(value)"
            :key="fi"
            class="so-card"
          >
            <div class="so-card-field">
              <span class="so-card-key">Subject</span>
              <span class="so-card-val">{{
                fact.subject ?? fact.id ?? ""
              }}</span>
            </div>
            <div class="so-card-field">
              <span class="so-card-key">Fact Needed</span>
              <span class="so-card-val">{{ fact.fact_needed ?? "" }}</span>
            </div>
            <div v-if="fact.claim" class="so-card-field">
              <span class="so-card-key">Claim</span>
              <span class="so-card-val">{{ fact.claim }}</span>
            </div>
            <div v-if="fact.status" class="so-card-field">
              <span class="so-card-key">Status</span>
              <span
                :class="[
                  'so-badge',
                  fact.status === 'verified'
                    ? 'success'
                    : fact.status === 'contradicted'
                      ? 'error'
                      : 'neutral',
                ]"
                >{{ fact.status }}</span
              >
            </div>
          </div>
        </div>
        <div v-if="asObjectArray(value).length === 0" class="so-empty">
          None
        </div>
      </template>

      <template v-else-if="resolveSpec(key, value).type === 'object-list'">
        <div class="detail-label">
          {{ resolveSpec(key, value).label || prettyLabel(key) }}
        </div>
        <div class="so-card-list">
          <div
            v-for="(item, oi) in asObjectArray(value)"
            :key="oi"
            class="so-card"
          >
            <div
              v-for="field in resolveSpec(key, value).itemFields ?? []"
              :key="field.key"
              class="so-card-field"
            >
              <template v-if="item[field.key] != null">
                <span class="so-card-key">{{ field.label }}</span>
                <template v-if="field.type === 'pill-list'">
                  <div class="so-pill-list so-card-val">
                    <span
                      v-for="p in asStringArray(item[field.key])"
                      :key="p"
                      class="tool-tag default"
                      >{{ p }}</span
                    >
                    <span
                      v-if="asStringArray(item[field.key]).length === 0"
                      class="tool-tag none"
                      >None</span
                    >
                  </div>
                </template>
                <template v-else-if="field.type === 'badge'">
                  <span
                    :class="
                      badgeClass(
                        {
                          type: 'badge',
                          label: '',
                          variants:
                            field.key === 'status'
                              ? {
                                  verified: 'success',
                                  contradicted: 'error',
                                  unverified: 'warning',
                                  unknown: 'neutral',
                                }
                              : undefined,
                        },
                        item[field.key],
                      )
                    "
                    >{{ badgeText(item[field.key]) }}</span
                  >
                </template>
                <template v-else-if="field.type === 'code'">
                  <pre class="so-card-code">{{
                    renderSubValue(item[field.key], "code")
                  }}</pre>
                </template>
                <template v-else-if="field.type === 'key-value'">
                  <div class="so-kv-list so-card-val">
                    <template
                      v-for="([kvKey, kvVal], kvi) in asKvPairs(
                        item[field.key],
                      )"
                      :key="kvi"
                    >
                      <span class="so-kv-key">{{ kvKey }}</span>
                      <span class="so-kv-val">{{ kvVal }}</span>
                    </template>
                    <span
                      v-if="asKvPairs(item[field.key]).length === 0"
                      class="so-empty"
                      >None</span
                    >
                  </div>
                </template>
                <template v-else>
                  <span class="so-card-val">{{ item[field.key] }}</span>
                </template>
              </template>
            </div>
          </div>
        </div>
        <div v-if="asObjectArray(value).length === 0" class="so-empty">
          None
        </div>
      </template>

      <template v-else-if="resolveSpec(key, value).type === 'code'">
        <div class="detail-label">
          {{ resolveSpec(key, value).label || prettyLabel(key) }}
        </div>
        <pre class="detail-text-block">{{
          JSON.stringify(value, null, 2)
        }}</pre>
      </template>

      <template v-else-if="resolveSpec(key, value).type === 'key-value'">
        <div class="detail-label">
          {{ resolveSpec(key, value).label || prettyLabel(key) }}
        </div>
        <div class="so-kv-list">
          <template
            v-for="([kvKey, kvVal], kvi) in asKvPairs(value)"
            :key="kvi"
          >
            <span class="so-kv-key">{{ kvKey }}</span>
            <span class="so-kv-val">{{ kvVal }}</span>
          </template>
          <span v-if="asKvPairs(value).length === 0" class="so-empty"
            >None</span
          >
        </div>
      </template>
    </div>
  </div>
</template>
