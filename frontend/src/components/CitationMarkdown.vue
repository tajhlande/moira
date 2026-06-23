<script setup lang="ts">
import { ref, computed, watch, onMounted, inject, type Ref } from "vue";
import { initMarked } from "../markdown";
import type { ResearchReport } from "../api/client";

const props = defineProps<{
  content: string;
  citations: ResearchReport["citations"];
}>();
const isDark = inject<Ref<boolean>>("isDark", ref(false));

const processed = ref("");
const ready = ref(false);

let marked: Awaited<ReturnType<typeof initMarked>> | null = null;

const citeRefPattern = /\[(\d+)\]/g;
const skipTags = new Set(["CODE", "PRE", "A", "SCRIPT"]);

function annotateCitations(html: string): string {
  let result = "";
  let i = 0;
  const len = html.length;

  while (i < len) {
    if (html[i] === "<") {
      const close = html.indexOf(">", i);
      if (close === -1) {
        result += html.slice(i);
        break;
      }
      const tag = html
        .slice(i + 1, close)
        .split(/[\s/>]/)[0]
        .toUpperCase();
      result += html.slice(i, close + 1);
      i = close + 1;
      // Skip content inside self-closing or void-style tags we want to avoid
      if (skipTags.has(tag)) {
        const closeTag = `</${tag.toLowerCase()}>`;
        const end = html.toLowerCase().indexOf(closeTag, i);
        if (end !== -1) {
          result += html.slice(i, end + closeTag.length);
          i = end + closeTag.length;
        }
      }
      continue;
    }

    // Collect text until next '<'
    const nextTag = html.indexOf("<", i);
    const textEnd = nextTag === -1 ? len : nextTag;
    const text = html.slice(i, textEnd);

    result += text.replace(citeRefPattern, (match, numStr) => {
      const num = parseInt(numStr, 10);
      if (num < 1 || num > props.citations.length) return match;
      return `<a class="cite-ref" data-cite="${num}" href="#cite-${num}">${match}</a>`;
    });

    i = textEnd;
  }

  return result;
}

async function render() {
  if (!marked) {
    marked = await initMarked();
  }
  const raw = (await marked.parse(props.content)) as string;
  processed.value = annotateCitations(raw);
  ready.value = true;
}

onMounted(render);
watch(() => props.content, render);
</script>

<template>
  <div
    v-if="ready"
    :class="['markdown-content', isDark ? 'markdown-dark' : 'markdown-light']"
    v-html="processed"
  />
  <div
    v-else
    :class="[
      'markdown-content',
      'markdown-loading',
      isDark ? 'markdown-dark' : 'markdown-light',
    ]"
  >
    {{ content }}
  </div>
</template>
