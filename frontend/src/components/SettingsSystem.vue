<script setup lang="ts">
import { NText } from "naive-ui";
import { IconSettings } from "@tabler/icons-vue";
</script>

<template>
  <div class="settings-system">
    <div class="section-header">
      <IconSettings :size="24" class="section-icon" />
      <NText class="section-title">System</NText>
    </div>
    <NText depth="3">System configuration coming soon.</NText>
  </div>
</template>

<style scoped>
.settings-system {
  padding: 24px;
  max-width: 800px;
  width: 100%;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.section-icon {
  color: var(--n-primary-color);
}

.section-title {
  font-size: 1.3em;
  font-weight: 600;
}
</style>
