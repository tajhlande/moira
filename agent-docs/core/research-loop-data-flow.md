# Research Loop Data Flow

Field-by-field documentation of how data moves through the research loop,
where critical values (especially fact/conclusion statuses) are set and changed,
and where budget is consumed.

This document covers the **overhauled** research loop (decomposition →
tool_identification → planning → research → synthesis → verification →
report_generation). For the high-level architecture, see
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
| `facts` | `list[Fact]` | decomposition, research, verification | **Replaced** by each node (local copy → mutate → return full list) |
| `conclusions` | `list[Conclusion]` | synthesis, verification | **Replaced** entirely on each synthesis run |
| `citations` | `list[Citation]` | research | **Replaced** (local copy → append → return) |
| `verification_history` | `list[VerificationOutcome]`` | verification | Appended each verification pass |
| `report` | `ResearchReport` | report_generation | Final report dict |
| `generation_path` | `str` | report_generation | `"verified"` / `"budget_exhausted"` / `"error"` |

### `execution_state` — orchestration mechanics

| Field | Type | Set by | Notes |
|-------|------|--------|-------|
| `budget_remaining` | `float` | every node | Decremented by step costs and tool costs |
| `budget_limit` | `float` | streaming.py (init) | Never changes after init |
| `step_costs` | `dict[str, int]` | streaming.py (init) | Per-node cost weights |
| `candidate_tools` | `list[ToolDefinition]` | tool_identification | Tools available for research/verification |
| `tool_call_plan` | `list[ToolCallPlan]` | planning | The model's planned tool calls |
| `tool_costs` | `dict[str, float]` | tool_identification | Per-call invocation cost per tool |
| `tool_call_limits` | `dict[str, int]` | tool_identification | Max calls per tool per run |
| `tool_call_counts` | `dict[str, int]` | research, verification | Running call counts |
| `total_tool_cost_consumed` | `float` | research, verification | Cumulative tool spending |
| `verification_attempts` | `int` | verification | Incremented each verification pass |
| `synthesis_retry_count` | `int` | streaming.py (init 0), incremented by synthesis node | Entry-count: 0 on init, +1 each synthesis entry |
| `research_retry_count` | `int` | streaming.py (init 0), incremented by planning node | Entry-count: 0 on init, +1 each planning entry |
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
            ┌─── verification (fact_results) ───┐
            │          status: unverified        │
            │               │                   │
            │     ┌─────────┼─────────┐         │
            │     ▼         ▼         ▼         │
            │ "verified" "contradicted" (stays) │
            │ verification_note set              │
            └───────────────────────────────────┘
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
            ┌─── verification (conclusion_results) ───┐
            │          status: unverified              │
            │               │                         │
            │     ┌─────────┼─────────┐               │
            │     ▼         ▼         ▼               │
            │ "verified" "contradicted" (stays)       │
            │ verification_note set                    │
            └─────────────────────────────────────────┘
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
| **Reads** (`knowledge`) | `question`, `user_goal`, `topic`, `entities`, `concepts`, `facts`, `verification_history` (on retry) |
| **Reads** (`execution_state`) | `step_costs`, `budget_remaining`, `candidate_tools`, `tool_costs`, `tool_call_counts`, `tool_call_limits`, `research_retry_count` |
| **Writes** (`knowledge`) | *(none)* |
| **Writes** (`execution_state`) | `tool_call_plan`, `budget_remaining` (deducted) |

**Budget:** deducts `step_costs["planning"]` (default: 2). Also computes an advisory `available_for_tools` budget for the prompt (not deducted).

**Detail keys:** `prompt`, `response`, `model`, `thinking?`, `structured_output?`

**Structured output:** `calls` (raw model JSON: list of `{tool, args, target_fact_ids, rationale}`).

---

### 4. Research

**Purpose:** Execute tool calls, discover facts, build citations.

| Direction | Fields |
|-----------|--------|
| **Reads** (`knowledge`) | `question`, `user_goal`, `facts`, `citations` |
| **Reads** (`execution_state`) | `step_costs`, `budget_remaining`, `candidate_tools`, `tool_call_plan`, `tool_call_counts`, `total_tool_cost_consumed`, `tool_costs`, `tool_call_limits` |
| **Writes** (`knowledge`) | `facts` (full list, mutated), `citations` (full list, appended) |
| **Writes** (`execution_state`) | `budget_remaining`, `tool_call_counts`, `total_tool_cost_consumed` |

**Budget:** deducts `step_costs["research"]` (default: 10) **plus** per-tool-call cost for each executed call.

#### Fact updates in research

Two mechanisms update facts during the tool loop:

1. **Plan-matching** (per tool call): When a tool call succeeds and matches a planned call's tool name, target facts are marked `"unverified"` and citation_ids are appended. **Does NOT set `claim`** — the raw tool output is a poor claim. The model's `discovered_facts` or post-loop extraction provides proper claims.

2. **`_apply_discovered_facts()`** (per model response round): Applies the model's `discovered_facts` to matching facts (`status in ("unknown", "unverified")`). Sets `claim`, `relation`, `value`, transitions to `"unverified"`.

If the model never produces `discovered_facts` but tool results exist, a post-loop extraction prompt asks the model to interpret the results. If the loop exhausts `DEFAULT_MAX_ROUNDS` (3), a final summary round asks the model to extract facts without requesting more tools.

**Detail keys:** `facts_resolved`, `facts_newly_unknown`, `tool_plan_size`, `executor_available`, `rounds`, `prompt`, `model`, `response?`, `thinking?`

**No `tool_results` in detail** — the run_manager accumulates these from `tool_result` events with full output (2000 chars). The research node's internal log truncates to 500 chars; including it in `node_end` would overwrite with truncated copies.

---

### 5. Synthesis

**Purpose:** Derive conclusions from verified/unverified facts with claims.

| Direction | Fields |
|-----------|--------|
| **Reads** (`knowledge`) | `question`, `user_goal`, `topic`, `entities`, `concepts`, `facts` (status in verified/unverified with claim), `verification_history` (on retry) |
| **Reads** (`execution_state`) | `step_costs`, `budget_remaining`, `synthesis_retry_count` |
| **Writes** (`knowledge`) | `conclusions` (full replacement — old conclusions discarded) |
| **Writes** (`execution_state`) | `budget_remaining` (deducted) |

**Budget:** deducts `step_costs["synthesis"]` (default: 5).

**Detail keys:** `prompt`, `response`, `model`, `thinking?`, `structured_output`

**Structured output:** `conclusions` (always set: list of reconstructed Conclusion dicts).

---

### 6. Verification

**Purpose:** Evaluate facts, conclusions, and goal attainment. May call tools for independent fact-checking.

| Direction | Fields |
|-----------|--------|
| **Reads** (`knowledge`) | `question`, `user_goal`, `facts` (all with a claim), `conclusions`, `verification_history` |
| **Reads** (`execution_state`) | `step_costs`, `budget_remaining`, `candidate_tools`, `tool_call_counts`, `tool_costs`, `tool_call_limits`, `total_tool_cost_consumed`, `verification_attempts` |
| **Writes** (`knowledge`) | `facts` (mutated statuses + new unknown facts appended), `conclusions` (mutated statuses), `verification_history` (appended) |
| **Writes** (`execution_state`) | `budget_remaining`, `verification_attempts` (incremented), `tool_call_counts`, `total_tool_cost_consumed` |

**Budget:** deducts `step_costs["verification"]` (default: 8) per model call (initial + fact-check re-prompts). Plus per-tool-call cost for any fact-checking tool calls.

#### Fact-checking loop

When the model returns `tool_calls` instead of a verification result (requesting independent evidence), the node:

1. Extracts tool calls from the parsed response
2. Executes them (with call-limit enforcement)
3. Feeds results back via the `verification.evidence` prompt
4. Makes a second model call to get the actual verification result
5. Repeats up to `MAX_FACT_CHECK_ROUNDS = 2` times

#### Fact status transitions

```python
fact["status"] = fr.get("result")  # "verified" | "contradicted" | "unverified"
fact["verification_note"] = fr.get("evidence")
```

#### Conclusion status transitions

```python
conclusion["status"] = cr.get("result")  # "verified" | "contradicted" | "unverified"
conclusion["verification_note"] = cr.get("reason")
```

#### Routing decision

The model returns a `route` field: `"accept"`, `"retry_research"`, or `"retry_synthesis"`. This drives the graph's conditional edge.

**Detail keys:** `prompt`, `response`, `model`, `tool_results`, `thinking?`, `structured_output`

**Structured output:** always `{fact_results, conclusion_results, new_unknown_facts, goal_met, goal_assessment, route}` — never raw `tool_calls`.

---

### 7. Report Generation

**Purpose:** Write the final research report from the knowledge model.

| Direction | Fields |
|-----------|--------|
| **Reads** (`knowledge`) | `question`, `user_goal`, `facts`, `conclusions`, `citations`, `verification_history` |
| **Reads** (`execution_state`) | `step_costs`, `budget_remaining`, `budget_limit`, `error`, `total_tool_cost_consumed` |
| **Writes** (`knowledge`) | `report`, `generation_path` |
| **Writes** (`execution_state`) | `budget_remaining` (deducted) |

**Budget:** deducts `step_costs["report_generation"]` (default: 3). This node is **budget-exempt** — always runs regardless of remaining budget (no `can_execute` gate).

**Generation path logic:**
- `error` in state → `"error"`
- `verification_history[-1].goal_met` AND no contradicted conclusions → `"verified"`
- Otherwise → `"budget_exhausted"`

**Events:** emits `run_complete` (with report + knowledge summary + generation path), then `node_end`.

**Detail keys:** `generation_path` only.

---

## Graph Routing

```
START
  │
  ▼
decomposition
  │
  ▼
tool_identification
  │
  ▼
planning
  │
  ▼
research
  │
  ▼
synthesis ◄───────────┐
  │                   │ retry_synthesis (max 1 retry)
  ▼                   │
verification ─────────┤
  │                   │
  │ retry_research    │
  ▼                   │ (max 1 retry)
tool_identification ──┘
  │
  │ accept / declined retry / out of budget
  ▼
report_generation
  │
  ▼
END
```

### Routing conditions (after verification)

| `route` value | Condition | Target |
|---------------|-----------|--------|
| `"accept"` | — | `report_generation` |
| `"retry_synthesis"` | `synthesis_retry_count < 2` AND budget ≥ synthesis+verification cost | `synthesis` |
| `"retry_synthesis"` | Out of retries or budget | `report_generation` |
| `"retry_research"` | `research_retry_count < 2` AND budget ≥ full research cycle cost | `tool_identification` |
| `"retry_research"` | Out of retries or budget | `report_generation` |
| (missing/unknown) | — | `report_generation` |

**Retry costs:**
- Synthesis retry: synthesis(5) + verification(8) = **13**
- Research retry: tool_identification(1) + planning(2) + research(10) + synthesis(5) + verification(8) = **26**

---

## Budget Cost Summary

| Node | Step cost | Per-tool cost | Notes |
|------|-----------|---------------|-------|
| decomposition | 2 | — | |
| tool_identification | 1 | — | |
| planning | 2 | — | |
| research | 10 | Yes | One step cost + per-call costs for each executed tool |
| synthesis | 5 | — | |
| verification | 8 | Yes | Step cost per model call (initial + fact-check rounds) + per-call tool costs |
| report_generation | 3 | — | Budget-exempt (always runs) |
| **Full cycle** | **31** | | Without retries |

---

## Merged vs. Replaced Fields

### Within `knowledge`

| Field | Behavior | Pattern |
|-------|----------|---------|
| `facts` | **Replaced** | Node copies via `list(knowledge["facts"])`, mutates locally, returns full list |
| `citations` | **Replaced** | Same pattern |
| `conclusions` | **Replaced** | Synthesis creates fresh list; verification mutates local copy and returns |
| `verification_history` | **Replaced** | Local copy + append pattern |
| Scalar fields (`user_goal`, etc.) | **Replaced** | Simple overwrite |

### Within `execution_state`

| Field | Behavior |
|-------|----------|
| `tool_costs` | Explicitly merged in tool_identification: `{**old, **new}` |
| `tool_call_limits` | Explicitly merged in tool_identification: `{**old, **new}` |
| Everything else | Replaced (but all nodes spread `**es` so full state is preserved) |

---

## Design Notes

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

### Why `structured_output` is always set in verification

The verification model sometimes returns `tool_calls` instead of a
verification result. The node extracts these, executes them, and re-prompts
with evidence. The `structured_output` is always built from the verification
schema (`fact_results`, `conclusion_results`, etc.) — never from raw
`tool_calls` — so the frontend renderer shows the right fields.

---

## Resolved Issues

These were tracked gaps that have been fixed:

1. **`synthesis_retry_count` was never incremented.** Now incremented by
   the synthesis node on every entry (entry-count semantics). The router
   guard is `synthesis_retry_count < 2` (allow initial run + 1 retry).

2. **`research_retry_count` was never initialized.** Now declared on
   `ExecutionState`, initialized to 0 in both `streaming.py` entry points,
   and incremented by the planning node on every entry. The router guard
   is `research_retry_count < 2` (allow initial run + 1 retry). Planning
   shows retry prompts when the count read at entry is `> 0`.

3. **Verification step cost was charged per fact-check round.** The
   `deduct_cost` call has been removed from inside the fact-check
   `while` loop. The verification step cost (default 8) is now deducted
   exactly once at the top of the node. Only per-tool-call costs are
   deducted inside the loop.
