<script setup lang="ts">
import { NButton, NScrollbar, NInput, useDialog } from "naive-ui";
import { IconPencil, IconCheck, IconSparkles, IconTrash, IconHandStop } from "@tabler/icons-vue";
import { useChatStore } from "../stores/chat";
import { useRouter } from "vue-router";
import { ref, nextTick } from "vue";

const store = useChatStore();
const router = useRouter();
const dialog = useDialog();

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
  dialog.error({
    title: "Delete conversation?",
    content: "This will permanently delete the conversation and all its messages. This cannot be undone.",
    positiveText: "Delete",
    negativeText: "Cancel",
    onPositiveClick: async () => {
      const wasCurrent = store.currentConversationId === conversationId;
      await store.deleteConversation(conversationId);
      if (wasCurrent) {
        router.push({ name: "new-conversation" });
      }
    },
  });
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
          v-for="conversation in store.conversations"
          :key="conversation.id"
          :class="[
            'conv-item',
            {
              active: conversation.id === store.currentConversationId,
              running: store.isConversationRunning(conversation.id),
            },
          ]"
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
                <IconCheck :size="22" />
              </template>
            </NButton>
          </div>
          <div
            v-else
            class="conv-row"
            @click="
              router.push({
                name: 'conversation',
                params: { id: conversation.id },
              })
            "
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
                  <IconPencil :size="18" />
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
                  <IconSparkles :size="18" />
                </template>
              </NButton>
              <NButton
                v-if="store.isConversationRunning(conversation.id)"
                quaternary
                circle
                size="small"
                @click.stop="store.stopRun()"
                class="icon-btn icon-btn-stop"
              >
                <template #icon>
                  <IconHandStop :size="18" />
                </template>
              </NButton>
              <NButton
                v-else
                quaternary
                circle
                size="small"
                @click.stop="handleDelete(conversation.id)"
                class="icon-btn icon-btn-delete"
              >
                <template #icon>
                  <IconTrash :size="18" />
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

.conv-item.running {
  background-image:
    linear-gradient(90deg, #36ad6a 50%, transparent 50%),
    linear-gradient(90deg, #36ad6a 50%, transparent 50%),
    linear-gradient(0deg, #36ad6a 50%, transparent 50%),
    linear-gradient(0deg, #36ad6a 50%, transparent 50%);
  background-repeat: repeat-x, repeat-x, repeat-y, repeat-y;
  background-size: 16px 2px, 16px 2px, 2px 16px, 2px 16px;
  background-position: 0 0, 100% 100%, 0 100%, 100% 0;
  animation: border-dance 6s infinite linear;
}

.conv-item.running.active {
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

.icon-btn-stop {
  color: var(--n-warning-color, #f0a020);
  opacity: 1;
}

.icon-btn-stop:hover {
  color: var(--n-error-color, #d03050);
}
</style>

<style>
@keyframes border-dance {
  0% {
    background-position: 0 0, 100% 100%, 0 100%, 100% 0;
  }
  100% {
    background-position: 100% 0, 0 100%, 0 0, 100% 100%;
  }
}
</style>
