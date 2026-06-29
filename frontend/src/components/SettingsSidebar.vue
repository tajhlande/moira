<script setup lang="ts">
import { NMenu } from "naive-ui";
import { IconSettings, IconChartLine, IconBug, IconCpu } from "@tabler/icons-vue";
import { useRoute, useRouter } from "vue-router";
import { computed, h } from "vue";

const route = useRoute();
const router = useRouter();

const menuItems = [
  { label: "System", key: "system", icon: IconSettings },
  { label: "Inference", key: "inference", icon: IconCpu },
  { label: "Analytics", key: "analytics", icon: IconChartLine },
  { label: "Debug", key: "debug", icon: IconBug },
];

const activeKey = computed(() => {
  if (route.path.endsWith("/inference")) return "inference";
  if (route.path.endsWith("/analytics")) return "analytics";
  if (route.path.endsWith("/debug")) return "debug";
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
