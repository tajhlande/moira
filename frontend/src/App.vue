<script setup lang="ts">
import { darkTheme } from "naive-ui";
import { provide, ref } from "vue";
import AppLayout from "./components/AppLayout.vue";
import ChatView from "./components/ChatView.vue";
import "./themes/default-light/style.css";

const isDark = ref(false);
provide("isDark", isDark);
</script>

<template>
  <n-config-provider :theme="isDark ? darkTheme : undefined">
    <n-message-provider>
      <AppLayout>
        <ChatView />
      </AppLayout>
    </n-message-provider>
  </n-config-provider>
</template>
