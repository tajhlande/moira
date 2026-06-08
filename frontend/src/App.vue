<script setup lang="ts">
import { darkTheme } from "naive-ui";
import { provide, ref } from "vue";
import { RouterView } from "vue-router";
import AppLayout from "./components/AppLayout.vue";
import "./themes/default-light/style.css";

const isDark = ref(false);
provide("isDark", isDark);
</script>

<template>
  <n-config-provider :theme="isDark ? darkTheme : undefined">
    <n-message-provider>
      <n-dialog-provider>
        <AppLayout>
          <RouterView />
        </AppLayout>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
