<script setup lang="ts">
import { NText, NIcon, NDivider, NButton } from "naive-ui";
import { ArrowLeft } from "@vicons/tabler";
import { useToolsStore } from "../stores/tools";
import { useRoute, useRouter } from "vue-router";
import { computed } from "vue";

const store = useToolsStore();
const route = useRoute();
const router = useRouter();

const toolName = computed(() => route.params.name as string);
const tool = computed(() => store.tools.find((t) => t.name === toolName.value));

const requiredParams = computed(() => {
  if (!tool.value) return [];
  return tool.value.parameters.filter((p) => p.required);
});

const optionalParams = computed(() => {
  if (!tool.value) return [];
  return tool.value.parameters.filter((p) => !p.required);
});
</script>

<template>
  <div class="detail-view" v-if="tool">
    <div class="detail-header">
      <NButton quaternary circle @click="router.push({ name: 'tools' })">
        <template #icon>
          <NIcon><ArrowLeft /></NIcon>
        </template>
      </NButton>
      <div>
        <NText class="detail-name">{{ tool.name }}</NText>
        <NText v-if="tool.builtIn" depth="3" class="detail-badge">Built-in</NText>
      </div>
    </div>

    <NText class="detail-desc">{{ tool.description }}</NText>

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
  </div>

  <div class="detail-view" v-else>
    <NText>Tool "{{ toolName }}" not found.</NText>
    <NButton @click="router.push({ name: 'tools' })">Back to catalog</NButton>
  </div>
</template>

<style scoped>
.detail-view {
  padding: 32px;
  max-width: 800px;
  width: 100%;
}

.detail-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.detail-name {
  font-family: monospace;
  font-size: 1.4em;
  font-weight: 700;
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
</style>
