# Per-Conversation Model Selection

## Goal

Surface intelligence model selection in the conversation UI via a header pill +
popover, with per-conversation overrides layered on top of global defaults.

## Motivation

Currently, model selection is global — changing the intelligence model in Settings
affects all conversations. Users may want different models for different research
tasks (e.g., a fast local model for quick questions, a premium API model for deep
research). The conversation UI should let users switch models without leaving the
conversation, following MOiRA's layered configuration design convention.

## Key Decisions

1. **Persist per conversation** — overrides are stored in a dedicated
   `conversation_models` table, surviving page refresh and reruns. Switching model
   in conversation A does not affect conversation B.
2. **Intelligence only** — the conversation UI controls the intelligence model
   only. The task model stays in Settings (it's an infrequent background concern).
3. **Header pill + popover** — compact clickable pill in the conversation header
   showing provider display name + model ID. Click opens a searchable popover with
   all models grouped by provider.
4. **Provider + model ID display** — the pill shows two lines: provider display
   name (line 1) and model ID (line 2, truncated if long). No separate display name
   field needed — uses existing `inference_models.model_id` and
   `inference_providers.display_name`.
5. **Dedicated table** (not the scalar settings table) — consistent with the
   inference-settings-migration design doc's choice of dedicated tables for model
   config. The settings scope system is built but unused for non-system scopes;
   using it here would split model config between two systems unnecessarily.

## Architecture Challenge

`conversation_id` is available at the API boundary but never reaches the workflow
nodes. It exists in `ActiveRun.conversation_id` but is not injected into
`config["configurable"]` or `ResearchState`. The 7 workflow nodes that call
`resolve("intelligence")` have no way to know which conversation they are running
for.

### Current resolve() call sites

| File | Purpose | conversation_id available? |
|------|---------|---------------------------|
| `conversations.py:396` (title generation) | task | YES (path param) |
| `enrichment.py:100` (tool enrichment) | task | NO (batch background job) |
| `workflow/nodes/decomposition.py:58` | intelligence | NO |
| `workflow/nodes/planning.py:198` | intelligence | NO |
| `workflow/nodes/research.py:1156` | intelligence | NO |
| `workflow/nodes/synthesis.py:94` | intelligence | NO |
| `workflow/nodes/research_review.py:124` | intelligence | NO |
| `workflow/nodes/evaluation.py:103` | intelligence | NO |
| `workflow/nodes/report_generation.py:225` | intelligence | NO |

### Current conversation_id flow

```
POST /api/conversations/{conversation_id}/messages
│
├─ streaming.py:226-234   builds graph_config["configurable"]
│      ↳ keys: thread_id, moira_config, prior_report, prior_question, prior_turns
│      ↳ conversation_id NOT included
│
├─ run_manager.py:107-110  injects run_id into config
│      ↳ conversation_id NOT injected
│
└─► LangGraph nodes: node(state, config)
      ↳ config["configurable"] has NO conversation_id
      ↳ state has NO conversation_id
      ↳ node calls: registry.resolve("intelligence") — purpose only
```

## Schema (Migration 021)

```sql
CREATE TABLE IF NOT EXISTS conversation_models (
    conversation_id       TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    intelligence_endpoint TEXT NOT NULL,
    intelligence_model    TEXT NOT NULL,
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Only intelligence fields — task model stays global. FK cascade ensures overrides
are cleaned up when conversations are deleted.

## Resolve Chain

```
resolve("intelligence", conversation_id="abc-123")
  │
  ├─ 1. Check conversation_models WHERE conversation_id = "abc-123"
  │     ↳ Found? Use override's endpoint + model
  │
  └─ 2. Fall back to model_preferences (global default)
        ↳ intelligence_endpoint + intelligence_model from user prefs
```

Task model resolution is unchanged — always reads from `model_preferences`.

## Implementation Phases

### Phase 1: Backend Storage + Resolve Chain

**Files created:**
- `backend/moira/persistence/sqlite/migrations/021_conversation_models.sql`
- `backend/moira/persistence/sqlite/repos/conversation_models.py`

**Files modified:**
- `backend/moira/persistence/sqlite/schema.py` — bump `CURRENT_VERSION` to 21
- `backend/moira/persistence/interfaces.py` — add
  `ConversationModelOverride` dataclass + `ConversationModelRepository` ABC
- `backend/moira/inference/registry.py` — `resolve()` gains `conversation_id`
  parameter; constructor gains `conversation_model_repo` parameter
- `backend/moira/service_setup.py` — wire up new repo, pass to registry
- `backend/moira/api/streaming.py` — inject `conversation_id` into
  `graph_config["configurable"]` in `send_message` and `rerun_message`
- `backend/moira/workflow/run_manager.py` — inject `conversation_id` into
  `graph_config["configurable"]` in `resume_run`
- `backend/moira/workflow/nodes/_helpers.py` — new
  `_resolve_intelligence(config)` helper that extracts `conversation_id` from
  config and calls `resolve("intelligence", conversation_id=...)`
- `backend/moira/workflow/nodes/decomposition.py` — replace resolve call
- `backend/moira/workflow/nodes/planning.py` — replace resolve call
- `backend/moira/workflow/nodes/research.py` — replace resolve call
- `backend/moira/workflow/nodes/synthesis.py` — replace resolve call
- `backend/moira/workflow/nodes/research_review.py` — replace resolve call
- `backend/moira/workflow/nodes/evaluation.py` — replace resolve call
- `backend/moira/workflow/nodes/report_generation.py` — replace resolve call

**Node change pattern** (one-line each):

Before:
```python
registry = _get_model(config)
resolved = await registry.resolve("intelligence")
```

After:
```python
resolved = await _resolve_intelligence(config)
```

### Phase 2: API Endpoints

**Files created:**
- `backend/moira/api/routes/conversation_model.py` (or add to `conversations.py`)

**Endpoints:**

| Method | Path | Body | Response |
|--------|------|------|----------|
| GET | `/api/conversations/{id}/model` | — | `{endpoint, model, overridden: bool}` |
| PUT | `/api/conversations/{id}/model` | `{endpoint, model}` | `{endpoint, model}` |
| DELETE | `/api/conversations/{id}/model` | — | `{status: "reset"}` |

The `overridden` flag in the GET response tells the frontend whether to show a
"Reset to default" option.

### Phase 3: Frontend

**Files modified:**
- `frontend/src/api/client.ts` — add `getConversationModel`,
  `setConversationModel`, `resetConversationModel` methods

**Files created or modified:**
- Conversation header component — add model pill showing:
  - Line 1: provider display name (e.g., "OpenRouter")
  - Line 2: model ID, truncated if long (e.g., "deepseek/deepseek-v4-flash")
- Popover component — model selection with:
  - Search/filter input at top (essential for providers with hundreds of models)
  - Models grouped by provider with provider display name as section headers
  - Current selection highlighted
  - "Reset to default" button if an override is active

**Pill display:**
```
┌──────────────────────────┐
│  OpenRouter              │
│  deepseek/deepseek-v4…   │
└──────────────────────────┘
```

**Popover layout:**
```
┌─────────────────────────────────┐
│ [🔍 Search models...]           │
│─────────────────────────────────│
│ OpenRouter                      │
│   gpt-4o                    ✓   │
│   claude-3.5-sonnet            │
│   deepseek/deepseek-v4-flash   │
│ Local Lab                       │
│   llama3:70b                   │
│   qwen2.5:32b                  │
│─────────────────────────────────│
│ Reset to default                │
└─────────────────────────────────┘
```

### Phase 4: Tests

**Files created:**
- `backend/tests/test_conversation_models.py` — repo CRUD tests
- API endpoint tests (in `test_inference_api.py` or new file)
- Registry resolve-with-override test (in `test_registry.py`)

**Test cases:**
- Repo: upsert + get, delete, cascade on conversation delete
- API: get with no override (returns global default, `overridden: false`),
  get with override, set override, delete override, invalid conversation_id
- Registry: resolve with conversation override returns override values,
  resolve without override returns global default, resolve with non-existent
  override falls back to global

## File Impact Summary

| Action | Files |
|--------|-------|
| New | `021_conversation_models.sql`, `repos/conversation_models.py`, conversation model API routes, frontend pill/popover, `test_conversation_models.py` |
| Modified | `schema.py`, `interfaces.py`, `registry.py`, `service_setup.py`, `streaming.py`, `run_manager.py`, `_helpers.py`, 7 workflow node files, `client.ts`, conversation header component, `test_registry.py` |

## Future: Cost Surfacing (Phase 2, deferred)

Not part of this plan but designed to accommodate future cost features:
- Cost tier badges (`$`/`$$`/`$$$`) in the popover for providers that expose pricing
- OpenRouter's `/models` response includes `pricing.prompt` and `pricing.completion`
- Would require storing pricing in `inference_models` during discovery
- Cost-per-run estimate based on historical data from the budget system
