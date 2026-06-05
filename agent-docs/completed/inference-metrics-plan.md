# Workflow Steps Normalization + Inference Token Tracking — Implementation Plan

## Summary

Normalize the `execution_steps` JSON blob on `workflow_runs` into a dedicated `workflow_steps` table, with one row per graph node invocation. 
Add inference token columns (`input_tokens`, `thinking_tokens`, `output_tokens`) directly to the step rows, eliminating the need for a separate `inference_usage` table. 
Add metrics capture code to call sites where we are invoking inference services.
This phase is capture-and-store only — API exposure and analytics/chart integration is deferred.

## Motivation

Two problems:

1. **Token usage is invisible.** The system discards the `usage` field from every OpenAI-compatible API response. There is no way to determine how many tokens a research run consumed, which steps are most expensive, or how thinking vs. output tokens compare across models.

2. **JSON blobs are no longer the right storage for steps.** The `execution_steps` JSON blob on `workflow_runs` is rewritten in full on every step completion, growing larger each time. The write amplification requires an `asyncio.Lock` to avoid collisions. The data is opaque to SQL — no indexing, no aggregation across runs. As we add token metrics, embedding them in unqueryable JSON would perpetuate the problem.

Normalizing steps into a table solves both: per-step writes eliminate read-modify-write amplification, and real columns enable SQL aggregation. Token columns go directly on the step row — the step IS the per-call row.

## Design Decisions

1. **One row per graph node invocation** — the table cardinality matches the LangGraph node execution structure. A full cycle produces 7 rows (planning, tool_discovery, tool_selection, research_execution, draft_synthesis, verification, report_generation). Retry cycles add rows for the revisited nodes. The frontend and SSE event model already treat each node invocation as a discrete step.

2. **Token counts aggregated across tool loop rounds** — nodes that run tool loops (`research_execution`, `verification`) make multiple `chat_completion()` calls within a single node invocation. These are aggregated into the step row (sum of input tokens, sum of output tokens, sum of thinking tokens). Per-round granularity is lost, but the table stays aligned with the graph navigation structure. We will also capture the call count for these nodes as well.

3. **No separate `inference_usage` table** — the `workflow_steps` table carries both execution metadata (node, status, budget, timing) and inference metadata (model, purpose, tokens). One table, one row per node invocation.

4. **`detail` JSON column for variable content** — prompts, model responses, thinking text, tool results, structured output, and verification reports are stored as a JSON object in a `detail TEXT` column. These are display-only, not queryable. Structured fields that need SQL access are real columns.

5. **`verification_attempts` JSON blob relocated into step detail** — verification reports live in the verification step's `detail.structured_output`. The `verification_attempts` TEXT column on `workflow_runs` is dropped during migration. To reconstruct "all verification attempts for this run," query `workflow_steps WHERE workflow_run_id = ? AND node_name = 'verification' ORDER BY id`. The API response continues to include a `verification_attempts` field (backward compat) assembled from step detail.

6. **`tool_executions` JSON blob retained on `workflow_runs` for now** — tool results are also available in step `detail`, but the flat list on the run is useful for quick "all tools used" queries and doesn't interfere with the metrics work. Normalize later.

7. **Old columns dropped during migration** — `execution_steps`, `verification_attempts`, and the unused `thinking_traces` columns are dropped from `workflow_runs` after their data is migrated into `workflow_steps` rows. The migration is a Python function (`_apply_migration_009` in `schema.py`) that reads existing JSON blobs, inserts step rows, then drops the columns. This preserves all existing data and keeps the schema clean.

8. **API response shape unchanged** — the backend assembles `execution_steps` and `verification_attempts` from the `workflow_steps` table when serving `GET /conversations/{id}`. Frontend types and rendering logic don't change.

9. **`run_id` passed through `graph_config`** — nodes currently receive `thread_id` and `moira_config` via `config["configurable"]`. `RunManager.start_run()` injects `run_id` into the config so nodes know which run they're in.

10. **`purpose` and `model` nullable** — `tool_discovery` makes no inference call. Its step row has `NULL` for `purpose`, `model`, and all token columns. This distinguishes "no inference" from "inference made but provider reported no tokens."

11. **Silent failure for token recording** — if the provider doesn't report usage data, token columns are `NULL`. If recording fails for any reason, the error is logged but never breaks graph execution.

12. **Capture only, this phase** — no new API endpoints or UI changes. Storage and recording only.

## Database — Migration 009

```sql
-- workflow_steps: one row per graph node invocation
CREATE TABLE workflow_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id),
    node_name TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    cost REAL NOT NULL DEFAULT 0,
    budget_remaining REAL NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    purpose TEXT,
    model TEXT,
    call_count INTEGER NOT NULL DEFAULT 1,
    input_tokens INTEGER,
    thinking_tokens INTEGER,
    output_tokens INTEGER,
    prompt_time_ms REAL,
    gen_time_ms REAL,
    error TEXT NOT NULL DEFAULT '',
    detail TEXT
);

CREATE INDEX idx_workflow_steps_run ON workflow_steps(workflow_run_id);
CREATE INDEX idx_workflow_steps_node ON workflow_steps(node_name);
CREATE INDEX idx_workflow_steps_purpose ON workflow_steps(purpose);
CREATE INDEX idx_workflow_steps_started ON workflow_steps(started_at);
```

### Column notes

| Column             | Type       | Notes                                                                                                                                                                                                |
|--------------------|------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `id`               | INTEGER PK | Autoincrement. Order reflects execution sequence.                                                                                                                                                    |
| `workflow_run_id`  | TEXT FK    | References `workflow_runs(id)`. All steps for a run are fetched via this index.                                                                                                                      |
| `node_name`        | TEXT       | Machine identifier: `planning`, `tool_discovery`, `tool_selection`, etc.                                                                                                                             |
| `label`            | TEXT       | Human-readable label from `STAGE_LABELS` map (e.g., "Planning", "Researching").                                                                                                                      |
| `status`           | TEXT       | `"running"`, `"completed"`, or `"error"`.                                                                                                                                                            |
| `cost`             | REAL       | Budget cost weight consumed by this step (from the abstract cost weight system).                                                                                                                     |
| `budget_remaining` | REAL       | Budget remaining after this step executed.                                                                                                                                                           |
| `started_at`       | TEXT       | ISO 8601 timestamp when the node started.                                                                                                                                                            |
| `elapsed_ms`       | INTEGER    | Wall-clock milliseconds for the node execution.                                                                                                                                                      |
| `purpose`          | TEXT       | `"intelligence"` or `"task"`, matching the model key used. `NULL` for `tool_discovery` or steps that don't call inference models.                                                                                                              |
| `model`            | TEXT       | Actual model ID from `ChatResponse.model` (e.g., `"deepseek-r1:14b"`). `NULL` for `tool_discovery`.                                                                                                  |
| `input_tokens`     | INTEGER    | Prompt tokens from `usage.prompt_tokens`. `NULL` when no inference or provider doesn't report.                                                                                                       |
| `thinking_tokens`  | INTEGER    | Reasoning tokens from `usage.completion_tokens_details.reasoning_tokens`. `NULL` when unavailable.                                                                                                   |
| `output_tokens`    | INTEGER    | Completion tokens from `usage.completion_tokens`. `NULL` when no inference or provider doesn't report.                                                                                               |
| `call_count`       | INTEGER    | Number of `chat_completion()` calls made within this step. `1` for single-call nodes. `NULL` for `tool_discovery` (no inference). Tool loop nodes aggregate: rounds + parse retries + verdict calls. |
| `prompt_time_ms`   | REAL       | Time the provider spent processing prompt tokens (e.g., `timings.prompt_ms` from llama.cpp server). Aggregated (summed) across tool loop rounds. `NULL` when provider doesn't report timing.       |
| `gen_time_ms`      | REAL       | Time the provider spent generating completion tokens (e.g., `timings.predicted_ms` from llama.cpp server). Aggregated (summed) across tool loop rounds. `NULL` when provider doesn't report timing. |
| `error`            | TEXT       | Error message if the step failed. Empty string otherwise.                                                                                                                                            |
| `detail`           | TEXT       | JSON object with variable content (see below).                                                                                                                                                       |

### `detail` JSON structure

The `detail` column holds a JSON object whose keys vary by node type:

| Key                 | Present in                                   | Content                                                                            |
|---------------------|----------------------------------------------|------------------------------------------------------------------------------------|
| `response`          | All inference nodes                          | Model's text response                                                              |
| `thinking`          | Nodes where model produced reasoning         | Reasoning content text                                                             |
| `prompt`            | All inference nodes                          | `{"messages": [...]}` — the prompt messages array                                  |
| `structured_output` | tool_selection, verification, tool_discovery | Parsed JSON from the model (selected tools, verification report, discovered tools) |
| `tool_results`      | research_execution, verification             | Array of `{tool, args, output, success, duration_ms}`                              |
| `fact_check_prompt` | verification (when fact-checking ran)        | `{"messages": [...]}` — the fact-check prompt                                      |
| `provider_timings`  | Any node where provider reported timing      | Full provider-specific timing object (e.g., llama.cpp `timings` with cache, per-token rates) |

This is the same data that currently lives in the `execution_steps` JSON blob's `detail` sub-object. The shape doesn't change — it just moves to its own column.

## ChatResponse Changes

Extend the existing `ChatResponse` dataclass in `moira/inference/client.py`:

```python
@dataclass
class ChatResponse:
    content: str
    thinking: str = ""
    model: str = ""
    finish_reason: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    prompt_time_ms: float | None = None
    gen_time_ms: float | None = None
```

All token fields are `None` by default — backward compatible.

## InferenceClient Changes

In `moira/inference/client.py`, parse the `usage` field from the API response:

```python
data = resp.json()
choice = data["choices"][0]
message = choice["message"]
usage = data.get("usage") or {}

thinking = message.get("reasoning_content", "")
if thinking:
    logger.debug("Model thinking (reasoning_content): %s", thinking[:2000])

completion_details = usage.get("completion_tokens_details") or {}
thinking_tokens = completion_details.get("reasoning_tokens")

# Provider-specific timing. llama.cpp server reports a timings object
# with prompt_ms and predicted_ms. Most providers don't report timing.
timings = data.get("timings") or {}

return ChatResponse(
    content=message.get("content", ""),
    thinking=thinking,
    model=data.get("model", model),
    finish_reason=choice.get("finish_reason", ""),
    input_tokens=usage.get("prompt_tokens"),
    output_tokens=usage.get("completion_tokens"),
    thinking_tokens=thinking_tokens,
    prompt_time_ms=timings.get("prompt_ms"),
    gen_time_ms=timings.get("predicted_ms"),
)
```

### Provider compatibility notes

| Provider               | `prompt_tokens` | `completion_tokens` | `reasoning_tokens`    | Timing                |
|------------------------|-----------------|---------------------|-----------------------|-----------------------|
| Ollama (OpenAI compat) | Yes             | Yes                 | No (as of 0.6.x)      | No                    |
| LM Studio              | Yes             | Yes                 | No                    | No                    |
| vLLM                   | Yes             | Yes                 | No                    | No                    |
| llama.cpp (server)     | Yes             | Yes                 | No                    | Yes (`timings` object)|
| OpenAI                 | Yes             | Yes                 | Yes (o-series models) | No                    |
| DeepSeek (API)         | Yes             | Yes                 | Yes                   | No                    |
| OpenRouter             | Yes             | Yes                 | Yes                   | No                    |

When a provider omits `usage` or `timings` entirely, all token and timing fields remain `None`. Step rows are still written — the columns are just `NULL`.

## Data Model

### `WorkflowStep` (dataclass in `moira/persistence/interfaces.py`)

```python
@dataclass
class WorkflowStep:
    id: int | None
    workflow_run_id: str
    node_name: str
    label: str
    status: str
    cost: float
    budget_remaining: float
    started_at: str
    elapsed_ms: int
    purpose: str | None
    model: str | None
    input_tokens: int | None
    thinking_tokens: int | None
    output_tokens: int | None
    call_count: int | None
    prompt_time_ms: float | None
    gen_time_ms: float | None
    error: str
    detail: dict | None
```

### `WorkflowRun` (modified in `moira/persistence/interfaces.py`)

```python
@dataclass
class WorkflowRun:
    id: str
    conversation_id: str
    user_message_id: int
    thread_id: str
    tool_executions: list = field(default_factory=list)
    report: dict | None = None
    budget_limit: float = 0.0
    budget_consumed: float = 0.0
    error: str = ""
    status: str = "running"
    started_at: str = ""
    completed_at: str = ""
    total_elapsed_ms: int = 0
```

`execution_steps` and `verification_attempts` fields removed. Steps are loaded from the `workflow_steps` table. The API response assembly reconstructs these fields for backward compatibility.

### `WorkflowStepRepository` (ABC in `moira/persistence/interfaces.py`)

```python
class WorkflowStepRepository(ABC):
    @abstractmethod
    async def save_step(self, step: WorkflowStep) -> int: ...

    @abstractmethod
    async def get_steps_for_run(self, workflow_run_id: str) -> list[WorkflowStep]: ...
```

`save_step` returns the auto-generated `id`.

### `SqliteWorkflowStepRepository` (in `repos.py`)

Simple INSERT for new steps, SELECT for reads. The `detail` dict is serialized as JSON on write and deserialized on read.

## Token Accumulation Helper

A helper dataclass in `moira/inference/metrics.py` (new module) that accumulates token counts from `ChatResponse` objects. Used by `_run_tool_loop` to aggregate across rounds:

```python
@dataclass
class TokenCounts:
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    prompt_time_ms: float = 0.0
    gen_time_ms: float = 0.0

    def accumulate(self, response: ChatResponse) -> None:
        if response.input_tokens is not None:
            self.input_tokens += response.input_tokens
        if response.output_tokens is not None:
            self.output_tokens += response.output_tokens
        if response.thinking_tokens is not None:
            self.thinking_tokens += response.thinking_tokens
        if response.prompt_time_ms is not None:
            self.prompt_time_ms += response.prompt_time_ms
        if response.gen_time_ms is not None:
            self.gen_time_ms += response.gen_time_ms
```

Single-call nodes create a `TokenCounts` from one response. Tool loop nodes accumulate across rounds.

## ActiveRun Changes

### Step persistence moves from blob serialization to table INSERT

Currently, `ActiveRun` accumulates steps in `self.execution_steps: list[dict]` and serializes the entire list as JSON in `_persist()`. After this change:

1. `self.execution_steps` remains as an in-memory list for SSE replay (no change to `_replay_events()`).
2. On `node_end`, the step is also INSERTed into the `workflow_steps` table via `WorkflowStepRepository`.
3. `_persist()` writes only run-level metadata to `workflow_runs` — no step blob.

### New `_persist_step()` method

```python
async def _persist_step(self, step: WorkflowStep) -> int:
    """Write a completed step to the workflow_steps table."""
    step_repo = cast(
        WorkflowStepRepository,
        self._run_manager._get_service("workflow_step_repository"),
    )
    return await step_repo.save_step(step)
```

### Modified `_handle_event()`

In the `node_end` handler, after finalizing the step dict:

```python
wf_step = WorkflowStep(
    id=None,
    workflow_run_id=self.run_id,
    node_name=self._current_step["node"],
    label=self._current_step.get("label", ""),
    status="completed",
    cost=self._current_step.get("cost", 0),
    budget_remaining=self.budget_remaining,
    started_at=self._current_step.get("started_at", ""),
    elapsed_ms=self._current_step.get("elapsed_ms", 0),
    purpose=self._current_step.get("purpose"),
    model=self._current_step.get("model"),
    input_tokens=self._current_step.get("input_tokens"),
    thinking_tokens=self._current_step.get("thinking_tokens"),
    output_tokens=self._current_step.get("output_tokens"),
    call_count=self._current_step.get("call_count"),
    prompt_time_ms=self._current_step.get("prompt_time_ms"),
    gen_time_ms=self._current_step.get("gen_time_ms"),
    error=self._current_step.get("error", ""),
    detail=self._current_step.get("detail"),
)
await self._persist_step(wf_step)
```

### How step metadata gets into `_current_step`

Nodes write their inference metadata into the `_current_step` dict via the `node_end` event payload. The `_emit_node_end()` helper already accepts a `detail` dict. We extend this: nodes also pass `purpose`, `model`, and token counts through the `node_end` event payload.

The `_emit_node_end()` helper gains optional parameters:

```python
def _emit_node_end(
    writer,
    node: str,
    budget_remaining: float,
    detail: dict | None = None,
    purpose: str | None = None,
    model: str | None = None,
    call_count: int | None = None,
    input_tokens: int | None = None,
    thinking_tokens: int | None = None,
    output_tokens: int | None = None,
    prompt_time_ms: float | None = None,
    gen_time_ms: float | None = None,
) -> None:
    payload: dict = {"node": node, "timestamp": _now(), "budget_remaining": budget_remaining}
    if detail:
        payload["detail"] = detail
    if purpose is not None:
        payload["purpose"] = purpose
    if model is not None:
        payload["model"] = model
    if call_count is not None:
        payload["call_count"] = call_count
    if input_tokens is not None:
        payload["input_tokens"] = input_tokens
    if thinking_tokens is not None:
        payload["thinking_tokens"] = thinking_tokens
    if output_tokens is not None:
        payload["output_tokens"] = output_tokens
    if prompt_time_ms is not None:
        payload["prompt_time_ms"] = prompt_time_ms
    if gen_time_ms is not None:
        payload["gen_time_ms"] = gen_time_ms
    writer({"event": "node_end", "payload": payload})
```

`ActiveRun._handle_event()` already merges `payload` fields into `_current_step` on `node_end` — the new fields flow through the same path.

## RunManager Changes

### `start_run()` injects `run_id` into graph config

```python
async def start_run(self, ...):
    run_id = str(uuid.uuid4())
    ...

    graph_config = dict(graph_config)
    configurable = dict(graph_config.get("configurable", {}))
    configurable["run_id"] = run_id
    graph_config["configurable"] = configurable

    active_run.start_graph(graph, initial_state, graph_config)
    ...
```

Shallow-copy mutation — the original config in `streaming.py` is not modified.

### `_get_service()` helper

`ActiveRun` needs access to `service_provider()` for step persistence. Add a method on `RunManager`:

```python
class RunManager:
    def _get_service(self, name: str) -> object:
        from moira.service_setup import service_provider
        return service_provider(name)
```

### `remove_run()` step cleanup

When a run completes, steps are already persisted. No additional cleanup needed.

## Node Changes

### Single-call nodes

Each node calls `_emit_node_end()` with the additional token parameters. The change is mechanical — extract data from the `ChatResponse` and pass it through.

Example for `planning`:

```python
raw = await resolved.client.chat_completion(
    model=resolved.model_id, messages=messages, temperature=0.7
)
plan, thinking = _extract(node, raw)

_emit_node_end(
    writer, node, new_budget,
    detail=_build_detail(plan, thinking, messages=messages),
    purpose="intelligence",
    model=raw.model or resolved.model_id,
    call_count=1,
    input_tokens=raw.input_tokens,
    thinking_tokens=raw.thinking_tokens,
    output_tokens=raw.output_tokens,
    prompt_time_ms=raw.prompt_time_ms,
    gen_time_ms=raw.gen_time_ms,
)
```

### Tool loop nodes

`_run_tool_loop()` returns token counts as part of its result. The signature changes to:

```python
async def _run_tool_loop(
    node: str,
    messages: list[dict[str, str]],
    active_tools: list,
    executor,
    registry,
    model_key: str,
    writer,
    max_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    temperature: float = 0.5,
    max_parse_retries: int = DEFAULT_MAX_PARSE_RETRIES,
) -> tuple[str, str | None, list[dict], TokenCounts, int]:
```

Inside the loop, after each `chat_completion()`:

```python
raw = await resolved.client.chat_completion(...)
response, thinking = _extract(node, raw)
token_counts.accumulate(raw)
call_count += 1
```

The calling node passes the accumulated counts to `_emit_node_end()`:

```python
# In research_execution:
response, thinking, all_tool_results, tokens, call_count = await _run_tool_loop(...)

_emit_node_end(
    writer, node, new_budget,
    detail=detail,
    purpose="intelligence",
    model=resolved_model_id,
    call_count=call_count,
    input_tokens=tokens.input_tokens or None,
    thinking_tokens=tokens.thinking_tokens or None,
    output_tokens=tokens.output_tokens or None,
    prompt_time_ms=tokens.prompt_time_ms or None,
    gen_time_ms=tokens.gen_time_ms or None,
)
```

### `verification` node

The verification node has two phases: fact-check tool loop + verdict call. Both use the intelligence model. Token counts from both phases are aggregated:

```python
# Phase 1: fact-check tool loop
fc_response, fc_thinking, all_tool_results, fc_tokens, fc_call_count = await _run_tool_loop(...)

# Phase 2: verdict
raw = await resolved.client.chat_completion(...)
final_response, final_thinking = _extract(node, raw)

# Aggregate tokens from both phases
total_tokens = TokenCounts()
total_tokens.input_tokens = fc_tokens.input_tokens
total_tokens.output_tokens = fc_tokens.output_tokens
total_tokens.thinking_tokens = fc_tokens.thinking_tokens
total_tokens.prompt_time_ms = fc_tokens.prompt_time_ms
total_tokens.gen_time_ms = fc_tokens.gen_time_ms
if raw.input_tokens:
    total_tokens.input_tokens += raw.input_tokens
if raw.output_tokens:
    total_tokens.output_tokens += raw.output_tokens
if raw.thinking_tokens:
    total_tokens.thinking_tokens += raw.thinking_tokens
if raw.prompt_time_ms:
    total_tokens.prompt_time_ms += raw.prompt_time_ms
if raw.gen_time_ms:
    total_tokens.gen_time_ms += raw.gen_time_ms

total_call_count = fc_call_count + 1  # tool loop calls + verdict call

_emit_node_end(
    writer, node, new_budget,
    detail=detail,
    purpose="intelligence",
    model=raw.model or resolved.model_id,
    call_count=total_call_count,
    input_tokens=total_tokens.input_tokens or None,
    thinking_tokens=total_tokens.thinking_tokens or None,
    output_tokens=total_tokens.output_tokens or None,
    prompt_time_ms=total_tokens.prompt_time_ms or None,
    gen_time_ms=total_tokens.gen_time_ms or None,
)
```

### `tool_discovery` — no token data

This node makes no inference call. Its `_emit_node_end()` call passes no token, call_count, or timing parameters. The step row will have `NULL` for `purpose`, `model`, `call_count`, all token columns, and all timing columns.

### `report_generation` — wrapped in try/except

This node's inference call is already in a try/except. Token extraction follows the same pattern — if the call fails, no token data is passed.

## Repository Changes

### `save_workflow_run()` — no longer serializes steps

The `execution_steps` and `verification_attempts` columns are dropped from the INSERT/UPSERT. The `tool_executions` JSON blob is retained:

```python
async def save_workflow_run(self, run: WorkflowRun) -> None:
    conn = self._connect()
    try:
        conn.execute(
            "INSERT INTO workflow_runs "
            "(id, conversation_id, user_message_id, thread_id, "
            "tool_executions, report, budget_limit, budget_consumed, "
            "error, status, started_at, completed_at, total_elapsed_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "tool_executions = excluded.tool_executions, "
            "report = excluded.report, "
            "budget_consumed = excluded.budget_consumed, "
            "error = excluded.error, "
            "status = excluded.status, "
            "completed_at = excluded.completed_at, "
            "total_elapsed_ms = excluded.total_elapsed_ms",
            (
                run.id, run.conversation_id, run.user_message_id, run.thread_id,
                json.dumps(run.tool_executions),
                json.dumps(run.report) if run.report else None,
                run.budget_limit, run.budget_consumed,
                run.error, run.status, run.started_at,
                run.completed_at or None, run.total_elapsed_ms or None,
            ),
        )
        conn.commit()
    finally:
        conn.close()
```

### `get_workflow_runs()` — no longer deserializes steps

The `WorkflowRun` dataclass no longer has `execution_steps` or `verification_attempts` fields. The query drops those columns:

```python
async def get_workflow_runs(self, conversation_id: str) -> list[WorkflowRun]:
    conn = self._connect()
    try:
        rows = conn.execute(
            "SELECT id, conversation_id, user_message_id, thread_id, "
            "tool_executions, report, budget_limit, budget_consumed, "
            "error, status, started_at, completed_at, total_elapsed_ms "
            "FROM workflow_runs WHERE conversation_id = ? ORDER BY started_at ASC",
            (conversation_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["tool_executions"] = json.loads(d["tool_executions"])
            d["report"] = json.loads(d["report"]) if d["report"] else None
            d["total_elapsed_ms"] = d["total_elapsed_ms"] or 0
            result.append(WorkflowRun(**d))
        return result
    finally:
        conn.close()
```

## Router Changes

### `GET /conversations/{id}` — assembles steps from table

```python
runs = await conversations.get_workflow_runs(conversation_id)
step_repo = cast(WorkflowStepRepository, service_provider("workflow_step_repository"))

"runs": [
    {
        "id": r.id,
        "user_message_id": r.user_message_id,
        "execution_steps": [
            {
                "node": s.node_name,
                "label": s.label,
                "status": s.status,
                "cost": s.cost,
                "budget_remaining": s.budget_remaining,
                "elapsed_ms": s.elapsed_ms,
                "started_at": s.started_at,
                "error": s.error if s.error else None,
                "detail": s.detail,
            }
            for s in await step_repo.get_steps_for_run(r.id)
        ],
        "tool_executions": r.tool_executions,
        "verification_attempts": [
            s.detail.get("structured_output", {})
            for s in await step_repo.get_steps_for_run(r.id)
            if s.node_name == "verification" and s.detail and s.detail.get("structured_output")
        ],
        "report": r.report,
        "budget_limit": r.budget_limit,
        "budget_consumed": r.budget_consumed,
        "error": r.error,
        "status": r.status,
        "started_at": r.started_at,
        "completed_at": r.completed_at,
        "total_elapsed_ms": r.total_elapsed_ms,
    }
    for r in runs
],
```

The API response shape is identical to the current shape. `execution_steps` is assembled from the `workflow_steps` table. `verification_attempts` is reconstructed from verification step details. The frontend doesn't change.

**Performance note:** this issues one `get_steps_for_run()` call per run to build `execution_steps`, and a second to build `verification_attempts`. These should be consolidated into a single query, or the verification attempts should be filtered from the already-loaded steps list in memory. The implementation should use a single query and do both derivations in Python.

## SSE Replay Compatibility

`ActiveRun._replay_events()` continues to iterate `self.execution_steps` (in-memory list of dicts). The SSE events emitted during streaming are unchanged — `node_start`, `node_end`, `tool_result`, `verification_report`, etc. The step data is now dual-written: in memory for SSE replay, and to the `workflow_steps` table for persistence.

The in-memory step dict format doesn't change. The table writes are additive — the in-memory structure continues to serve its role during the run's lifetime, and the table serves the reload path after the run completes.

## Migration Strategy

Migration 009 is a Python function (`_apply_migration_009` in `schema.py`), not a pure SQL migration, because it needs to parse JSON blobs and insert rows conditionally. The SQL file (`009_workflow_steps.sql`) creates the table and indexes; the Python function runs after and handles data migration.

Steps:

1. **Create table** — `009_workflow_steps.sql` creates `workflow_steps` with all columns and indexes.
2. **Migrate `execution_steps`** — for each `workflow_runs` row, parse the `execution_steps` JSON array. Each element becomes a `workflow_steps` row with `purpose`, `model`, `call_count`, and token columns set to `NULL` (this data was not captured previously). Verification steps are already in `execution_steps` with their reports in `detail.structured_output`, so no separate migration of `verification_attempts` is needed — the data is already there.
3. **Drop old columns** — `execution_steps`, `verification_attempts`, and the unused `thinking_traces` columns are dropped from `workflow_runs` after migration.

| File                                                         | Description                                         |
|--------------------------------------------------------------|-----------------------------------------------------|
| `moira/persistence/sqlite/migrations/009_workflow_steps.sql` | Migration creating `workflow_steps` table + indexes |
| `moira/inference/metrics.py`                                 | `TokenCounts` accumulator dataclass                 |

## Files to Modify

| File                                     | Change                                                                                                                                     |
|------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| `moira/inference/client.py`              | Add token fields to `ChatResponse`; parse `usage` from API response                                                                        |
| `moira/persistence/interfaces.py`        | Add `WorkflowStep` dataclass, `WorkflowStepRepository` ABC; remove `execution_steps` and `verification_attempts` from `WorkflowRun`        |
| `moira/persistence/sqlite/repos.py`      | Add `SqliteWorkflowStepRepository`; modify `save_workflow_run` and `get_workflow_runs` to exclude step blob                                |
| `moira/persistence/sqlite/schema.py`     | Version bump to 9                                                                                                                          |
| `moira/workflow/run_manager.py`          | Inject `run_id` into config; add `_persist_step()`; modify `_handle_event()` to persist steps to table; remove step blob from `_persist()` |
| `moira/workflow/nodes/research_nodes.py` | Extend `_emit_node_end()` with token params; extend `_run_tool_loop()` to return `TokenCounts`; add token extraction in all nodes          |
| `moira/api/router.py`                    | Assemble `execution_steps` and `verification_attempts` from `workflow_steps` table in conversation detail endpoint                         |
| `moira/service_setup.py`                 | Register `workflow_step_repository`                                                                                                        |

## Test Plan

### ChatResponse and client tests

1. **Token fields parsed from response** — mock API response with full `usage` and `timings` objects; verify all `ChatResponse` fields populated including `prompt_time_ms` and `gen_time_ms`
2. **Missing usage handled** — API response with no `usage` or `timings` keys; verify all token and timing fields are `None`
3. **Missing completion_tokens_details** — response with `usage` but no `completion_tokens_details`; verify `thinking_tokens` is `None`, others populated
4. **Timing only from llama.cpp-style providers** — response with `timings.prompt_ms` and `timings.predicted_ms`; verify `prompt_time_ms` and `gen_time_ms` populated

### WorkflowStep repository tests

4. **Insert a single step** — verify row written with correct fields, auto-generated ID returned
5. **Retrieve steps for a run** — insert 3 steps for one run, 2 for another; verify `get_steps_for_run()` returns only the right 3, ordered by ID
6. **NULL token columns** — insert step with `purpose=None`, `model=None`, `input_tokens=None`; verify stored and retrieved correctly
7. **Detail JSON roundtrip** — insert step with complex `detail` dict; verify JSON roundtrip preserves structure

### TokenCounts accumulator tests

8. **Single response** — accumulate a response with all token and timing fields; verify correct totals
9. **Multiple responses** — accumulate 3 responses; verify sums for tokens and timing
10. **None fields skipped** — accumulate response with `input_tokens=None` and `prompt_time_ms=None`; verify they're treated as 0

### ActiveRun integration tests

11. **Step written to table on node_end** — simulate a `node_end` event; verify `workflow_steps` row created
12. **Run metadata persisted without step blob** — verify `workflow_runs` row has no `execution_steps` content (old column is ignored)
13. **Verification report in step detail** — simulate verification `node_end` with structured_output; verify reconstructable from step table

### Node-level tests

14. **Single-call node passes tokens** — mock `chat_completion` to return tokens; verify `_emit_node_end` called with token params
15. **Tool loop aggregates tokens** — mock 2-round tool loop; verify returned `TokenCounts` sums both rounds
16. **Tool discovery has no tokens** — verify `tool_discovery` emits node_end without token params

### API response assembly tests

17. **Steps assembled from table** — create run with steps in DB; call `GET /conversations/{id}`; verify `execution_steps` array matches table data
18. **Verification attempts reconstructed** — create verification steps with reports; verify `verification_attempts` array reconstructed from step detail
19. **Backward-compatible response shape** — verify all expected fields present in API response

## Implementation Order

1. **Migration 009** — `workflow_steps` table + indexes
2. **`moira/inference/client.py`** — extend `ChatResponse`, parse `usage`
3. **`moira/inference/metrics.py`** — `TokenCounts` accumulator
4. **`moira/persistence/interfaces.py`** — `WorkflowStep` dataclass, `WorkflowStepRepository` ABC, modify `WorkflowRun`
5. **`moira/persistence/sqlite/repos.py`** — `SqliteWorkflowStepRepository`, modify run persistence
6. **`moira/service_setup.py`** — register `workflow_step_repository`
7. **`moira/workflow/run_manager.py`** — inject `run_id`, add `_persist_step()`, modify `_handle_event()`, simplify `_persist()`
8. **`moira/workflow/nodes/research_nodes.py`** — extend `_emit_node_end()`, extend `_run_tool_loop()`, add token extraction in all nodes
9. **`moira/api/router.py`** — assemble steps from table in conversation detail endpoint
10. **Schema version bump to 9**
11. **Tests** — repository, accumulator, ActiveRun, nodes, API response

## Step-to-Node Map

| Node                 | Inference? | Model key      | Token/timing handling                                       | call_count       |
|----------------------|------------|----------------|-------------------------------------------------------------|------------------|
| `planning`           | Yes        | `intelligence` | Single call → direct from `ChatResponse`                    | 1                |
| `tool_discovery`     | No         | —              | All NULL                                                    | NULL             |
| `tool_selection`     | Yes        | `intelligence` | Single call → direct from `ChatResponse`                    | 1                |
| `research_execution` | Yes        | `intelligence` | Tool loop → `TokenCounts` aggregate                         | rounds + retries |
| `compression`        | Yes        | `task`         | Single call → direct from `ChatResponse`                    | 1                |
| `draft_synthesis`    | Yes        | `intelligence` | Single call → direct from `ChatResponse`                    | 1                |
| `verification`       | Yes        | `intelligence` | Tool loop + verdict → `TokenCounts` aggregate + single call | loop + 1         |
| `report_generation`  | Yes        | `intelligence` | Single call (in try/except) → direct from `ChatResponse`    | 1                |

## Deferred

- API endpoint for querying step/token data across runs
- Analytics charts in the Settings view (token usage over time, per-node breakdown)
- Token-based cost estimation (per-model pricing configuration)
- Normalize `tool_executions` into a `workflow_step_tools` child table
- Data retention / archival strategy for step data
- Integration with the abstract budget system (token-based budget limits)
- Frontend rendering of token counts in step detail
