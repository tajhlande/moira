# TipTap Rich Text Input

Status: **Planned**

## Goal

Replace the plain `NInput` in ChatView.vue with a TipTap rich text editor
that supports basic formatting, paste-from-rich-text, and markdown
serialization. The backend API remains unchanged — it always receives and
returns a plain `content: string`.

## Why TipTap

- **Bidirectional markdown**: `@tiptap/markdown` extension provides
  `editor.getMarkdown()` for output and `setContent(text,
  { contentType: 'markdown' })` for input. No format conversion needed at
  the API boundary.
- **Paste handling**: ProseMirror (TipTap's engine) converts pasted rich
  text (HTML, Word, etc.) into structured content automatically. Plain text
  and markdown paste cleanly as-is.
- **Headless**: No opinionated toolbar or chrome. Can be styled to look
  identical to the current input area. Users who type plain text won't
  notice a difference.
- **Vue 3 first-party support**: `@tiptap/vue-3` provides `useEditor` +
  `EditorContent` with `<script setup>` support.
- **Extensible**: StarterKit covers bold, italic, headings, lists, code
  blocks, blockquotes. Add only what's needed.

## Packages

| Package | Purpose |
|---|---|
| `@tiptap/vue-3` | Vue 3 integration (`useEditor`, `EditorContent`) |
| `@tiptap/starter-kit` | Basic extensions (bold, italic, headings, lists, code, blockquote) |
| `@tiptap/markdown` | Markdown serialization/deserialization |
| `@tiptap/pm` | ProseMirror core (peer dependency) |

## Architecture

```
User types/formats in TipTapInput.vue
  → editor.getMarkdown() extracts plain markdown string
  → store.sendMessage(markdownString)
  → backend API receives content: string (unchanged)
  → responses rendered via MarkdownContent.vue (marked + shiki)
```

### Layers affected

| Layer | Change |
|---|---|
| Backend | **None** — API still receives/sends plain `content: string` |
| Frontend input | Replace `NInput` in ChatView.vue with `TipTapInput.vue` component |
| Markdown bridge | `editor.getMarkdown()` extracts markdown before sending to API |
| Styling | Style TipTap editor to match current input area (border, padding, height) |
| Submit | Enter submits (like current NInput), Shift+Enter inserts newline |

## Implementation Steps

1. Install packages: `@tiptap/vue-3`, `@tiptap/starter-kit`, `@tiptap/markdown`
2. Create `TipTapInput.vue` component:
   - `useEditor` with StarterKit + Markdown extension
   - Style to match current input area
   - Enter/Shift+Enter handling for submit vs newline
   - Expose `getContent(): string` returning `editor.getMarkdown()`
   - Expose `clear()` to reset editor after submit
3. Update `ChatView.vue`:
   - Replace `NInput` with `TipTapInput`
   - Wire submit logic to call `getContent()` then `clear()`
4. Style the editor:
   - Match current border, padding, font size
   - Code blocks inline (backtick styling)
   - Dark mode support via NaiveUI theme

## Open Questions

- **Toolbar**: Visible toolbar (bold/italic/code buttons) vs keyboard-only
  (Ctrl+B, Ctrl+I, backtick for code)? Toolbar adds discoverability but
  clutter. Keyboard-only keeps it minimal.
- **Min/max height**: Should the editor auto-expand as the user types, or
  have a fixed height with scroll?
- **Message history display**: Should past user messages in the scroll also
  render via TipTap, or continue using `MarkdownContent.vue` (which already
  handles markdown display)? Recommendation: keep using `MarkdownContent.vue`
  for display — TipTap is only for input.
