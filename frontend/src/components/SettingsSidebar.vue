<script setup lang="ts">
import { NMenu } from "naive-ui";
import { IconSettings, IconChartLine } from "@tabler/icons-vue";
import { useRoute, useRouter } from "vue-router";
import { computed, h } from "vue";

const route = useRoute();
const router = useRouter();

const menuItems = [
  { label: "System", key: "system", icon: IconSettings },
  { label: "Analytics", key: "analytics", icon: IconChartLine },
];

const activeKey = computed(() => {
  if (route.path.endsWith("/analytics")) return "analytics";
  return "system";
});

function handleMenuUpdate(key: string) {
  router.push({ name: `settings-${key}` });
}
</script>

<template>
  <NMenu
    :options="
      menuItems.map((item) => ({
        label: item.label,
        key: item.key,
        icon: () => h(item.icon),
      }))
    "
    :value="activeKey"
    @update:value="handleMenuUpdate"
  />
</template>
