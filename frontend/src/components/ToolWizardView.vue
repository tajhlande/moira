<script setup lang="ts">
import { NText, NButton } from "naive-ui";
import { IconArrowLeft, IconPlus } from "@tabler/icons-vue";
import { useRouter } from "vue-router";

const router = useRouter();
</script>

<template>
  <div class="wizard-view">
    <div class="wizard-header">
      <NButton quaternary circle @click="router.push({ name: 'tools' })">
        <template #icon>
          <IconArrowLeft />
        </template>
      </NButton>
      <IconPlus :size="24" class="wizard-icon" />
      <NText class="wizard-title">Add New Tool</NText>
    </div>

    <NText depth="3">
      Tool discovery wizard coming soon. This will guide you through finding,
      configuring, and registering new tools for the research agent.
    </NText>
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
  margin-bottom: 24px;
}

.wizard-icon {
  color: var(--n-primary-color);
}

.wizard-title {
  font-size: 1.4em;
  font-weight: 700;
}
</style>
