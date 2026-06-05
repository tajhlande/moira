# Tool Call Metrics — Implementation Plan

## Summary

Add a metrics recording layer to `ToolExecutor` that captures per-tool call counts, latency, and call types in hourly buckets. This provides cross-conversation visibility into tool usage patterns and enables monitoring of cost-limited APIs. Cost estimation is deferred to individual tool implementations.

## Design Decisions

1. **Rolling counters with hourly buckets** — one row per `(tool_name, call_type, period_hour)`. Compact, efficiently aggregable, granular enough for rate-limit debugging.
2. **`call_type` is opaque to the metrics layer** — each tool defines its own call type taxonomy via `report_call_type()`. Tools that don't need to differentiate call types can rely on `"default"` as the default value. The metrics table records the string for bucketing.
3. **Cost estimation is deferred to tools** — the metrics framework does not handle cost estimation. Individual tool implementations may estimate costs using call counts and their own pricing metadata. The metrics table stores only call counts and latency.
4. **Capture in `ToolExecutor.execute()`** — already wraps every tool call, has `ToolResult` with `duration_ms` and `success`.
5. **Same SQLite database** — follows existing per-call connection pattern.
6. **Latency tracking via min/max/sum** — `low_duration_ms`, `high_duration_ms`, `aggregate_duration_ms` per bucket give outlier visibility without per-call rows.

## Database — Migration 008

```sql
CREATE TABLE tool_metrics (
    tool_name TEXT NOT NULL,
    call_type TEXT NOT NULL DEFAULT 'default',
    period_hour TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    aggregate_duration_ms INTEGER NOT NULL DEFAULT 0,
    low_duration_ms INTEGER NOT NULL DEFAULT 0,
    high_duration_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tool_name, call_type, period_hour)
);
```

`period_hour` is ISO 8601 hour string, e.g., `"2026-06-02T14:00"`. PK is `(tool_name, call_type, period_hour)`.

### Upsert logic

```sql
INSERT INTO tool_metrics (tool_name, call_type, period_hour, call_count, success_count, error_count,
                          aggregate_duration_ms, low_duration_ms, high_duration_ms)
VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
ON CONFLICT(tool_name, call_type, period_hour) DO UPDATE SET
    call_count = call_count + 1,
    success_count = success_count + ?,
    error_count = error_count + ?,
    aggregate_duration_ms = aggregate_duration_ms + ?,
    low_duration_ms = MIN(low_duration_ms, ?),
    high_duration_ms = MAX(high_duration_ms, ?);
```

Atomic under SQLite's serialized write model.

## Tool-Level Interface — `BaseTool.report_call_type()`

```python
class BaseTool(ABC):
    def report_call_type(self, args: dict, result: ToolResult) -> str | None:
        """Return a call_type string for metrics bucketing, or None to use
        the default. Subclasses override this to classify calls by pricing
        tier or usage category (e.g., 'search', 'summarize', 'search+extract').
        Cost estimation is the tool's responsibility, not the metrics layer's."""
        return None
```

Tools get this default implementation for free. Tools that need to differentiate call types should override, e.g.:

```python
class KagiWebSearchTool(BaseTool):
    def report_call_type(self, args: dict, result: ToolResult) -> str | None:
        if not result.success:
            return None
        if args.get("workflow") == "summarize":
            return "summarize"
        extract_count = args.get("extract_count", 0) or 0
        if extract_count > 0:
            return "search+extract"
        return "search"
```

The executor uses the returned string as `call_type`, or `"default"` when `None`. Cost estimation is deferred to individual tools — they can use their own pricing metadata and the metrics data to compute costs as needed.

## Repository

### `ToolMetricsRow` (dataclass in `tools/metrics.py`)

```python
@dataclass
class ToolMetricsRow:
    tool_name: str
    call_type: str
    period_hour: str
    call_count: int
    success_count: int
    error_count: int
    aggregate_duration_ms: int
    low_duration_ms: int
    high_duration_ms: int
```

### `ToolMetricsRepository` (ABC in `tools/metrics.py`)

```python
class ToolMetricsRepository(ABC):
    @abstractmethod
    async def record_call(self, tool_name: str, call_type: str, period_hour: str,
                          success: bool, duration_ms: int) -> None: ...

    @abstractmethod
    async def get_metrics(self, tool_name: str | None = None,
                          period_start: str | None = None,
                          period_end: str | None = None) -> list[ToolMetricsRow]: ...
```

### `SqliteToolMetricsRepository` (in `repos.py`)

Uses the upsert SQL above. `period_hour` is `datetime.utcnow().strftime("%Y-%m-%dT%H:00")` at call time.

## Executor Changes

### `ToolExecutor.__init__()` — add optional `metrics_repo`

```python
def __init__(self, timeout=DEFAULT_TIMEOUT, max_retries=DEFAULT_MAX_RETRIES,
             metrics_repo: ToolMetricsRepository | None = None):
    self._metrics_repo = metrics_repo
    ...
```

Optional so existing tests and construction sites don't break. When `None`, metrics are silently skipped.

### `_record_metrics()` helper

```python
async def _record_metrics(self, tool_name: str, args: dict, result: ToolResult) -> None:
    if self._metrics_repo is None:
        return
    report = None
    tool = self._tool_instances.get(tool_name)
    if tool is not None:
        try:
            report = tool.report_call_type(args, result)
        except Exception:
            pass  # never let metrics recording break execution
    call_type = report[0] if report else "default"
    period_hour = datetime.utcnow().strftime("%Y-%m-%dT%H:00")
    await self._metrics_repo.record_call(
        tool_name=tool_name, call_type=call_type, period_hour=period_hour,
        success=result.success, duration_ms=result.duration_ms,
    )
```

Called after every execution path (success, failure, timeout, exception).

## service_setup.py Changes

```python
metrics_repo = SqliteToolMetricsRepository(db_path)
executor = ToolExecutor(metrics_repo=metrics_repo)
```

## Files to Create

| File                                                       | Description                             |
|------------------------------------------------------------|-----------------------------------------|
| `moira/persistence/sqlite/migrations/008_tool_metrics.sql` | Migration creating `tool_metrics` table |
| `moira/tools/metrics.py`                                   | Tools metrics model and class           |

## Files to Modify

| File                                 | Change                                                         |
|--------------------------------------|----------------------------------------------------------------|
| `moira/persistence/sqlite/schema.py` | Version bump to 8                                              |
| `moira/persistence/interfaces.py`    | Add `ToolMetricsRepository` ABC and `ToolMetricsRow` dataclass |
| `moira/persistence/sqlite/repos.py`  | Add `SqliteToolMetricsRepository`                              |
| `moira/tools/base.py`                | Add `report_call_type()` method to `BaseTool`                  |
| `moira/tools/executor.py`            | Add `metrics_repo` param, `_record_metrics()` helper           |
| `moira/service_setup.py`             | Create metrics repo, pass to executor                          |

## Test Plan

1. **Metrics row created on successful call** — verify row with correct counts
2. **Row updated on repeated calls** — two calls in same hour, verify `call_count=2` and aggregated duration
3. **Error counted** — tool returns `success=False`, verify `error_count=1`
4. **Timeout counted** — tool times out, verify metrics recorded
5. **Free tool uses "default" call_type** — execute calculator, verify `call_type="default"`
6. **Paid tool uses custom call_type** — Kagi search, verify `call_type="search"`
7. **report_call_type exception swallowed** — verify execution still succeeds with "default" type
8. **Metrics repo is None** — verify no errors, silent skip
9. **Min/max duration** — two calls with different durations, verify `low_duration_ms` and `high_duration_ms`
10. **Hourly bucket isolation** — calls in two hours produce two rows

## Implementation Order

1. Migration 008
2. `ToolMetricsRow` + `ToolMetricsRepository` in `metrics.py`
3. `SqliteToolMetricsRepository` in `repos.py`
4. `report_call_type()` on `BaseTool`
5. `ToolExecutor` changes
6. `service_setup.py` wiring
7. Schema version bump
8. Tests

## Deferred

- API surface for querying metrics
- Cost estimation tooling — deferred to individual tool implementations
- Data retention / rollup strategy
- Decile/percentile latency tracking
- Per-conversation metrics breakdown
- Dashboard UI
