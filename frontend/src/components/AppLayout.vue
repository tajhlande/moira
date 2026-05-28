<script setup lang="ts">
import {
  NLayout,
  NLayoutSider,
  NLayoutContent,
  NButton,
  NList,
  NListItem,
  NText,
  NScrollbar,
  NIcon,
  NInput,
} from "naive-ui";
import { Pencil, Check } from "@vicons/tabler";
import { useChatStore } from "../stores/chat";
import { ref, nextTick, onMounted } from "vue";

const store = useChatStore();

const editingId = ref<string | null>(null);
const editTitle = ref("");
const editInput = ref<InstanceType<typeof NInput> | null>(null);

const saving = ref(false);

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
</script>

<template>
  <NLayout has-sider style="height: 100vh">
    <NLayoutSider
      bordered
      width="260"
      :native-scrollbar="false"
      content-style="padding: 16px;"
    >
      <div class="sidebar-title">MOiRA</div>
      <NButton
        type="primary"
        block
        @click="store.startNewChat"
        style="margin-bottom: 16px"
      >
        New Chat
      </NButton>
      <NList clickable>
        <NListItem
          v-if="store.isNewChat"
          class="active"
          style="cursor: pointer; padding: 8px 12px"
        >
          <NText depth="3">New Chat</NText>
        </NListItem>
        <NListItem
          v-for="conversation in store.conversations"
          :key="conversation.id"
          :class="{
            active: conversation.id === store.currentConversationId,
          }"
          style="cursor: pointer; padding: 8px 12px"
        >
          <div
            v-if="editingId === conversation.id"
            style="
              display: flex;
              align-items: center;
              gap: 4px;
              width: 100%;
            "
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
              style="flex-shrink: 0"
            >
              <template #icon>
                <NIcon size="22"><Check /></NIcon>
              </template>
            </NButton>
          </div>
          <div
            v-else
            style="
              display: flex;
              align-items: center;
              gap: 4px;
              width: 100%;
            "
            @click="store.selectConversation(conversation.id)"
          >
            <NText
              style="
                flex: 1;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
              "
            >
              {{ conversation.title }}
            </NText>
            <NButton
              quaternary
              circle
              size="small"
              @click.stop="startEdit(conversation)"
              class="icon-btn"
            >
              <template #icon>
                <NIcon size="22"><Pencil /></NIcon>
              </template>
            </NButton>
          </div>
        </NListItem>
      </NList>
    </NLayoutSider>
    <NLayoutContent
      content-style="display: flex; flex-direction: column; height: 100vh;"
    >
      <slot />
    </NLayoutContent>
  </NLayout>
</template>

<style scoped>
.sidebar-title {
  font-size: 1.4em;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-align: center;
  margin-bottom: 16px;
  opacity: 0.85;
}

.active {
  background-color: var(--moira-border, #e0e0e0);
  border-radius: 4px;
}

.icon-btn {
  flex-shrink: 0;
  color: var(--n-primary-color);
  opacity: 0.65;
}

.icon-btn:hover {
  opacity: 1;
}
</style>
