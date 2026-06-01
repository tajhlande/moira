<script setup lang="ts">
import { ref, watch, onMounted, inject, type Ref } from "vue";
import { initMarked } from "../markdown";

const props = defineProps<{ content: string; inline?: boolean }>();
const isDark = inject<Ref<boolean>>("isDark", ref(false));

const rendered = ref("");
const ready = ref(false);

let marked: Awaited<ReturnType<typeof initMarked>> | null = null;

async function render() {
  if (!marked) {
    marked = await initMarked();
  }
  const raw = (await marked.parse(props.content)) as string;
  // Inline mode: strip wrapping <p> tags so the content flows inline
  // within list items and other block elements.
  if (props.inline) {
    rendered.value = raw.replace(/^<p>/, "").replace(/<\/p>\n?$/, "");
  } else {
    rendered.value = raw;
  }
  ready.value = true;
}

onMounted(render);
watch(() => props.content, render);

function handleClick(e: MouseEvent) {
  const target = e.target as HTMLElement;
  const btn = target.closest(".code-copy-btn") as HTMLElement | null;
  if (!btn) return;

  const block = btn.closest(".code-block") as HTMLElement | null;
  if (!block) return;

  const raw = block.getAttribute("data-raw") || "";
  // Decode HTML entities back to plain text for clipboard
  const decoded = raw
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"');

  navigator.clipboard.writeText(decoded).then(() => {
    btn.textContent = "Copied!";
    setTimeout(() => {
      btn.textContent = "Copy";
    }, 2000);
  });
}
</script>

<template>
  <component
    :is="inline ? 'span' : 'div'"
    v-if="ready"
    :class="['markdown-content', isDark ? 'markdown-dark' : 'markdown-light']"
    @click="handleClick"
    v-html="rendered"
  />
  <component
    :is="inline ? 'span' : 'div'"
    v-else
    :class="[
      'markdown-content',
      'markdown-loading',
      isDark ? 'markdown-dark' : 'markdown-light',
    ]"
  >
    {{ content }}
  </component>
</template>

<style>
@import "./markdown-content.css";
</style>
