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
} from "naive-ui";
import { useChatStore } from "../stores/chat";
import { onMounted } from "vue";

const store = useChatStore();

onMounted(() => {
  store.fetchSessions();
});
</script>

<template>
  <NLayout has-sider style="height: 100vh">
    <NLayoutSider
      bordered
      width="260"
      :native-scrollbar="false"
      content-style="padding: 16px;"
    >
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
          v-for="session in store.sessions"
          :key="session.id"
          :class="{ active: session.id === store.currentSessionId }"
          @click="store.selectSession(session.id)"
          style="cursor: pointer; padding: 8px 12px"
        >
          <NText>{{ session.title }}</NText>
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
.active {
  background-color: var(--moira-border, #e0e0e0);
  border-radius: 4px;
}
</style>
