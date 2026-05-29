<script setup lang="ts">
import { NText, NIcon } from "naive-ui";
import { Settings } from "@vicons/tabler";
</script>

<template>
  <div class="settings-view">
    <div class="settings-header">
      <NIcon :size="28" class="header-icon"><Settings /></NIcon>
      <NText class="header-title">Settings</NText>
    </div>

    <NText depth="3">
      System information and configuration settings coming soon.
    </NText>
  </div>
</template>

<style scoped>
.settings-view {
  padding: 32px;
  max-width: 800px;
  width: 100%;
}

.settings-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}

.header-icon {
  color: var(--n-primary-color);
}

.header-title {
  font-size: 1.6em;
  font-weight: 700;
}
</style>
