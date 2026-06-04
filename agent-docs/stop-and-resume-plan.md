# Stop and Resume Plan

## User Requirements

- A "Stop" button replaces the "Send" button when a run is active. No confirmation required.
- The stopped step shows a stop icon where the spinner or checkmark normally appears.
- A "Resume" button appears to the right of the steps container, bottom-aligned, when a step was stopped.
- "stopped" is a distinct outcome in the database and the UI from "completed", "error", and "running".
- On resume, the stopped node restarts from scratch with the same inputs. Partial work from the interrupted node is discarded.
- Concurrent runs across conversations are unaffected.

## Architecture

### Two-mechanism approach: task cancellation + cooperative flag

**Primary: `asyncio.Task.cancel()`** for immediate mid-node interruption. When the user presses Stop, the graph task is cancelled, injecting `CancelledError` at the next `await` point (typically the LLM HTTP call). This gives near-instant feedback — the currently-running step is stopped, not just the next one.

**Secondary: cooperative `_stop_requested` flag** checked via `_check_stop()` at the start of all 7 node functions. This uses LangGraph's `interrupt()` to pause the graph cleanly at a node boundary and save a checkpoint. It serves as a safety net: if task cancellation somehow doesn't take effect (e.g., the coroutine is between `await` points), the flag ensures the next node will still stop.

### Why not `interrupt()` alone

LangGraph's `interrupt()` is a **cooperative, node-boundary** mechanism designed for human-in-the-loop workflows. It can only take effect when code explicitly calls it. In our case, `_check_stop()` calls `interrupt()` at the top of each node — but if the user presses Stop during planning's 15-second LLM call, nothing happens until planning finishes and the next node starts. The user sees the step they wanted to stop complete normally and a different step show as stopped. This violates the "stop more or less immediately" expectation.

`interrupt()` cannot interrupt a mid-flight `await chat_completion()` because:
1. It must be called explicitly — it cannot be injected into a running coroutine from outside.
2. The LLM call is an atomic async operation with no internal cancellation points.
3. It is cooperative by design — it only works when the code reaches the call site.

### Why task cancellation is safe here

`asyncio.Task.cancel()` raises `CancelledError` at the next `await` point. In practice this is the HTTP request to the LLM, which httpx cancels cleanly. The `CancelledError` handler in `_run_graph()` / `_run_graph_resume()`:

1. Calls `_handle_stop()` which marks the current step as "stopped", persists it to the `workflow_steps` table, and broadcasts `run_stopped`.
2. Uses `asyncio.shield()` for the async persistence calls so they complete even though the task is cancelled.
3. Skips assistant message insertion for stopped runs (no "Unable to generate" fallback).
4. Does synchronous cleanup (`_broadcast(_STREAM_END)`, `remove_run()`).

The cooperative flag + `interrupt()` remain useful for the **resume** path: when we resume with `Command(resume=True)`, LangGraph re-executes the interrupted node from scratch. The new `ActiveRun` has `_stop_requested = False`, so `_check_stop()` passes and the node proceeds normally.

### Resume mechanism

Resume uses LangGraph's checkpoint/resume semantics:
1. Load the stopped `WorkflowRun`'s `thread_id` from the DB.
2. Create a new `ActiveRun` with a new `run_id` but the same `thread_id` and `conversation_id`.
3. Call `graph.astream(Command(resume=True), config={"configurable": {"thread_id": ..., "run_id": ...}})`.
4. LangGraph picks up from the checkpoint and re-executes the interrupted node from scratch.
5. The `_check_stop()` call in the node sees `_stop_requested = False` on the new `ActiveRun` and proceeds.

### Backend Changes

#### 1. `ActiveRun.stop_run()` — `run_manager.py`

- Set `_stop_requested` flag (for cooperative node-boundary check via `_check_stop()`).
- Cancel `self._graph_task` via `asyncio.Task.cancel()` for immediate mid-node interruption.

#### 2. `ActiveRun._handle_stop()` — `run_manager.py`

- Mark `_current_step["status"] = "stopped"`, compute elapsed time.
- Persist the stopped step to `workflow_steps` via `_persist_step()` (shielded from re-cancellation).
- Persist run status = "stopped" via `_persist()` (shielded).
- Broadcast `run_stopped` SSE event with stopped node name.

#### 3. `ActiveRun._run_graph()` / `_run_graph_resume()` — `run_manager.py`

- `except asyncio.CancelledError`: if `_stop_requested`, call `_handle_stop()`.
- `except Exception`: existing error handling (unchanged).
- After handlers: if `status == "stopped"`, skip assistant message insertion, do sync cleanup, return early.

#### 4. `RunManager.resume_run()` — `run_manager.py`

- Load stopped `WorkflowRun` from DB to get `thread_id`.
- Create new `ActiveRun` with same `conversation_id`, `user_message_id`, `thread_id`.
- Carry forward `budget_remaining` from the stopped run.
- Start graph via `start_graph_resume()` with `Command(resume=True)`.
- Broadcast global `run_status: running` for sidebar.

#### 5. `_check_stop()` — `research_nodes.py`

- Shared helper called at the start of all 7 node functions.
- Looks up the `ActiveRun` via `RunManager` using `run_id` from config.
- If `_stop_requested`, emits `node_start` then calls `interrupt("user_stop")`.
- Primarily serves the resume path (flag is cleared on new `ActiveRun`).

#### 6. API Endpoints — `streaming.py`

**`POST /api/conversations/{conversation_id}/runs/stop`**
- Looks up the `ActiveRun` by conversation_id.
- Confirms that the run is actually running.
- Calls `active_run.stop_run()`.
- Returns `{"status": "stopped"}`.

**`POST /api/conversations/{conversation_id}/runs/resume`**
- Fetches the `WorkflowRun` from DB to get `thread_id` and state.
- Confirms that the run was previously stopped.
- Creates a new `ActiveRun` and starts the graph from checkpoint.
- Returns `{"run_id": ..., "user_message_id": ...}`.
- Frontend reconnects SSE stream as normal.

#### 7. Status Values

- `WorkflowRun.status`: `"stopped"` alongside `"running"`, `"completed"`, `"error"`.
- `WorkflowStep.status`: `"stopped"`. No schema change needed — column is TEXT.
- No schema migration needed.

### Frontend Changes

#### 8. `chat.ts` Store

- `stopRun()`: POST to stop endpoint.
- `resumeRun()`: POST to resume endpoint, then `connectStream()`.
- `runStopped` ref: tracks whether the current run was stopped.
- Handle `run_stopped` SSE event: mark `currentStep.status = "stopped"`, push to `executionSteps`, set `loading = false`.
- Reset `runStopped` in `resetWorkflowState()`.

#### 9. Stop Button — `ChatView.vue`

Replace the Send button with conditional rendering:
- `store.loading === true` → show Stop button (warning style, `IconPlayerStop`).
- `store.loading === false` → show Send button (disabled when input empty).

#### 10. Resume Button — `ChatView.vue`

- Appears to the right of the `steps-container` div, bottom-aligned.
- Visible when `store.runStopped && !store.loading`.
- Wrapped in a `steps-and-resume-wrapper` flex container.

#### 11. Stopped Step Icon

Both `ChatView.vue` and `RunArtifacts.vue`:
- `IconPlayerStop` from `@tabler/icons-vue` for `step.status === "stopped"`.
- `.step-stopped-icon` CSS class (amber/warning color).
- `.step-row.stopped` CSS class (amber text).

### Data Flow

```
User clicks Stop
  → POST /runs/stop
  → ActiveRun.stop_run() sets flag + cancels graph task
  → CancelledError raised at next await (LLM HTTP call)
  → _run_graph() catches CancelledError
  → _handle_stop(): marks current step "stopped", persists step + run
  → Broadcasts run_stopped SSE event
  → Skips assistant message insertion (stopped runs have no report)
  → Broadcasts _STREAM_END, removes run
  → Frontend: step shows stop icon, loading=false, Stop→Send, Resume appears

User clicks Resume
  → POST /runs/resume
  → RunManager.resume_run() loads thread_id from DB
  → Creates new ActiveRun (_stop_requested = False)
  → Starts graph with Command(resume=True) from checkpoint
  → LangGraph re-executes interrupted node from scratch
  → _check_stop() sees flag=False, proceeds normally
  → SSE stream reconnects, events flow normally
  → Frontend: step transitions to running, loading=true, Send→Stop, Resume hides
```

### Files Modified

| File | Change |
|------|--------|
| `backend/moira/workflow/run_manager.py` | `stop_run()` (flag + cancel), `_handle_stop()` (shielded persist), `CancelledError` handler in `_run_graph()` / `_run_graph_resume()`, `resume_run()`, `start_graph_resume()` |
| `backend/moira/workflow/nodes/research_nodes.py` | `_check_stop()` helper with `interrupt()` at start of all 7 node functions |
| `backend/moira/api/streaming.py` | `POST .../stop` and `POST .../resume` endpoints |
| `frontend/src/api/client.ts` | `stopRun()`, `resumeRun()` API methods, `"stopped"` status type |
| `frontend/src/stores/chat.ts` | `stopRun()`, `resumeRun()`, `runStopped` ref, `run_stopped` event handler |
| `frontend/src/components/ChatView.vue` | Stop/Resume buttons, stopped step icon, wrapper layout |
| `frontend/src/components/RunArtifacts.vue` | Stopped step icon |
| `frontend/src/components/workflow-artifacts.css` | `.step-stopped-icon`, `.step-row.stopped` styles |

### Key Design Decisions

- **`asyncio.Task.cancel()` for immediate stop, `interrupt()` as safety net**: Task cancellation interrupts the currently-running node mid-execution. The cooperative flag + `interrupt()` catches the edge case where cancellation doesn't take effect and ensures the next node boundary also stops.
- **`asyncio.shield()` in `_handle_stop()`**: Persistence calls are shielded so they complete even inside a `CancelledError` handler where bare `await`s would be immediately re-cancelled.
- **No assistant message for stopped runs**: Stopped runs skip the "Unable to generate a research report" fallback. The user stopped intentionally — there should be no misleading report block.
- **Stopped step persisted to `workflow_steps`**: The step is written to the DB in `_handle_stop()` so it survives page reload and appears in the conversation history.
- **Discard partial work on resume**: The interrupted node restarts from scratch via LangGraph's `Command(resume=True)`. Any partial tool results are lost. Clean and predictable.
- **New `run_id` on resume, same `thread_id`**: Each resume creates a fresh `ActiveRun` with `_stop_requested = False`, ensuring the resumed node doesn't immediately re-stop.
- **No confirmation on Stop**: Single-click stops immediately. Fast and direct.
- **Resume button placement**: Right of steps-container, bottom-aligned. Proximity to the stopped step.
- **Global SSE compatibility**: Stop/resume broadcasts `run_status` events to global subscribers so the sidebar indicator updates correctly.
