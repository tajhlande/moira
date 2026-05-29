import { Marked } from "marked";
import markedShiki from "marked-shiki";
import { createHighlighter } from "shiki";

import langBash from "@shikijs/langs/bash";
import langCss from "@shikijs/langs/css";
import langHtml from "@shikijs/langs/html";
import langJavascript from "@shikijs/langs/javascript";
import langJson from "@shikijs/langs/json";
import langMarkdown from "@shikijs/langs/markdown";
import langPython from "@shikijs/langs/python";
import langSql from "@shikijs/langs/sql";
import langTypescript from "@shikijs/langs/typescript";
import langYaml from "@shikijs/langs/yaml";

import themeDarkPlus from "@shikijs/themes/dark-plus";
import themeLightPlus from "@shikijs/themes/light-plus";

// Lazy-initialized singleton — the highlighter loads grammars asynchronously.
let highlighterInstance: Awaited<ReturnType<typeof createHighlighterCore>> | null = null;
let markedInstance: Marked | null = null;

const LANGUAGES = [
  langBash,
  langCss,
  langHtml,
  langJavascript,
  langJson,
  langMarkdown,
  langPython,
  langSql,
  langTypescript,
  langYaml,
] as const;

const LANG_IDS = [
  "bash",
  "css",
  "html",
  "javascript",
  "json",
  "markdown",
  "python",
  "sql",
  "typescript",
  "yaml",
];

// Fallback for unknown languages — plain <code> block with no highlighting
function plainCodeBlock(code: string, lang: string): string {
  const escaped = code
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return `<div class="code-block" data-lang="${lang || 'text'}"><div class="code-block-header"><span class="code-block-lang">${lang || "text"}</span><button class="code-copy-btn" title="Copy">Copy</button></div><pre><code>${escaped}</code></pre></div>`;
}

async function initHighlighter() {
  if (highlighterInstance) return highlighterInstance;

  // Uses the full shiki import which bundles the JS regex engine internally.
  // Falls back to the JS engine (no WASM needed) for broader compatibility.
  highlighterInstance = await createHighlighter({
    themes: [themeDarkPlus, themeLightPlus],
    langs: [...LANGUAGES],
  });
  return highlighterInstance;
}

// Builds the highlighted code block HTML with header (language label + copy button)
// and dual-theme output (light/dark via CSS classes).
function wrapHighlightedCode(
  highlightedHtml: string,
  rawCode: string,
  lang: string,
): string {
  // Shiki's dual-theme output uses CSS variables for color switching.
  // We wrap it in a container that toggles via a data attribute set by the app theme.
  const dataRaw = rawCode
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

  return (
    `<div class="code-block" data-lang="${lang}" data-raw="${dataRaw}">` +
    `<div class="code-block-header">` +
    `<span class="code-block-lang">${lang}</span>` +
    `<button class="code-copy-btn" title="Copy">Copy</button>` +
    `</div>` +
    `<div class="code-block-content">${highlightedHtml}</div>` +
    `</div>`
  );
}

export async function initMarked(): Promise<Marked> {
  if (markedInstance) return markedInstance;

  const highlighter = await initHighlighter();

  const md = new Marked({
    gfm: true,
    breaks: false,
    renderer: {
      link({ href, title, text }) {
        const t = title ? ` title="${title}"` : "";
        return `<a href="${href}"${t} target="_blank" rel="noopener noreferrer">${text}</a>`;
      },
    },
  });

  md.use(
    markedShiki({
      async highlight(code, lang) {
        const resolvedLang = LANG_IDS.includes(lang) ? lang : "";
        if (!resolvedLang) {
          return plainCodeBlock(code, lang);
        }

        // Dual-theme: light-plus and dark-plus. Shiki generates HTML with
        // CSS custom properties that switch based on .shiki-dark / .shiki-light
        // class presence on ancestor elements.
        const html = highlighter.codeToHtml(code, {
          lang: resolvedLang,
          themes: {
            light: "light-plus",
            dark: "dark-plus",
          },
        });

        return wrapHighlightedCode(html, code, resolvedLang);
      },
      container: "%s",
    }),
  );

  markedInstance = md;
  return md;
}

// Synchronous parse for cases where initMarked() hasn't been awaited yet.
// Falls back to raw text.
export function parseMarkdownSync(_text: string): string {
  return _text;
}
