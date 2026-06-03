<script setup lang="ts">
import { NScrollbar } from "naive-ui";
import { useRoute } from "vue-router";
import { computed, ref, onMounted, onUnmounted } from "vue";
import { useChatStore } from "../stores/chat";
import { useToolsStore } from "../stores/tools";
import ConversationSidebar from "./ConversationSidebar.vue";
import ToolSidebar from "./ToolSidebar.vue";
import SettingsSidebar from "./SettingsSidebar.vue";
import NavTray from "./NavTray.vue";

const route = useRoute();
const store = useChatStore();
const toolsStore = useToolsStore();

const siderWidth = ref(260);
const SIDER_MIN = 180;
const SIDER_MAX = 480;
let dragging = false;
let dragStartX = 0;
let dragStartWidth = 0;

const sidebarMode = computed(() => {
  return (route.meta?.sidebar as string) || "conversations";
});

const modeLabel = computed(() => {
  const labels: Record<string, string> = {
    conversations: "Conversations",
    tools: "Tools",
    settings: "Settings",
  };
  return labels[sidebarMode.value] || sidebarMode.value;
});

function onDragStart(e: MouseEvent) {
  dragging = true;
  dragStartX = e.clientX;
  dragStartWidth = siderWidth.value;
  document.addEventListener("mousemove", onDragMove);
  document.addEventListener("mouseup", onDragEnd);
  document.body.style.cursor = "col-resize";
  document.body.style.userSelect = "none";
}

function onDragMove(e: MouseEvent) {
  if (!dragging) return;
  const delta = e.clientX - dragStartX;
  siderWidth.value = Math.min(
    SIDER_MAX,
    Math.max(SIDER_MIN, dragStartWidth + delta),
  );
}

function onDragEnd() {
  dragging = false;
  document.removeEventListener("mousemove", onDragMove);
  document.removeEventListener("mouseup", onDragEnd);
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
}

onUnmounted(() => {
  document.removeEventListener("mousemove", onDragMove);
  document.removeEventListener("mouseup", onDragEnd);
});

onMounted(() => {
  store.fetchConversations();
  toolsStore.fetchTools();
});
</script>

<template>
  <div class="app-layout">
    <div class="sider-panel" :style="{ width: siderWidth + 'px' }">
      <div class="sider-content">
        <div class="sidebar-title">MOiRA</div>
        <div class="sidebar-mode-label">{{ modeLabel }}</div>
        <ConversationSidebar v-if="sidebarMode === 'conversations'" />
        <ToolSidebar v-else-if="sidebarMode === 'tools'" />
        <SettingsSidebar v-else-if="sidebarMode === 'settings'" />
      </div>
      <NavTray />
    </div>
    <div class="sider-handle" @mousedown="onDragStart"></div>
    <div class="main-content">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.app-layout {
  --bottom-bar-height: 66px;
  display: flex;
  height: 100vh;
}

.sider-panel {
  flex-shrink: 0;
  height: 100%;
  border-right: 1px solid var(--n-border-color, #e0e0e0);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.sider-content {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.sidebar-title {
  font-size: 1.4em;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-align: center;
  padding: 16px 16px 0;
  opacity: 0.85;
  flex-shrink: 0;
}

.sidebar-mode-label {
  font-size: 1.25em;
  font-weight: 600;
  text-align: center;
  padding: 4px 16px 8px;
  opacity: 1;
  flex-shrink: 0;
}

.sider-handle {
  flex-shrink: 0;
  width: 4px;
  cursor: col-resize;
  background: transparent;
  transition: background 0.15s;
  z-index: 10;
  position: relative;
}

.sider-handle::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  top: calc(100% - var(--bottom-bar-height));
  height: 1px;
  background: var(--moira-border, #e0e0e0);
}

.sider-handle:hover {
  background: var(--n-primary-color, #18a058);
}

.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}
</style>
