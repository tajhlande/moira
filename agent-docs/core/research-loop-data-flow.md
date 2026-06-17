# Research Loop Data Flow

Field-by-field documentation of how data moves through the research loop,
where critical values (especially fact/conclusion statuses) are set and changed,
and where budget is consumed.

This document covers the **overhauled** research loop (decomposition →
tool_identification → planning → research → synthesis → research_review →
evaluation → report_generation). For the high-level architecture, see
[core/architecture.md](core/architecture.md). For the implementation plan,
see [research-loop-overhaul-plan.md](research-loop-overhaul-plan.md).

---

## State Model

The graph state is a single `ResearchState` TypedDict (`models/knowledge.py`)
with two sub-objects:

### `knowledge` — the research artifact

| Field | Type | Set by | Notes |
|-------|------|--------|-------|
| `question` | `str` | streaming.py (init) | Original user question |
| `user_goal` | `str` | decomposition | Inferred research goal |
| `topic` | `str` | decomposition | Topic classification |
| `entities` | `list[str]` | decomposition | Named entities |
| `concepts` | `list[str]` | decomposition | Key concepts |
| `facts` | `list[Fact]` | decomposition, research, research_review | **Replaced** by each node (local copy → mutate → return full list) |
| `conclusions` | `list[Conclusion]` | synthesis, evaluation | **Replaced** entirely on each synthesis run; mutated by evaluation |
| `citations` | `list[Citation]` | research | **Replaced** (local copy → append → return) |
| `review_history` | `list[ReviewOutcome]` | research_review | Appended each review pass |
| `evaluation_history` | `list[EvaluationOutcome]` | evaluation | Appended each evaluation pass |
| `report` | `ResearchReport` | report_generation | Final report dict |
| `generation_reason` | `str` | report_generation | `"verified"` / `"retries_exhausted"` / `"budget_exhausted"` / `"incomplete"` / `"error"` |

### `execution_state` — orchestration mechanics

| Field | Type | Set by | Notes |
|-------|------|--------|-------|
| `budget_remaining` | `float` | every node | Decremented by step costs and tool costs |
| `budget_limit` | `float` | streaming.py (init) | Never changes after init |
| `step_costs` | `dict[str, int]` | streaming.py (init) | Per-node cost weights |
| `retry_limits` | `dict[str, int]` | streaming.py (init) | `max_review` (default 3) and `max_evaluation` (default 2), from settings |
| `candidate_tools` | `list[ToolDefinition]` | tool_identification | Tools available for research |
| `tool_call_plan` | `list[ToolCallPlan]` | planning | The model's planned tool calls |
| `tool_costs` | `dict[str, float]` | tool_identification | Per-call invocation cost per tool |
| `tool_call_limits` | `dict[str, int]` | tool_identification | Max calls per tool per run |
| `tool_call_counts` | `dict[str, int]` | research | Running call counts |
| `total_tool_cost_consumed` | `float` | research | Cumulative tool spending |
| `review_count` | `int` | research_review | Incremented each review pass; reset to 0 by evaluation on retry |
| `research_retry_count` | `int` | streaming.py (init 0), incremented by planning node | Entry-count: 0 on init, +1 each planning entry |
| `evaluation_count` | `int` | evaluation | Incremented each evaluation pass |
| `error` | `str` | any node on failure | Error message for budget exhaustion |

### Reducer behavior

Both `knowledge` and `execution_state` use the `_shallow_merge` reducer:

```python
def _shallow_merge(old: dict, new: dict) -> dict:
    return {**old, **new}
```

- Keys **not returned** by a node **retain their previous value**.
- Keys **returned** by a node **fully replace** the old value (lists are replaced, not appended).
- Nodes spread `**es` into their `execution_state` return to preserve all keys.
- Nodes that update `knowledge` return only the keys they changed.

---

## Fact Status Lifecycle

```
                    decomposition
                         │
                    creates Fact(status="unknown")
                         │
                         ▼
            ┌─── research (plan-matching) ───┐
            │          status: unknown        │
            │               │                │
            │          status → "unverified" │  (matched to successful tool call)
            │          citation_ids appended │  (claim NOT set — see Design Note)
            └────────────────────────────────┘
                         │
            ┌─── research (_apply_discovered_facts) ───┐
            │          status: unknown OR unverified    │
            │               │                          │
            │          status → "unverified"           │  (model produces discovered_facts)
            │          claim, relation, value set       │
            └──────────────────────────────────────────┘
                         │
            ┌─── research_review (fact_results) ───┐
            │          status: unverified            │
            │               │                       │
            │     ┌─────────┼─────────┐             │
            │     ▼         ▼         ▼             │
            │ "verified" "contradicted" (stays)     │
            │ verification_note set                  │
            └───────────────────────────────────────┘
                         │
                    report_generation
                    (reads statuses, does not change)
```

**Status values:** `"unknown"` → `"unverified"` → `"verified"` | `"contradicted"`

---

## Conclusion Status Lifecycle

```
                    synthesis
                         │
                    creates Conclusion(status="unverified")
                         │
                         ▼
            ┌─── evaluation (conclusion_results) ───┐
            │          status: unverified            │
            │               │                       │
            │     ┌─────────┼─────────┐             │
            │     ▼         ▼         ▼             │
            │ "verified" "contradicted" (stays)     │
            │ verification_note set                  │
            └───────────────────────────────────────┘
                         │
                    report_generation
                    (reads statuses, does not change)
```

**Status values:** `"unverified"` → `"verified"` | `"contradicted"`

---

## Per-Node Data Flow

### 1. Decomposition

**Purpose:** Parse the user's question into a research plan.

| Direction | Fields |
|-----------|--------|
| **Reads** (`knowledge`) | `question` |
| **Reads** (`execution_state`) | `step_costs`, `budget_remaining` |
| **Writes** (`knowledge`) | `user_goal`, `topic`, `entities`, `concepts`, `facts` (fresh list, all `status="unknown"`) |
| **Writes** (`execution_state`) | `budget_remaining` (deducted) |

**Budget:** deducts `step_costs["decomposition"]` (default: 2).

**Detail keys:** `prompt`, `response`, `model`, `thinking?`, `structured_output?`

**Structured output:** `user_goal`, `topic`, `entities`, `concepts`, `unknown_facts` (raw model JSON).

---

### 2. Tool Identification

**Purpose:** Discover tools relevant to the unknown facts via LanceDB semantic search.

| Direction | Fields |
|-----------|--------|
| **Reads** (`knowledge`) | `facts` (filters for `status in ("unknown", "contradicted")`) |
| **Reads** (`execution_state`) | `step_costs`, `budget_remaining`, `tool_costs`, `tool_call_limits` |
| **Writes** (`knowledge`) | *(none)* |
| **Writes** (`execution_state`) | `candidate_tools`, `budget_remaining`, `tool_costs` (merged), `tool_call_limits` (merged) |

`tool_costs` and `tool_call_limits` are explicitly merged: `{**old, **new}` — catalog entries preserved, candidate tool entries added.

**Budget:** deducts `step_costs["tool_identification"]` (default: 1).

**Detail keys:** `queries`, `candidate_tools`, `default_tools_included`

**No structured output** (no model call).

---

### 3. Planning

**Purpose:** Produce a tool call plan targeting unknown facts.

| Direction | Fields |
|-----------|--------|
| **Reads** (`knowledge`) | `question`, `user_goal`, `topic`, `entities`, `concepts`, `facts`, `evaluation_history` (on retry) |
| **Reads** (`execution_state`) | `step_costs`, `budget_remaining`, `candidate_tools`, `tool_costs`, `tool_call_counts`, `tool_call_limits`, `research_retry_count` |
| **Writes** (`knowledge`) | *(none)* |
| **Writes** (`execution_state`) | `tool_call_plan`, `budget_remaining` (deducted) |

**Budget:** deducts `step_costs["planning"]` (default: 2). Also computes an advisory `available_for_tools` budget for the prompt (not deducted).

**Retry feedback:** When `research_retry_count > 0` (re-entered from evaluation
retry), the system prompt appends `planning.system_retry_evaluation` with the
evaluation's `goal_assessment` and failed conclusion results. This helps the
model replan with awareness of what went wrong.

**Detail keys:** `prompt`, `response`, `model`, `thinking?`, `structured_output?`

**Structured output:** `calls` (raw model JSON: list of `{tool, args, target_fact_ids, rationale}`).

---

### 4. Research

**Purpose:** Execute tool calls, discover facts, build citations.

| Direction | Fields |
|-----------|--------|
| **Reads** (`knowledge`) | `question`, `user_goal`, `facts`, `citations`, `review_history` (on review retry) |
| **Reads** (`execution_state`) | `step_costs`, `budget_remaining`, `candidate_tools`, `tool_call_plan`, `tool_call_counts`, `total_tool_cost_consumed`, `tool_costs`, `tool_call_limits` |
| **Writes** (`knowledge`) | `facts` (full list, mutated), `citations` (full list, appended) |
| **Writes** (`execution_state`) | `budget_remaining`, `tool_call_counts`, `total_tool_cost_consumed` |

**Budget:** deducts `step_costs["research"]` (default: 10) **plus** per-tool-call cost for each executed call.

#### Fact updates in research

Two mechanisms update facts during the tool loop:

1. **Plan-matching** (per tool call): When a tool call succeeds and matches a planned call's tool name, target facts are marked `"unverified"` and citation_ids are appended. **Does NOT set `claim`** — the raw tool output is a poor claim. The model's `discovered_facts` or post-loop extraction provides proper claims.

2. **`_apply_discovered_facts()`** (per model response round): Applies the model's `discovered_facts` to matching facts (`status in ("unknown", "unverified")`). Sets `claim`, `relation`, `value`, transitions to `"unverified"`.

If the model never produces `discovered_facts` but tool results exist, a post-loop extraction prompt asks the model to interpret the results. If the loop exhausts `DEFAULT_MAX_ROUNDS` (3), a final summary round asks the model to extract facts without requesting more tools.

#### Retry feedback from research_review

When re-entered from a research_review retry (`review_count > 0` and last
review route is `"retry"`), the system prompt appends
`research.system_retry_review` with the review's `coverage_assessment` and
`missing_areas`. This guides the model to focus additional queries on the
identified gaps.

**Detail keys:** `facts_resolved`, `facts_newly_unknown`, `tool_plan_size`, `executor_available`, `rounds`, `prompt`, `model`, `response?`, `thinking?`

**No `tool_results` in detail** — the run_manager accumulates these from `tool_result` events with full output (2000 chars). The research node's internal log truncates to 500 chars; including it in `node_end` would overwrite with truncated copies.

---

### 5. Synthesis

**Purpose:** Derive conclusions from verified/unverified facts with claims.

| Direction | Fields |
|-----------|--------|
| **Reads** (`knowledge`) | `question`, `user_goal`, `topic`, `entities`, `concepts`, `facts` (status in verified/unverified with claim), `evaluation_history` (on retry) |
| **Reads** (`execution_state`) | `step_costs`, `budget_remaining` |
| **Writes** (`knowledge`) | `conclusions` (full replacement — old conclusions discarded) |
| **Writes** (`execution_state`) | `budget_remaining` (deducted) |

**Budget:** deducts `step_costs["synthesis"]` (default: 5).

**Retry feedback:** On evaluation retry cycles (when
`evaluation_history[-1].route == "retry"`), the system prompt appends
`synthesis.system_retry` with the evaluation's `goal_assessment` and failed
conclusion results. This helps the model avoid repeating the same reasoning
errors.

**Detail keys:** `prompt`, `response`, `model`, `thinking?`, `structured_output`

**Structured output:** `conclusions` (always set: list of reconstructed Conclusion dicts).

---

### 6. Research Review

**Purpose:** Evaluate evidence coverage and fact quality. Marks facts as
verified/contradicted/unverified and identifies gaps. This node is **tool-free**
— it evaluates existing evidence only.

| Direction | Fields |
|-----------|--------|
| **Reads** (`knowledge`) | `question`, `user_goal`, `facts` (all with a claim), `conclusions` (for context), `review_history` |
| **Reads** (`execution_state`) | `step_costs`, `budget_remaining`, `review_count` |
| **Writes** (`knowledge`) | `facts` (mutated statuses), `review_history` (appended) |
| **Writes** (`execution_state`) | `budget_remaining` (deducted), `review_count` (incremented) |

**Budget:** deducts `step_costs["research_review"]` (default: 3).

#### Fact status transitions

```python
fact["status"] = fr.get("result")  # "verified" | "contradicted" | "unverified"
fact["verification_note"] = fr.get("evidence")
```

The model returns `fact_results` with per-fact verdicts. The node also accepts
`"status"` as a fallback key for `"result"` since models sometimes use the wrong
key — the verdict is critical for routing. Only `"evidence"` is used for the
note field (not model-invented keys like `"evaluation"`, which are assessments,
not evidence).

#### Routing decision

The model returns a `route` field: `"continue"` or `"retry"`.

- `"continue"` → evaluation
- `"retry"` → research (light retry: research → synthesis → research_review again),
  if `review_count < max_review` (default 3) and budget is sufficient
- `"retry"` declined (out of retries or budget) → evaluation (fall through)

#### Parse failure handling

If `_parse_json_object` returns an empty dict or the result is missing `route`,
the node emits a `run_error` event with the raw response and raises
`RuntimeError`. This makes model output problems (malformed JSON, missing
required fields) visible instead of silently proceeding with defaults.

**Detail keys:** `prompt`, `response`, `model`, `thinking?`, `structured_output`

**Structured output:** always `{fact_results, coverage_assessment, missing_areas, route}`.

---

### 7. Evaluation

**Purpose:** Evaluate conclusion logic and goal attainment. Examines conclusions
against fact statuses (set by research_review). This node is **tool-free** —
pure reasoning over already-verified facts.

| Direction | Fields |
|-----------|--------|
| **Reads** (`knowledge`) | `question`, `user_goal`, `facts` (reads statuses), `conclusions`, `evaluation_history` |
| **Reads** (`execution_state`) | `step_costs`, `budget_remaining`, `evaluation_count` |
| **Writes** (`knowledge`) | `conclusions` (mutated statuses), `evaluation_history` (appended) |
| **Writes** (`execution_state`) | `budget_remaining` (deducted), `evaluation_count` (incremented), `review_count` (reset to 0 on retry) |

**Budget:** deducts `step_costs["evaluation"]` (default: 5).

#### Conclusion status transitions

```python
conclusion["status"] = cr.get("result")  # "verified" | "contradicted" | "unverified"
conclusion["verification_note"] = cr.get("reason")
```

The model returns `conclusion_results` with per-conclusion verdicts. Same
`"status"` fallback and `"reason"`-only note policy as research_review.

#### Routing decision

The model returns a `route` field: `"accept"` or `"retry"`.

- `"accept"` → report_generation
- `"retry"` → tool_identification (heavy retry: full pipeline reset through
  evaluation), if `evaluation_count < max_evaluation` (default 2) and budget
  is sufficient
- `"retry"` declined (out of retries or budget) → report_generation
  (reason: `retries_exhausted` or `budget_exhausted`)

When evaluation routes to `"retry"`, `review_count` is reset to 0 so the new
evaluation cycle gets a fresh set of research retries.

#### Parse failure handling

Same as research_review: missing `route` raises `RuntimeError` with error event.

**Detail keys:** `prompt`, `response`, `model`, `thinking?`, `structured_output`

**Structured output:** always `{conclusion_results, goal_met, goal_assessment, route}`.

---

### 8. Report Generation

**Purpose:** Write the final research report from the knowledge model.

| Direction | Fields |
|-----------|--------|
| **Reads** (`knowledge`) | `question`, `user_goal`, `facts`, `conclusions`, `citations`, `evaluation_history` |
| **Reads** (`execution_state`) | `step_costs`, `budget_remaining`, `budget_limit`, `error`, `total_tool_cost_consumed`, `retry_limits`, `evaluation_count` |
| **Writes** (`knowledge`) | `report`, `generation_reason` |
| **Writes** (`execution_state`) | `budget_remaining` (deducted) |

**Budget:** deducts `step_costs["report_generation"]` (default: 3). This node is **budget-exempt** — always runs regardless of remaining budget (no `can_execute` gate).

**Generation reason logic:**
- `error` in state → `"error"`
- `evaluation_history[-1].goal_met` AND no contradicted conclusions → `"verified"`
- `evaluation_history[-1].route == "retry"` AND `evaluation_count >= max_evaluation` → `"retries_exhausted"`
- `evaluation_history[-1].route == "retry"` AND budget insufficient → `"budget_exhausted"`
- `evaluation_history[-1].route == "accept"` but goal not met or contradictions present → `"incomplete"`
- No evaluation history → `"budget_exhausted"`

**Events:** emits `run_complete` (with report + knowledge summary + generation reason), then `node_end`.

**Detail keys:** `generation_reason` only.

**Error handling:** If the model returns empty content or unparseable JSON,
the node emits a `run_error` event and raises `RuntimeError`. No silent
fallback report is generated.

---

## Graph Routing

```
START
  │
  ▼
decomposition
  │
  ▼
tool_identification ◄─────────────┐
  │                               │ evaluation retry
  ▼                               │ (max 2, full pipeline reset)
planning                           │
  │                               │
  ▼                               │
research ◄──────────┐             │
  │                 │ review retry│
  ▼                 │ (max 3,    │
synthesis ◄─────────┤ research   │
  │                 │ through    │
  ▼                 │ review)    │
research_review ────┘             │
  │                               │
  │ continue / retry declined     │
  ▼                               │
evaluation ───────────────────────┘
  │
  │ accept / retry declined
  ▼
report_generation
  │
  ▼
END
```

### Routing conditions (after research_review)

| `route` value | Condition | Target |
|---------------|-----------|--------|
| `"continue"` | — | `evaluation` |
| `"retry"` | `review_count < max_review` (default 3) AND budget ≥ review retry cost | `research` |
| `"retry"` | Out of retries or budget | `evaluation` (fall through) |
| (missing/unknown) | — | `evaluation` |

### Routing conditions (after evaluation)

| `route` value | Condition | Target |
|---------------|-----------|--------|
| `"accept"` | — | `report_generation` |
| `"retry"` | `evaluation_count < max_evaluation` (default 2) AND budget ≥ evaluation retry cost | `tool_identification` |
| `"retry"` | Out of retries or budget | `report_generation` (reason: `retries_exhausted` or `budget_exhausted`) |
| (missing/unknown) | — | `report_generation` |

**Retry costs (default weights):**
- Review retry (research → synthesis → research_review): 10 + 5 + 3 = **18**
- Evaluation retry (tool_identification → planning → research → synthesis → research_review → evaluation): 1 + 2 + 10 + 5 + 3 + 5 = **26**

---

## Budget Cost Summary

| Node | Step cost | Per-tool cost | Notes |
|------|-----------|---------------|-------|
| decomposition | 2 | — | |
| tool_identification | 1 | — | |
| planning | 2 | — | |
| research | 10 | Yes | One step cost + per-call costs for each executed tool |
| synthesis | 5 | — | |
| research_review | 3 | — | Tool-free |
| evaluation | 5 | — | Tool-free |
| report_generation | 3 | — | Budget-exempt (always runs) |
| **Full cycle** | **31** | | Without retries |

### Configurable retry limits

Retry limits are resolved at run start from system settings (`retry.max_review`,
`retry.max_evaluation`) and injected into `execution_state.retry_limits`. If
absent, the routers fall back to hardcoded defaults (3 and 2 respectively).

Per-request overrides can be sent from the conversation settings tray
(`max_review`, `max_evaluation` in the request body's `settings` dict). These
override the system defaults for that run only and are clamped to 1–10.

| Setting key | Default | Controls |
|-------------|---------|----------|
| `retry.max_review` | 3 | Max research_review → research retries per evaluation cycle |
| `retry.max_evaluation` | 2 | Max evaluation → tool_identification retries per run |

---

## Merged vs. Replaced Fields

### Within `knowledge`

| Field | Behavior | Pattern |
|-------|----------|---------|
| `facts` | **Replaced** | Node copies via `list(knowledge["facts"])`, mutates locally, returns full list |
| `citations` | **Replaced** | Same pattern |
| `conclusions` | **Replaced** | Synthesis creates fresh list; evaluation mutates local copy and returns |
| `review_history` | **Replaced** | Local copy + append pattern |
| `evaluation_history` | **Replaced** | Local copy + append pattern |
| Scalar fields (`user_goal`, etc.) | **Replaced** | Simple overwrite |

### Within `execution_state`

| Field | Behavior |
|-------|----------|
| `tool_costs` | Explicitly merged in tool_identification: `{**old, **new}` |
| `tool_call_limits` | Explicitly merged in tool_identification: `{**old, **new}` |
| Everything else | Replaced (but all nodes spread `**es` so full state is preserved) |

---

## Design Notes

### Why verification was split into research_review + evaluation

The original single `verification` node combined two logically distinct
responsibilities:

1. **Fact-level evidence review** — Are individual facts backed by citations
   and sufficient evidence?
2. **Conclusion-level goal evaluation** — Do the verified conclusions actually
   answer the user's question?

Combining these created problems:
- A single retry decision couldn't distinguish between "need more research"
  (light retry: just redo research) and "need a fundamentally different
  approach" (heavy retry: full pipeline reset).
- The model had to reason about both fact evidence and conclusion logic in one
  prompt, producing lower-quality verdicts on both.
- Retry budgets were all-or-nothing — one counter covered both retry types.

The split gives:
- **research_review** → light retry (research → synthesis → research_review),
  max 3 per evaluation cycle. Focuses on evidence coverage.
- **evaluation** → heavy retry (tool_identification → ... → evaluation), max 2
  per run. Focuses on goal attainment. Resets `review_count` on retry so each
  evaluation cycle gets a fresh set of research retries.

Both nodes are **tool-free** — they reason over evidence already gathered,
eliminating the complex fact-check tool-call loop of the old verification node.

### Why research doesn't set fact claims from raw tool output

Earlier versions set `fact["claim"] = result.output[:500]` during plan-matching.
This produced poor synthesis input — all facts matched to the same `web_search`
call would get the same truncated search snippet as their claim. The model's
`discovered_facts` (extracted claims) or the post-loop extraction prompt now
provide proper claims. Plan-matching only updates status and citation_ids.

`_apply_discovered_facts` applies to both `"unknown"` and `"unverified"` facts
so the model can set or improve claims in later rounds.

### Why `tool_results` are not in research's `node_end` detail

The run_manager accumulates `tool_results` from `tool_result` events (which
carry full 2000-char output). The research node's internal log truncates to
500 chars. Including the log in `node_end` would overwrite the run_manager's
accumulated entries with truncated copies.

### Why `structured_output` is always set in research_review and evaluation

Both nodes build `structured_output` from the parsed model response schema
(`fact_results`, `coverage_assessment`, etc. for review; `conclusion_results`,
`goal_met`, etc. for evaluation). This ensures the frontend renderer always
shows the right fields regardless of routing outcome.

---

## Resolved Issues

These were tracked gaps that have been fixed:

1. **`research_retry_count` was never initialized.** Now declared on
   `ExecutionState`, initialized to 0 in both `streaming.py` entry points,
   and incremented by the planning node on every entry. The router
   guard is `research_retry_count < 2` (allow initial run + 1 retry). Planning
   shows retry prompts when the count read at entry is `> 0`.

2. **LanceDB-discovered tools had empty `argument_schema`.** LanceDB
   stores only `{name, vector, description, enabled}` — not parameter
   schemas. The `tool_identification` node now re-hydrates each
   candidate tool from the in-memory `ToolCatalog` (loaded from SQLite)
   after vector search, replacing the truncated definition with the full
   one (`argument_schema`, `invocation_cost`, `call_limit_per_run`).
   Built-in tools were unaffected because they come from the catalog
   directly. No LanceDB rebuild or tool reingestion required — the fix
   is purely a runtime catalog lookup.

3. **JSON parse failures were silently masked.** When
   `_parse_json_object` returned `{}` (e.g., model omitted commas
   between array items or emitted literal control characters inside JSON
   string values), every field defaulted — `route` became `"accept"`,
   `goal_met` became `False`, `fact_results` became empty. Both
   research_review and evaluation now check for missing `route` after
   parsing and raise a `RuntimeError` with the raw response in the error
   detail, making model output problems visible instead of silently
   proceeding with defaults. The `_parse_json_object` helper now applies
   `_fix_json_control_chars` as preprocessing to escape literal control
   characters (common with quantized models) before attempting to parse.

4. **Empty report generation silently produced fallback.** When the
   model returned empty content or unparseable JSON, the old report
   generation node caught the exception and produced a generic fallback
   report. This masked model failures. The node now emits `run_error`
   and raises `RuntimeError` — no silent fallback.

5. **Accurate generation reasons.** The report generation node now
   distinguishes *why* the research loop ended using a `generation_reason`
   field (renamed from the misleading `generation_path`). When evaluation
   requests retry but the router can't honor it, the node checks
   `evaluation_count` vs `retry_limits.max_evaluation` to determine whether
   the reason is `"retries_exhausted"` or `"budget_exhausted"` — rather than
   lumping both into a single `"retry_overruled"` value. An `"incomplete"`
   reason covers the case where evaluation accepted but the goal wasn't
   fully met. Each reason has a dedicated prompt
   (`report_generation.reason_*`) with appropriate instructions for the
   model.

6. **Configurable retry limits.** Retry limits (`max_review`,
   `max_evaluation`) are now configurable via system settings
   (`retry.max_review`, `retry.max_evaluation`) and config file
   (`budget.retry_limits`), following the same resolution pattern as
   `cost_weights`. Settings take precedence over file config; routers
   fall back to hardcoded defaults (3/2) if absent from state.
