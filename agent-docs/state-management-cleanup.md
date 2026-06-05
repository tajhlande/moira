# State Management Cleanup Plan

Status: **Planned**

## Goal

Eliminate frontend/backend run-state drift by making run snapshots the single source of truth, and by unifying API and stream payloads around the same schema.

Primary outcomes:

1. The UI always reflects current workflow state after reload, reconnect, and route changes.
2. "Live" and "persisted" run rendering paths are unified.
3. Stream handling is idempotent and ordering-safe.

---

## Current Problems (Observed)

1. The frontend stores run data in two parallel models:
   - persisted runs map
   - separate live refs (`executionSteps`, `currentStep`, `toolExecutions`, `currentReport`, etc.)
2. Chat rendering has duplicated branches for "live" vs "persisted" run artifacts.
3. Stream replay and regular API hydration use different data semantics, creating merge ambiguity.
4. Event ordering edge cases (`run_complete` before terminal `node_end`) require local reconstruction logic.
5. Navigation and reconnect can apply stale async results without version guards.

---

## Target Design

## Single State Contract

Define one canonical run snapshot used everywhere for list/status rendering:

- `GET /conversations/{id}` returns runs as canonical `WorkflowRunSnapshot`.
- SSE emits `run_snapshot` events carrying the same `WorkflowRunSnapshot` shape.
- Frontend store only upserts snapshots; it does not reconstruct run state from low-level events.
- Heavy per-step artifacts are lazy-loaded on demand from detail endpoints.

### Canonical Run Snapshot (proposed)

```text
WorkflowRunSnapshot
  id: string
  conversation_id: string
  user_message_id: number
  status: running | completed | stopped | error
  budget_limit: number
  budget_consumed: number
  total_elapsed_ms?: number
  error: string
  report: ResearchReport | null
  execution_steps: ExecutionStepSummary[]
  state_version: number
  started_at: string
  completed_at?: string
  updated_at: string
```

`state_version` must monotonically increase every time persisted run state changes.
This includes any condition that causes a step's own `step_version` number to increase.

### Execution Step Summary (always present)

`execution_steps` in `WorkflowRunSnapshot` should contain only metadata needed for normal chat rendering:

```text
ExecutionStepSummary
  id: string
  node: string
  label: string
  status: running | completed | error | stopped
  cost: number
  budget_remaining: number
  elapsed_ms?: number
  started_at?: string
  error?: string
  tool_call_count: number
  step_version: number
  has_detail: boolean
```

`step_version` must monotonically increase per step whenever that step's detail payload or status changes.

### Lazy Step Detail (on expand)

Full detail blobs are fetched only when the user expands a step row:

- `GET /runs/{run_id}/steps/{step_id}/detail` -> `ExecutionStepDetail`
- UI shows a loading spinner in the expanded panel until detail resolves.
- UI caches by `(run_id, step_id, step_version)`.
- If step summary updates with a higher `step_version`, cached detail is invalidated and reloaded on next expand.

`ExecutionStepDetail` contains heavy fields such as prompt messages, thinking blocks, structured output, and full tool call arguments/results.

## Store Architecture

Frontend store becomes snapshot-driven:

- Keep a single `runsByMessageId: Map<number, WorkflowRunSnapshot>` for selected conversation.
- Remove parallel live refs (`executionSteps/currentStep/toolExecutions/currentReport/...`).
- UI derives "running" state from `run.status === "running"`.
- Resume/stop actions mutate backend, then snapshots update state.

## Stream Semantics

Use stream for change delivery, not custom local state assembly.

- `run_snapshot` event: canonical lightweight snapshot for one run.
- `run_status` global event remains for sidebar signal only.
- Optional: keep legacy events temporarily for compatibility, but frontend v2 should ignore them.
- Detail payloads are not pushed over SSE except by explicit future opt-in debug channel.

---

## Migration Strategy

Roll out in six phases with compatibility windows.

## Phase 0 - Stabilization Fixes (Before Refactor)

Apply small correctness fixes first to reduce noise while migrating:

1. Fix frontend finalize status mapping (`stopped` should not become `error`).
2. Fix `budget_limit` derivation when `budget_remaining === 0`.
3. Ensure `RunManager.start_run()` honors per-run budget passed from API settings.
4. Add async request version guards in conversation selection to prevent stale route writes.

Deliverable: current UI behavior is less brittle while deeper refactor proceeds.

## Phase 1 - Backend Snapshot Contract

Backend exposes canonical snapshot schema consistently.

### Changes

1. Add `state_version` and `updated_at` to persisted run metadata.
2. Add serializer in backend that builds `WorkflowRunSnapshot` from run + step summaries.
3. Update `GET /conversations/{id}` to return canonical snapshots.
4. Extend `RunManager` to broadcast `run_snapshot` whenever run state mutates.
5. Add per-step `step_version` tracking and `tool_call_count` summary field.
6. Add lazy detail endpoint: `GET /runs/{run_id}/steps/{step_id}/detail`.
7. Keep existing event types for backward compatibility during transition.

### Files (expected)

- `backend/moira/workflow/run_manager.py`
- `backend/moira/api/router.py`
- `backend/moira/persistence/interfaces.py`
- `backend/moira/persistence/sqlite/repos.py`
- SQLite schema migration file for `workflow_runs` columns

### Tests

1. `state_version` strictly increases across node/tool/report/error updates.
2. `run_snapshot` payload equals run shape returned by conversation API.
3. Reconnect receives current snapshot and converges without replay reconstruction.
4. `step_version` strictly increases when step detail changes.
5. Step detail endpoint returns full payload matching latest `(step_id, step_version)`.

## Phase 2 - Frontend Store Refactor (Single Source)

Replace dual state model with snapshot-only store.

### Changes

1. In `frontend/src/stores/chat.ts`:
   - remove live refs (`executionSteps`, `currentStep`, `toolExecutions`, `currentReport`, etc.)
   - maintain `runsByMessageId` as canonical run state
   - add `upsertRunSnapshot(snapshot)` with `state_version` guard
   - add `stepDetailsByKey` cache keyed by `(run_id, step_id, step_version)`
   - add `loadingStepDetails` state for per-step loading spinners
2. Stream handler only parses:
   - `run_snapshot` -> `upsertRunSnapshot`
   - `run_status` -> sidebar running badge set
3. On connect/reconnect:
   - hydrate conversation from API first
   - then subscribe to stream
4. Add per-request epoch guard for `selectConversation(id)` to ignore stale async completions.
5. Add `loadStepDetail(runId, stepId, stepVersion)` action with cache + in-flight dedupe.

### Tests

1. Older snapshot (lower `state_version`) is ignored.
2. Conversation switch mid-request does not overwrite newer selection.
3. Reload + reconnect reproduces same run state as API snapshot.
4. Expanding a step shows loading state then detail payload.
5. Step detail cache invalidates when `step_version` increases.

## Phase 3 - Conversation UI Unification

Remove live/persisted bifurcation in components.

### Changes

1. `ChatView.vue` always renders run artifacts via one code path:
   - after each user message, lookup run in canonical map
   - render `RunArtifacts` for all statuses including `running`
2. Enhance `RunArtifacts.vue` to support running-state visuals (spinner/live elapsed).
3. Delete duplicate live-run artifact template from `ChatView.vue`.
4. Step rows always render from `ExecutionStepSummary`, including `tool_call_count`.
5. Expanded step panels use lazy detail fetch and show spinner while loading.

### Benefits

1. One renderer for one data model.
2. No "switch from live branch to persisted branch" race.
3. Fewer conditional bugs and simpler tests.
4. Smaller baseline payloads for conversation hydrate and stream updates.

### Tests

1. Running run displays same component before/after completion.
2. Steps remain visible through `run_complete` and post-render updates.
3. Resume/retry buttons work from unified component state.
4. Tool call badges reflect `tool_call_count` without loading detail.
5. Expanding detail works for both running and completed steps.

## Phase 4 - Stream Replay Simplification

Stop replaying synthetic low-level events for frontend state assembly.

### Changes

1. `ActiveRun.subscribe()` replays current canonical snapshot first.
2. Live updates publish only snapshot changes and optional lightweight status signals.
3. Keep low-level events only for debugging channels (if needed), not UI state.
4. Keep SSE payload focused on run + step summary fields, not full detail blobs.

### Tests

1. Late subscriber sees current run immediately.
2. No duplicated steps/tool entries after reconnect.
3. Event order no longer affects rendered correctness.
4. Snapshot payload size remains bounded as run detail volume grows.

## Phase 5 - Compatibility Cleanup

After frontend migration is complete and stable:

1. Remove legacy frontend handling for `node_start/node_end/tool_result/run_complete` state assembly.
2. Remove backend compatibility emissions if no consumers remain.
3. Trim dead store fields and obsolete unit tests.

---

## API and Protocol Notes

## Idempotency Rule

Frontend must apply snapshots only when:

```text
incoming.state_version > stored.state_version
```

Equal or lower versions are no-ops.

## Ordering Rule

`state_version` is authoritative; arrival order is not.

## Reconnect Rule

Reconnect sequence:

1. Fetch conversation snapshot.
2. Render immediately.
3. Subscribe to stream.
4. Apply only newer snapshots.

## Step Detail Freshness Rule

Frontend detail cache validity rule:

```text
cached detail is valid only when
cached.step_version === summary.step_version
```

If `summary.step_version` is greater, detail is stale and must be refetched.

## Payload Size Rule

Conversation hydrate and `run_snapshot` payloads should include step summaries only.
Large fields (prompt text, thinking, full tool args/output) must be served through lazy detail endpoints.

---

## Verification Plan

## Backend

1. Unit tests for snapshot serializer consistency.
2. Unit tests for `state_version` progression.
3. Integration tests for:
   - start run + stream snapshots
   - late subscribe replay
   - stop/resume with version continuity
4. Integration test for lazy step detail endpoint during running and completed states.

## Frontend

1. Store tests for version-guarded upsert behavior.
2. Component tests for unified run rendering (running/completed/stopped/error).
3. Route tests for rapid conversation switching and reconnect.
4. Component tests for step expand spinner -> detail render flow.

## End-to-End Scenarios

1. Start run, close tab, reopen same conversation: state is correct.
2. Start run, navigate to another conversation, return: state is current.
3. Stop run, resume run: step history and status remain coherent.
4. Stream disconnect/reconnect during active run: no duplicated/missing artifacts.
5. Expand a running step, then receive newer step summary: next expand reloads stale detail by `step_version`.

---

## Rollout and Risk Control

1. Ship backend snapshot support first while preserving old events.
2. Gate frontend snapshot store path behind a temporary feature flag.
3. Run both old/new pipelines in tests until parity is confirmed.
4. Remove legacy path only after snapshot path passes reconnect/navigation matrix.

Key risk: partial migration where some components still depend on removed live refs.

Mitigation:

- migrate store + ChatView + RunArtifacts in a single PR slice
- keep strict TypeScript errors enabled to catch stale field usage

---

## Completion Criteria

This migration is complete when all are true:

1. Frontend no longer maintains duplicate live run state structures.
2. Conversation and stream endpoints use the same run snapshot schema.
3. UI run artifacts render through one component path for all statuses.
4. Reload/reconnect/navigation scenarios pass consistently in automated tests.
5. Legacy event-based state assembly logic is removed.
6. Step detail panels are lazy-loaded with spinner UX and refreshed by `step_version`.
