<script setup lang="ts">
import { IconCpu, IconBolt, IconBoltOff } from "@tabler/icons-vue";

defineProps<{
  providerName: string;
  modelId: string;
  overridden?: boolean;
  nativeToolCalling?: boolean;
}>();
</script>

<template>
  <div class="model-pill" :class="{ overridden }" title="Change model">
    <IconCpu :size="14" class="pill-icon" />
    <div class="pill-text">
      <span class="pill-provider">{{ providerName }}</span>
      <span class="pill-model">{{ modelId || "Not set" }}</span>
    </div>
    <IconBolt
      v-if="nativeToolCalling"
      :size="12"
      class="pill-capability"
      title="Native tool calling"
    />
    <IconBoltOff
      v-else
      :size="12"
      class="pill-capability pill-capability-off"
      title="Emulated tool calling"
    />
  </div>
</template>

<style scoped>
.model-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 6px;
  cursor: pointer;
  background: var(--moira-sidebar-bg, #f0f0f0);
  border: 1px solid var(--moira-border, #e0e0e0);
  transition: border-color 150ms ease;
}

.model-pill:hover {
  border-color: var(--n-primary-color, #18a058);
}

.model-pill.overridden {
  border-color: var(--n-primary-color, #18a058);
}

.pill-icon {
  opacity: 0.5;
  flex-shrink: 0;
}

.pill-text {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.pill-provider {
  font-size: 0.75em;
  font-weight: 600;
  opacity: 0.8;
}

.pill-model {
  font-size: 0.7em;
  opacity: 0.6;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pill-capability {
  flex-shrink: 0;
  opacity: 0.5;
  color: var(--n-primary-color, #18a058);
}

.pill-capability-off {
  color: var(--n-text-color-3, #999);
}
</style>
