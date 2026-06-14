# Structured Output Renderer

## Problem

`StepDetailContent.vue` renders structured output in two ways:
1. **Hardcoded branches** for specific keys (tool pills, verification report, report preview)
2. **Generic fallback**: `JSON.stringify` in a `<pre>` for everything else

The decomposition fields (`entities`, `concepts`, `unknown_facts`), planning `calls`,
synthesis `conclusions`, and verification `fact_results`/`conclusion_results` all fall
into the JSON dump. Additionally, the verification branch is dead code — it checks for
`outcome`/`case`/`assessment` keys that the backend never emits (the real schema uses
`route`/`goal_met`/`goal_assessment`).

## Approach: Field Renderer Registry + Type-Dispatching Fallback

A single component, `StructuredOutputRenderer.vue`, replaces the entire "Structured
Output" collapse section in `StepDetailContent.vue`.

### Layer 1 — Registry

A config object maps known knowledge-model field names to render specs. Each spec
declares a render type, a human-readable label, and optional type-specific config.

| Field | Render type | Label |
|---|---|---|
| `user_goal` | text | User Goal |
| `topic` | text | Topic |
| `goal_assessment` | text | Goal Assessment |
| `entities` | pill-list | Entities |
| `concepts` | pill-list | Concepts |
| `unknown_facts` | fact-cards | Unknown Facts |
| `new_unknown_facts` | string-list | New Unknown Facts |
| `calls` | object-list | Planned Calls |
| `conclusions` | object-list | Conclusions |
| `fact_results` | object-list | Fact Verification |
| `conclusion_results` | object-list | Conclusion Verification |
| `goal_met` | badge | Goal Met |
| `route` | badge | Route |
| `selected_tools` | pill-list | Selected Tools |
| `default_tools` | pill-list | Default Tools |
| `discovered_tools` | pill-list | Discovered Tools |

**Object-list** entries specify `itemFields` — an ordered list of sub-fields to render
inside each card, each with its own key, label, and render type. Example for `calls`:

```
itemFields: [
  { key: "tool", label: "Tool", type: "text" },
  { key: "args", label: "Args", type: "code" },
  { key: "target_fact_ids", label: "Target Facts", type: "pill-list" },
  { key: "rationale", label: "Rationale", type: "text" },
]
```

**Fact-cards** is a specialized object-list for `unknown_facts`, showing `subject` and
`fact_needed` as labeled lines in a flat card (definition-list style).

**Badge** entries can declare `variants` — a map from value to CSS class — or rely on
boolean truthiness (true = success, false = error).

### Layer 2 — Type-Dispatching Fallback

For any field **not** in the registry, the renderer dispatches by JavaScript type:

| Type | Rendering |
|---|---|
| `string` | Text block (pre-wrap) |
| `string[]` | Pill list (auto-detected) |
| `boolean` | Badge (Yes/No) |
| `number` | Plain display |
| `object[]` | Object-list with auto-discovered fields (union of keys across items) |
| `object` | Key-value grid with recursive fallback per value |

This means new knowledge-model fields always get a reasonable presentation, and we can
polish them later by adding one line to the registry.

### Layer 3 — Ordering

Fields render in JSON insertion order (matching the prompt's schema order), which is
already correct for all current node types.

## Render Types

### text
A `<div>` with `white-space: pre-wrap`. For short values like `user_goal` and `topic`,
this reads naturally without the overhead of a `<pre>` block.

### pill-list
A row of `.tool-tag` pills. Reuses the existing pill styling. Empty lists show "None".

### string-list
A `<ul>` of plain strings. Used for `new_unknown_facts`.

### badge
A colored span. For booleans: green "Yes" / red "No". For strings with `variants`:
maps value to CSS class (success/warning/error). Unmapped values get a neutral style.

### fact-cards
Flat cards for `unknown_facts` items. Each card shows `subject` and `fact_needed` as
labeled key-value lines. This is the same visual pattern as `object-list` but with a
fixed field layout (no `itemFields` config needed).

### object-list
Flat key-value cards. Each card renders the `itemFields` spec as labeled lines.
Sub-field render types follow the same dispatch: `text` → inline, `code` → monospace
block, `pill-list` → pills, `badge` → colored span.

## What Gets Removed

- Dead verification block (`isVerification`, `outcome`/`case`/`assessment`/claim lists)
- `genericEntries` JSON dump
- `isKnownKey` set, `prettyLabel`, `claimList`, `getToolNames`
- Dead CSS classes (`.verification-outcome.accept/.retry_plan/.retry_draft/.error`,
  `.verification-case`, `.verification-assessment`, `.retry-declined-note`,
  `.verification-claims`)

## What Stays

- `candidate_tools` and `queries` rendering (top-level `detail` keys, not `structured_output`)
- `generationPath` badge (top-level `detail` key)
- Tool execution list (top-level `detail.tool_results`)
- Prompt/thinking/response sections

## Files

| File | Change |
|---|---|
| **NEW** `StructuredOutputRenderer.vue` | Registry + type-dispatch + templates for each render type |
| `StepDetailContent.vue` | Replace structured-output section with `<StructuredOutputRenderer :so="so" />` |
| `workflow-artifacts.css` | Add `.so-badge`, `.so-card`, `.so-card-field` styles; remove dead verification CSS |

## Future Consideration

**Hybrid expandable cards:** If object-list items have >3 fields, auto-switch to
expandable cards with a summary line (first field) on top and details behind a
disclosure. This adds complexity but optimizes for objects with many fields.
Recorded for future evaluation.
