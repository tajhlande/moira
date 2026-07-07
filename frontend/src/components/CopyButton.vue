<script setup lang="ts">
import { ref } from "vue";
import { NButton } from "naive-ui";
import { IconCopy, IconCircleCheck } from "@tabler/icons-vue";

const props = withDefaults(
  defineProps<{
    text: string;
    size?: number;
    title?: string;
  }>(),
  {
    size: 14,
    title: "Copy to clipboard",
  },
);

const copied = ref(false);

async function copy() {
  try {
    await navigator.clipboard.writeText(props.text);
    copied.value = true;
    setTimeout(() => {
      copied.value = false;
    }, 1500);
  } catch {
    // Clipboard API may be unavailable (non-secure context); fail silently.
  }
}
</script>

<template>
  <NButton
    quaternary
    circle
    size="tiny"
    class="icon-action-btn"
    :title="title"
    @click="copy"
  >
    <template #icon>
      <IconCopy v-if="!copied" :size="size" />
      <IconCircleCheck v-else :size="size" />
    </template>
  </NButton>
</template>
