<script setup lang="ts">
import { NButton } from "naive-ui";
import { IconMessage, IconTools, IconSettings } from "@tabler/icons-vue";
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";

const route = useRoute();
const router = useRouter();

type NavMode = "conversations" | "tools" | "settings";

interface NavItem {
  mode: NavMode;
  icon: any;
  label: string;
  route: { name: string };
}

const items: NavItem[] = [
  {
    mode: "conversations",
    icon: IconMessage,
    label: "Conversations",
    route: { name: "new-conversation" },
  },
  { mode: "tools", icon: IconTools, label: "Tools", route: { name: "tools" } },
  {
    mode: "settings",
    icon: IconSettings,
    label: "Settings",
    route: { name: "settings" },
  },
];

const activeMode = computed<NavMode>(() => {
  const meta = route.meta?.sidebar;
  if (meta === "conversations") return "conversations";
  if (meta === "tools") return "tools";
  if (meta === "settings") return "settings";
  return "conversations";
});

function navigate(item: NavItem) {
  router.push(item.route);
}
</script>

<template>
  <div class="nav-tray">
    <NButton
      v-for="item in items"
      :key="item.mode"
      quaternary
      circle
      size="large"
      :type="activeMode === item.mode ? 'primary' : 'default'"
      :title="item.label"
      @click="navigate(item)"
    >
      <template #icon>
        <component :is="item.icon" :size="22" />
      </template>
    </NButton>
  </div>
</template>

<style scoped>
.nav-tray {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 8px;
  min-height: var(--bottom-bar-height, 66px);
  box-sizing: border-box;
  padding: 0 16px;
  border-top: 1px solid var(--moira-border, #e0e0e0);
  flex-shrink: 0;
}
</style>
