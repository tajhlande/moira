# Run Persistence and Stream Reconnection

Status: **Planned**

## Overview

When a workflow run is in progress and the browser reloads, the SSE connection
drops and the run is lost — the graph is cancelled, no WorkflowRun is persisted,
and the user sees nothing. This plan makes runs survive disconnection by:

1. Running the graph in a background task decoupled from the HTTP request
2. Persisting WorkflowRun state incrementally (after each node, not just at the end)
3. Providing a single stream endpoint for both live streaming and reconnection

## Architecture Decisions

### Option B: Decouple start from stream

Two endpoints with distinct responsibilities:

- **`POST /conversations/{id}/messages`** — starts a run, returns `{run_id: "..."}` as
  plain JSON. No SSE. This is a pure mutation.
- **`GET /conversations/{id}/stream`** — subscribes to the active run and returns SSE.
  Used for both initial streaming (right after POST) and reconnection (after page reload).

Frontend flow for **new message**:
1. POST to start the run -> get `run_id`
2. Immediately GET the stream -> receive SSE events
3. Process events through existing `handleEvent()` / `finalizeRun()`
Frontend flow for **reconnection**:
1. Page loads, `selectConversation()` fetches conversation detail
2. See a `status: "running"` run in the response -> populate partial state
3. GET the stream -> receive SSE events from current point forward
4. Same `handleEvent()` / `finalizeRun()` pipeline

### Event delivery: current-point only

No replay. The frontend loads partial state from the persisted WorkflowRun
(via the conversation detail API), then subscribes to the stream for future
events only.

### Broadcast: per-subscriber asyncio.Queue

Each SSE connection gets its own `asyncio.Queue`. The graph task puts events
into all subscriber queues. Clean, no event loss, natural backpressure.

### Run expiration: immediate on completion

When the graph task finishes (complete or error), the ActiveRun is removed from
RunManager immediately. All subscriber queues receive a final event then are
closed. No TTL, no waiting for subscribers to disconnect.

## Components

### 1. RunManager (`backend/moira/workflow/run_manager.py`)

New file. Process-level singleton registered in `service_setup.py`.

```python
class ActiveRun:
    run_id: str
    conversation_id: str
    user_message_id: int
    thread_id: str
    started_at: str
    # Accumulated state (same fields as WorkflowRun)
    execution_steps: list[dict]
    tool_executions: list[dict]
    verification_attempts: list[dict]
    thinking_traces: dict[str, str]
    report: dict | None
    error: str
    status: str  # "running" | "completed" | "error"
    budget_remaining: float
    budget_consumed: float
    budget_limit: float
    # Infrastructure
    _subscribers: list[asyncio.Queue]
    _graph_task: asyncio.Task
    _conversation_repo: ConversationRepository
    async def subscribe() -> AsyncIterator[dict]:
        """Yield SSE events from current point until run completes."""
    def _broadcast(event: dict) -> None:
        """Put event into all subscriber queues."""
    async def _persist() -> None:
        """Upsert the current accumulated state as a WorkflowRun."""
    async def _run_graph(graph, initial_state, graph_config) -> None:
        """Iterate graph.astream(), accumulate state, persist, broadcast."""
        # On completion: final persist, save assistant_report message, remove from RunManager.
class RunManager:
    _active_runs: dict[str, ActiveRun]           # keyed by run_id
    _conversation_runs: dict[str, str]            # conversation_id -> run_id
    async def start_run(conversation_id, user_msg, ...) -> str:
        """Create ActiveRun, persist initial WorkflowRun(status="running"),
        launch graph as asyncio.Task. Returns run_id."""
    def get_active_run(conversation_id) -> ActiveRun | None:
        """Look up by conversation_id. Returns None if no run is in-flight."""
    def remove_run(run_id) -> None:
        """Called by ActiveRun when graph completes. Cleans up both dicts."""
```

**Persistence granularity** -- `_persist()` is called after:
- `node_end` -- updates execution_steps, budget_consumed, budget_remaining
- `tool_result` -- updates tool_executions
- `verification_report` -- updates verification_attempts
- `run_complete` -- updates report, status="completed", completed_at, total_elapsed_ms
- `run_error` -- updates error, status="error"

The initial `WorkflowRun(status="running")` insert happens in `start_run()`.

**Subscriber lifecycle:**
1. `subscribe()` creates a per-subscriber `asyncio.Queue`
2. The graph task puts each event into all subscriber queues via `_broadcast()`
3. When the graph completes, `_broadcast()` sends the final event, then puts
   a sentinel (`None`) into each queue
4. `subscribe()` sees `None` and stops iterating
5. The queue is removed from the subscribers list
6. `remove_run()` cleans up the RunManager dicts

### 2. Backend API Changes

#### `POST /conversations/{conversation_id}/messages` (modified)

Currently: starts run AND streams SSE.
New behavior: starts run via RunManager, returns JSON `{run_id, user_message_id}`.

```python
@streaming_router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id, body):
    # Validate conversation exists, content is present
    # Insert user message
    # Check for prior report
    # Call run_manager.start_run(...)
    # Return {"run_id": run_id, "user_message_id": user_msg.id}
```

This becomes a fast synchronous-ish endpoint -- it just kicks off the background
task and returns immediately.

#### `GET /conversations/{conversation_id}/stream` (new)

```python
@streaming_router.get("/conversations/{conversation_id}/stream")
async def stream_events(conversation_id):
    active_run = run_manager.get_active_run(conversation_id)
    if active_run is None:
        raise HTTPException(404, "No active run for this conversation")
    async def event_source():
        async for event in active_run.subscribe():
            yield event
    return EventSourceResponse(event_source())
```

If the run is already complete by the time GET arrives (race condition), the
subscriber immediately gets the final event(s) and the stream closes. The
ActiveRun stays in memory until all subscribers drain their queues -- which
happens almost instantly since the final event is already queued.

### 3. Frontend Changes

#### `frontend/src/api/client.ts`

Add:

```typescript
startRun: (conversationId: string, content: string) =>
  request<{ run_id: string; user_message_id: number }>(
    `/conversations/${conversationId}/messages`,
    { method: "POST", body: JSON.stringify({ content }) },
  ),
```

Note: this replaces the existing SSE-based `POST .../messages` call. The
response is now JSON, not SSE.

#### `frontend/src/stores/chat.ts`

**`sendMessage()` refactored:**
1. Create conversation if needed (same as now)
2. POST to start the run -> `get {run_id, user_message_id}`
3. Push user message into messages array (using `user_message_id` from response)
4. Call `connectStream(conversationId)` to subscribe to SSE

**New `connectStream(conversationId)` function:**
1. `GET /conversations/{id}/stream`
2. Parse SSE events (same parser as current streamMessage)
3. Feed events through existing `handleEvent()`
4. On run_complete/run_error: `finalizeRun()`, `loading = false`

This replaces `streamMessage()` which currently does POST+SSE in one call.
The SSE parsing logic moves into `connectStream()`. The POST becomes a simple
JSON request.

**`selectConversation()` enhanced:**
1. Fetch conversation detail (same as now)
2. Populate messages and runs from response
3. Check runs for `status === "running"`
4. If found:
   a. Load partial state into live refs (execution_steps, tool_executions, etc.)
   b. Set `loading = true`
   c. Call `connectStream(conversationId)`
5. If no running run: display as normal (completed/errored run in runs map)

The partial state load (step 4a) sets up the live refs so the UI shows
completed steps immediately. New events from the stream append to these refs,
which is exactly what `handleEvent()` already does.

### 4. Service Setup

`service_setup.py` changes:
- Import and instantiate `RunManager`
- Register as `"run_manager"` service
- Pass `ConversationRepository` reference to RunManager (for incremental persists)

### 5. Existing streaming.py logic moves

The `event_generator()` function's core logic (graph iteration, state
accumulation, event type handling) moves into `ActiveRun._run_graph()`.
The `streaming_response()` persistence logic (saving assistant_report message,
saving final WorkflowRun) moves into the end of `_run_graph()`.
`streaming.py` becomes thin -- just the two HTTP handler functions that
delegate to RunManager.

## Files Changed

| File                                    | Change                                                                      |
|-----------------------------------------|-----------------------------------------------------------------------------|
| `backend/moira/workflow/run_manager.py` | **New** -- RunManager, ActiveRun                                            |
| `backend/moira/api/streaming.py`        | Refactor: POST returns JSON, new GET stream endpoint                        |
| `backend/moira/service_setup.py`        | Register RunManager singleton                                               |
| `frontend/src/api/client.ts`            | Add `startRun`, remove SSE from POST                                        |
| `frontend/src/stores/chat.ts`           | Refactor sendMessage, add connectStream, reconnection in selectConversation |

## Implementation Order

1. **`run_manager.py`** -- ActiveRun + RunManager core, graph execution, incremental persist
2. **`streaming.py`** -- Refactor POST to use RunManager, add GET stream endpoint
3. **`service_setup.py`** -- Wire RunManager into DI container
4. **Backend tests** -- Unit tests for RunManager, integration test for disconnect/reconnect
5. **`client.ts`** -- Update API client for new endpoints
6. **`chat.ts`** -- Refactor sendMessage, add connectStream, add reconnection logic
7. **Frontend tests** -- Update existing tests, add reconnection test

## Edge Cases

**Race: run completes between conversation fetch and stream connect**
- GET /stream returns 404 (no active run)
- Frontend catches the 404, re-fetches conversation detail
- The completed run is now in the response with status="completed"
- Display as normal

**Race: two messages sent to same conversation simultaneously**
- RunManager tracks one active run per conversation
- POST to start a second run while one is active returns 409 Conflict
- Frontend should prevent this (disable send while loading), backend guards too

**Server restart during run**
- Graph task is in-memory, lost on restart
- Persisted WorkflowRun has status="running" but will never complete
- Frontend shows it as "in progress" indefinitely
- Mitigation: on startup, scan for stale status="running" runs and mark them
  status="error" with a note about server restart. Follow-up task, not in scope.

**Subscriber queue grows unbounded**
- Each queue holds events until consumed. If a subscriber is slow, memory grows.
- Mitigation: set a max queue size (e.g. 100). If full, drop the oldest event.
  Since events are also persisted, nothing is truly lost -- the subscriber can
  re-fetch the full run state if needed.
  