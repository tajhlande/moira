<script setup lang="ts">
import {
  NButton,
  NScrollbar,
  NIcon,
  NInput,
} from "naive-ui";
import { Pencil, Check, Wand, Trash } from "@vicons/tabler";
import { useChatStore } from "../stores/chat";
import { ref, nextTick, onMounted, onUnmounted } from "vue";

const store = useChatStore();

const editingId = ref<string | null>(null);
const editTitle = ref("");
const editInput = ref<InstanceType<typeof NInput> | null>(null);

const saving = ref(false);
const generatingTitleId = ref<string | null>(null);

const siderWidth = ref(260);
const SIDER_MIN = 180;
const SIDER_MAX = 480;
let dragging = false;
let dragStartX = 0;
let dragStartWidth = 0;

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
  siderWidth.value = Math.min(SIDER_MAX, Math.max(SIDER_MIN, dragStartWidth + delta));
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
});

function startEdit(conversation: { id: string; title: string }) {
  editingId.value = conversation.id;
  editTitle.value = conversation.title;
  nextTick(() => editInput.value?.focus());
}

async function finishEdit(conversationId: string) {
  if (saving.value) return;
  const trimmed = editTitle.value.trim();
  if (!trimmed) {
    editingId.value = null;
    return;
  }
  saving.value = true;
  try {
    await store.renameConversation(conversationId, trimmed);
  } finally {
    saving.value = false;
    editingId.value = null;
  }
}

function cancelEdit() {
  editingId.value = null;
}

async function handleGenerateTitle(conversationId: string) {
  if (generatingTitleId.value) return;
  generatingTitleId.value = conversationId;
  try {
    await store.generateTitle(conversationId);
  } finally {
    generatingTitleId.value = null;
  }
}

async function handleDelete(conversationId: string) {
  if (!window.confirm("Delete this conversation?")) return;
  await store.deleteConversation(conversationId);
}

// A conversation has messages if it's the current one and messages exist,
// or we optimistically assume past conversations have messages.
function hasMessages(conversationId: string): boolean {
  if (conversationId === store.currentConversationId) {
    return store.messages.length > 0;
  }
  // Past conversations always have at least one message
  return true;
}
</script>

<template>
  <div style="display: flex; height: 100vh">
    <div class="sider-panel" :style="{ width: siderWidth + 'px' }">
      <NScrollbar>
        <div style="padding: 16px">
          <div class="sidebar-title">MOiRA</div>
          <NButton
            type="primary"
            block
            @click="store.startNewChat"
            style="margin-bottom: 16px"
          >
            New Chat
          </NButton>
          <div class="conv-list">
            <div
              v-if="store.isNewChat"
              class="conv-item active"
            >
              <span class="conv-title-fallback">New Chat</span>
            </div>
            <div
              v-for="conversation in store.conversations"
              :key="conversation.id"
              :class="['conv-item', { active: conversation.id === store.currentConversationId }]"
            >
              <div
                v-if="editingId === conversation.id"
                class="conv-row"
                @click.stop
              >
                <NInput
                  ref="editInput"
                  v-model:value="editTitle"
                  size="small"
                  @keydown.enter.prevent="finishEdit(conversation.id)"
                  @keydown.escape="cancelEdit"
                />
                <NButton
                  size="small"
                  quaternary
                  circle
                  :loading="saving"
                  @click.stop="finishEdit(conversation.id)"
                  class="icon-btn"
                >
                  <template #icon>
                    <NIcon size="22"><Check /></NIcon>
                  </template>
                </NButton>
              </div>
              <div
                v-else
                class="conv-row"
                @click="store.selectConversation(conversation.id)"
              >
                <span class="conv-title">{{ conversation.title }}</span>
                <span class="conv-actions">
                  <NButton
                    quaternary
                    circle
                    size="small"
                    @click.stop="startEdit(conversation)"
                    class="icon-btn"
                  >
                    <template #icon>
                      <NIcon size="18"><Pencil /></NIcon>
                    </template>
                  </NButton>
                  <NButton
                    quaternary
                    circle
                    size="small"
                    :disabled="!hasMessages(conversation.id)"
                    :loading="generatingTitleId === conversation.id"
                    @click.stop="handleGenerateTitle(conversation.id)"
                    class="icon-btn"
                  >
                    <template #icon>
                      <NIcon size="18"><Wand /></NIcon>
                    </template>
                  </NButton>
                  <NButton
                    quaternary
                    circle
                    size="small"
                    @click.stop="handleDelete(conversation.id)"
                    class="icon-btn icon-btn-delete"
                  >
                    <template #icon>
                      <NIcon size="18"><Trash /></NIcon>
                    </template>
                  </NButton>
                </span>
              </div>
            </div>
          </div>
        </div>
      </NScrollbar>
    </div>
    <div class="sider-handle" @mousedown="onDragStart"></div>
    <div class="main-content">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.sider-panel {
  flex-shrink: 0;
  height: 100%;
  border-right: 1px solid var(--n-border-color, #e0e0e0);
  overflow: hidden;
}

.sider-handle {
  flex-shrink: 0;
  width: 4px;
  cursor: col-resize;
  background: transparent;
  transition: background 0.15s;
  z-index: 10;
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

.sidebar-title {
  font-size: 1.4em;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-align: center;
  margin-bottom: 16px;
  opacity: 0.85;
}

.conv-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.conv-item {
  cursor: pointer;
  padding: 8px 12px;
  border-radius: 4px;
  width: 100%;
  box-sizing: border-box;
}

.conv-item.active {
  background-color: var(--moira-border, #e0e0e0);
}

.conv-row {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
}

.conv-title {
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  min-width: 0;
  margin-right: 4px;
}

.conv-title-fallback {
  color: var(--n-text-color-3, #999);
}

.conv-actions {
  display: inline-flex;
  align-items: center;
  gap: 0;
  flex-shrink: 0;
}

.icon-btn {
  color: var(--n-primary-color);
  opacity: 0.65;
}

.icon-btn:hover {
  opacity: 1;
}

.icon-btn-delete:hover {
  color: var(--n-error-color, #d03050);
}
</style>
