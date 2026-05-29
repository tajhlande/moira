<script setup lang="ts">
import {
  NButton,
  NScrollbar,
  NIcon,
  NInput,
} from "naive-ui";
import { Pencil, Check, Wand, Trash } from "@vicons/tabler";
import { useChatStore } from "../stores/chat";
import { useRouter } from "vue-router";
import { ref, nextTick } from "vue";

const store = useChatStore();
const router = useRouter();

const editingId = ref<string | null>(null);
const editTitle = ref("");
const editInput = ref<InstanceType<typeof NInput> | null>(null);

const saving = ref(false);
const generatingTitleId = ref<string | null>(null);

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
  const wasCurrent = store.currentConversationId === conversationId;
  await store.deleteConversation(conversationId);
  if (wasCurrent) {
    router.push({ name: "new-conversation" });
  }
}

function hasMessages(conversationId: string): boolean {
  if (conversationId === store.currentConversationId) {
    return store.messages.length > 0;
  }
  return true;
}
</script>

<template>
  <NScrollbar>
    <div style="padding: 16px">
      <NButton
        type="primary"
        block
        @click="router.push({ name: 'new-conversation' })"
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
            @click="router.push({ name: 'conversation', params: { id: conversation.id } })"
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
</template>

<style scoped>
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
