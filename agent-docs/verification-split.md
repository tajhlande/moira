# Verification Split: Research Review + Evaluation

Active plan to replace the single `verification` node with two focused,
tool-free steps: **research_review** and **evaluation**.

## Problem

The current `verification` node asks the intelligence model (Qwen3.6-35B-A3B) to
simultaneously:

1. **Fact-check** claims against cited evidence
2. **Optionally make tool calls** for independent verification (`web_search`,
   `url_content`) using a specific JSON format (`{"tool_calls": [...]}`)
3. **Verify conclusion logic** — are conclusions supported by facts?
4. **Assess goal attainment** — does the research answer the user's question?
5. **Make a 3-way routing decision** — `accept`, `retry_research`, or
   `retry_synthesis`

All five tasks must be produced in a single JSON response with six+ fields. The
model consistently **mixes response formats** — producing bare
`{"query": "..."}` fragments alongside or instead of the verification JSON,
causing `_parse_json_object` to extract the wrong fragment. Even with the
longest-match-first parser fix, the model struggles with the combined cognitive
load.

This is a **prompt complexity problem**, not a parsing problem. The fix is to
reduce the per-step cognitive load by splitting verification into two simpler
steps, each with a single focused task.

## Solution

Replace the `verification` node with two new nodes:

| Node                | Question it answers              | Tools? | Budget |
|---------------------|----------------------------------|--------|--------|
| **research_review** | "Did we gather enough evidence?" | No     | 3      |
| **evaluation**      | "Are the conclusions valid?"     | No     | 5      |

Neither step makes tool calls. This **eliminates the format mixing problem entirely** — 
neither step produces `tool_calls` JSON. Each step has a single
task, a short system prompt, and a simple 4-field JSON output schema.

If research_review finds evidence gaps, it sends back to research (which
already has the tool-calling loop and candidate tools). If evaluation finds
conclusion problems, it sends all the way back to tool_identification (which
can discover entirely new tools and trigger a fresh approach).

## New Graph Flow

```
START
  │
  ▼
decomposition
  │
  ▼
tool_identification ◄─────────────────────────────────┐
  │                                                   │
  ▼                                                   │
planning                                              │
  │                                                   │
  ▼                                                   │
research ◄──────────────────────┐                     │
  │                              │                     │
  ▼                              │                     │
synthesis                       │                     │
  │                              │                     │
  ▼                              │                     │
research_review ── retry ────────┘                     │
  │              (max retries                            │
  │ continue      from settings)                         │
  ▼                                                      │
evaluation ────── retry ────────────────────────────────┘
  │              (max retries
  │ accept        from settings)
  ▼
report_generation
  │
  ▼
END
```

### Two retry paths

1. **research_review → retry → `research`**: Goes directly back to research
   with the existing candidate tools and tool plan. Research incorporates the
   review feedback (missing_areas) and runs additional targeted queries.
   Cycle: research → synthesis → research_review.

   This is a **light retry** — "go search more with what you have."

2. **evaluation → retry → `tool_identification`**: Goes all the way back to
   tool identification to discover potentially new tools and trigger a
   completely fresh approach. Cycle: tool_identification → planning → research
   → synthesis → research_review → evaluation.

   This is a **heavy retry** — "this approach didn't work, start over with
   different tools."

The evaluation retry is deeper than the current `retry_synthesis` — it resets
the entire research pipeline from tool identification rather than just
re-running synthesis. This lets the system completely reconsider its approach
using the current set of verified/unverified/contradicted facts as guidance
for what went wrong.

### Retry budget costs

Step costs shown are defaults; actual costs are read from runtime configuration
and may differ.

| Retry type            | Path                                                                                  | Default step cost total |
|-----------------------|---------------------------------------------------------------------------------------|-------------------------|
| research_review retry | research(10) + synthesis(5) + research_review(3)                                      | **18**                  |
| evaluation retry      | tool_identification(1) + planning(2) + research(10) + synthesis(5) + research_review(3) + evaluation(5) | **26**             |

### Routing conditions

**After research_review:**

| Condition                                                             | Target                                      |
|-----------------------------------------------------------------------|---------------------------------------------|
| `route == "continue"`                                                 | `evaluation`                                |
| `route == "retry"` AND `review_count < 3` AND budget ≥ retry cost | `research`                            |
| `route == "retry"` but retries/budget exhausted                       | `evaluation` (let evaluation run)           |
| Budget too low for research_review to run at all                      | `report_generation` (budget_exhausted path) |

**After evaluation:**

| Condition                                                                  | Target                                      |
|----------------------------------------------------------------------------|---------------------------------------------|
| `route == "accept"`                                                        | `report_generation`                         |
| `route == "retry"` AND `evaluation_count < 2` AND budget ≥ retry cost | `tool_identification` (resets review_count) |
| `route == "retry"` but retries/budget exhausted                            | `report_generation` (retry_overruled path)  |
| Budget too low for evaluation to run at all                                | `report_generation` (budget_exhausted path) |

### Retry count semantics

Each node increments its own entry counter on every entry (same pattern as
current `synthesis_retry_count`):

- `review_count`: incremented by `research_review` on every entry. **Reset to 0
  by the evaluation node when it triggers a retry.**
- `evaluation_count`: incremented by `evaluation` on every entry. Never reset.
- `research_retry_count`: still incremented by `planning` on every entry
  (preserved for planning's retry prompt logic)

**Retry limits are configurable.** The maximum number of attempts for each
step should be adjustable via:
- System settings panel (global default)
- Conversation settings panel (per-conversation override)

The router reads the effective retry limit at runtime.

**Default limits:**
- research_review: **3 attempts** (router allows retry when `review_count < 3`)
- evaluation: **2 attempts** (router allows retry when `evaluation_count < 2`)

### Counter reset on evaluation retry

When evaluation triggers a retry (route `"retry"` → `tool_identification`),
the evaluation node resets `review_count` to 0 in its state update. This
starts a fresh research review cycle within the new evaluation iteration:

```python
# In evaluation node, when route == "retry":
state_update["execution_state"]["review_count"] = 0
```

This means the two retry limits are **independent**:
- Each evaluation cycle allows up to 3 research runs (initial + 2 review retries)
- Evaluation can cycle up to 2 times (initial + 1 evaluation retry)
- **Maximum total research runs: 3 × 2 = 6**

Example worst case:
```
Evaluation cycle 1 (evaluation_count=1):
  research (1) → synthesis → research_review (count=1) → retry
  research (2) → synthesis → research_review (count=2) → retry
  research (3) → synthesis → research_review (count=3) → blocked → evaluation
  evaluation: route=retry, resets review_count=0, evaluation_count=1 < 2 → tool_identification

Evaluation cycle 2 (evaluation_count=2):
  tool_identification → planning → research (4) → synthesis → research_review (count=1) → retry
  research (5) → synthesis → research_review (count=2) → retry
  research (6) → synthesis → research_review (count=3) → blocked → evaluation
  evaluation: evaluation_count=2, not < 2 → blocked → report_generation (retry_overruled)
```

Budget will almost certainly be exhausted before reaching all 6 research runs.

---

## Node 1: research_review

**Purpose**: Evaluate whether the research gathered sufficient evidence to
answer the user's question. Mark fact statuses based on evidence quality.
Identify gaps.

**Model**: intelligence

**Budget**: 3

**Tools**: None

### Reads

| Source            | Fields                                                                                      |
|-------------------|---------------------------------------------------------------------------------------------|
| `knowledge`       | `question`, `user_goal`, `facts` (all with claims), `conclusions` (as context), `citations` |
| `execution_state` | `step_costs`, `budget_remaining`, `review_count`                                            |

### Writes

| Source            | Fields                                                                       |
|-------------------|------------------------------------------------------------------------------|
| `knowledge`       | `facts` (updated statuses + verification_notes), `review_history` (appended) |
| `execution_state` | `budget_remaining` (deducted), `review_count` (incremented)                  |

### Fact status transitions

research_review marks each fact based on its claim and cited evidence:

- `"unverified"` → `"verified"`: claim is well-supported by cited evidence
- `"unverified"` → `"contradicted"`: claim conflicts with cited evidence
- `"unverified"` → stays `"unverified"`: insufficient evidence to confirm or refute
- `"unknown"` → stays `"unknown"`: no claim was ever established (no evidence gathered)

### Output schema

```json
{
  "fact_results": [
    {"fact_id": "f001", "result": "verified", "evidence": "brief note on what confirmed it"},
    {"fact_id": "f002", "result": "contradicted", "evidence": "what contradicted it"}
  ],
  "coverage_assessment": "Brief assessment of whether the research sufficiently covered the question",
  "missing_areas": ["specific description of what's still needed", "another gap"],
  "route": "continue"
}
```

`route` values:
- `"continue"`: research is sufficient, proceed to evaluation
- `"retry"`: critical gaps remain, send back for more research

### Prompt sketch (research_review.system)

```
You are a research reviewer. Your job is to evaluate whether the research
gathered sufficient evidence to answer the user's question.

For each fact that has a claim, review the claim against its cited evidence:
- "verified": the claim is well-supported by the cited source
- "contradicted": the claim conflicts with the cited evidence
- "unverified": insufficient evidence to confirm or refute

Then assess overall coverage:
- Are the materially required facts answered?
- What specific information is still missing (if any)?

If critical facts remain unknown or contradicted, recommend retrying research.
If the research is sufficient, recommend continuing to evaluation.

Respond with ONLY a JSON object, structured like this one:
{"fact_results": [...], "coverage_assessment": "...", "missing_areas": [...], "route": "continue"|"retry"}

where the fact_id value matches one of the facts you were given, 
the coverage_assessment is a brief written review, 
and the missing_areas are a list of short text descriptions of specific 
gaps in claims or subject matter areas that need further research to answer the question.
```

---

## Node 2: evaluation

**Purpose**: Evaluate whether conclusions follow logically from verified facts.
Assess goal attainment. Make the final accept/retry decision. It is OK if some
facts remain unverified — evaluation decides whether the verified set is
*sufficient* to answer the user's question.

**Model**: intelligence

**Budget**: 5

**Tools**: None

### Reads

| Source            | Fields                                                                                                                 |
|-------------------|------------------------------------------------------------------------------------------------------------------------|
| `knowledge`       | `question`, `user_goal`, `facts` (statuses now set by research_review), `conclusions`, `evaluation_history` (on retry) |
| `execution_state` | `step_costs`, `budget_remaining`, `evaluation_count`                                                                   |

### Writes

| Source            | Fields                                                                                   |
|-------------------|------------------------------------------------------------------------------------------|
| `knowledge`       | `conclusions` (updated statuses + verification_notes), `evaluation_history` (appended)   |
| `execution_state` | `budget_remaining` (deducted), `evaluation_count` (incremented), `review_count` (reset to 0 when route is `"retry"`) |

### Conclusion status transitions

- `"unverified"` → `"verified"`: reasoning is sound, all supporting facts are verified
- `"unverified"` → `"contradicted"`: reasoning contains a logical error, or a supporting fact is contradicted
- `"unverified"` → stays `"unverified"`: one or more supporting facts are unverified

### Output schema

```json
{
  "conclusion_results": [
    {"conclusion_id": "c001", "result": "verified", "reason": "why it's valid"},
    {"conclusion_id": "c002", "result": "contradicted", "reason": "what's wrong"}
  ],
  "goal_met": true,
  "goal_assessment": "Explanation of why the goal is or isn't met",
  "route": "accept"
}
```

`route` values:
- `"accept"`: conclusions are valid and the goal is met
- `"retry"`: conclusions are flawed or the goal isn't met — restart from tool identification

### Prompt sketch (evaluation.system)

```
You are an evaluation evaluator. Examine the conclusions drawn from the research.

For each conclusion:
- Is the logical reasoning valid? Does it follow from the supporting facts?
- Are the supporting facts themselves verified?
- Does the combination of facts actually support the conclusion?

A conclusion is:
- "verified": reasoning is sound and all supporting facts are verified
- "contradicted": reasoning contains a logical error, or a supporting fact is contradicted
- "unverified": one or more supporting facts are unverified

Then assess: do the verified conclusions sufficiently answer the user's question?
It is acceptable if some facts remain unverified, as long as the verified facts
and conclusions are sufficient to answer the question.

Respond with ONLY a JSON object structured like this one:

{"conclusion_results": [...], "goal_met": true/false, "goal_assessment": "...", "route": "accept"|"retry"}

where the conclusion_id values reference conclusions you were given,
the result value is one of "verified", "unverified", or "contradicted", following
your assessment of the referenced conclusion, 
the reason is your short description of the reason for your result value,
goal_met is either true or false, judging whether the set of verified facts and conclusions
are sufficient to answer the user's question, 
goal_assessment is your short text explanation of why the goal was or was not met, and
route is one of "accept" or "retry", aligned with the goal_met outcome.
```

---

## State Model Changes (`models/knowledge.py`)

### New types

```python
class ReviewOutcome(TypedDict):
    fact_results: list[dict]       # [{fact_id, result, evidence}]
    coverage_assessment: str
    missing_areas: list[str]
    route: str                     # "continue" | "retry"

class EvaluationOutcome(TypedDict):
    conclusion_results: list[dict] # [{conclusion_id, result, reason}]
    goal_met: bool
    goal_assessment: str
    route: str                     # "accept" | "retry"
```

### Knowledge changes

```python
class Knowledge(TypedDict):
    # ... existing fields ...
    # REMOVE: verification_history: list[VerificationOutcome]
    # ADD:
    review_history: list[ReviewOutcome]
    evaluation_history: list[EvaluationOutcome]
```

### ExecutionState changes

```python
class ExecutionState(TypedDict):
    # ... existing fields ...
    # REMOVE: verification_attempts: int
    # REMOVE: synthesis_retry_count: int  (no longer needed)
    # ADD:
    review_count: int
    evaluation_count: int
    # KEEP: research_retry_count: int  (still used by planning)
```

### Removal

- `VerificationOutcome` TypedDict — removed entirely

---

## Budget Changes (`workflow/budget.py`)

### Step costs

| Node            | Old cost | New cost    |
|-----------------|----------|-------------|
| research_review | —        | 3           |
| evaluation      | —        | 5           |
| verification    | 8        | *(removed)* |

Total full cycle (default costs): decomposition(2) + tool_identification(1) +
planning(2) + research(10) + synthesis(5) + research_review(3) + evaluation(5)
+ report_generation(3) = **31** (same as before).

Step costs are read from runtime configuration and may be overridden by the
user in system settings or conversation settings.

### Retry cost functions

```python
# research_review retry: research → synthesis → research_review
_REVIEW_RETRY_NODES = (
    "research",
    "synthesis",
    "research_review",
)

# evaluation retry: tool_identification → planning → research → synthesis
# → research_review → evaluation
_EVALUATION_RETRY_NODES = (
    "tool_identification",
    "planning",
    "research",
    "synthesis",
    "research_review",
    "evaluation",
)

def review_retry_cost(step_costs: dict[str, float]) -> float:
    """Step cost of a research_review retry (research through research_review)."""
    return sum(get_node_cost(step_costs, n) for n in _REVIEW_RETRY_NODES)

def evaluation_retry_cost(step_costs: dict[str, float]) -> float:
    """Step cost of an evaluation retry (tool_identification through evaluation)."""
    return sum(get_node_cost(step_costs, n) for n in _EVALUATION_RETRY_NODES)
```

### Planning reserved budget

Planning's `reserved_budget` changes from `synthesis(5) + verification(8) +
report_generation(3) = 16` to `synthesis(5) + research_review(3) + evaluation(5)
+ report_generation(3) = 16`. Same total (with default costs).

---

## Graph Changes (`workflow/graph.py`)

### Nodes

```python
graph.add_node("research_review", research_review)
graph.add_node("evaluation", evaluation)
# REMOVE: graph.add_node("verification", verification)
```

### Edges

```python
# synthesis → research_review (was: synthesis → verification)
graph.add_edge("synthesis", "research_review")

# Conditional from research_review
graph.add_conditional_edges(
    "research_review",
    make_review_router(),
    {
        "research": "research",                           # retry → back to research
        "evaluation": "evaluation",                       # continue
        "report_generation": "report_generation",         # budget exhausted
    },
)

# Conditional from evaluation
graph.add_conditional_edges(
    "evaluation",
    make_evaluation_router(),
    {
        "tool_identification": "tool_identification",     # retry → full reset
        "report_generation": "report_generation",         # accept / declined / exhausted
    },
)
```

### Router functions

**`make_review_router()`**:
- Reads `review_history[-1].route`
- `"continue"` → `evaluation`
- `"retry"` + `review_count < 3` (or configured limit) + budget ≥ `review_retry_cost` → `research`
- `"retry"` declined (retries/budget exhausted) → `evaluation` (let evaluation evaluate what we have)
- Budget too low for research_review itself → `report_generation`

**`make_evaluation_router()`**:
- Reads `evaluation_history[-1].route`
- `"accept"` → `report_generation`
- `"retry"` + `evaluation_count < 2` (or configured limit) + budget ≥ `evaluation_retry_cost` → `tool_identification`
  - **Side effect**: evaluation node resets `review_count` to 0 when route is `"retry"`
- `"retry"` declined → `report_generation` (generation_path: `retry_overruled`)
- Budget too low for evaluation itself → `report_generation` (generation_path: `budget_exhausted`)

---

## Research Adaptations (`workflow/nodes/research.py`)

Research is now re-entered from research_review retry. It needs to incorporate
review feedback to guide additional targeted queries.

### Retry detection

Research is being re-entered from a research_review retry when `review_count >
0` AND the last review entry has route `"retry"`. After an evaluation retry
resets `review_count` to 0, research knows it's starting a fresh cycle (even
though old review_history entries still exist).

```python
review_count = es.get("review_count", 0)
review_history = knowledge.get("review_history", [])
from_review = (
    review_count > 0
    and bool(review_history)
    and review_history[-1].get("route") == "retry"
)
```

### Feedback incorporation

When re-entered from research_review:

```python
if from_review:
    last_review = review_history[-1]
    missing_areas = last_review.get("missing_areas", [])
    coverage_assessment = last_review.get("coverage_assessment", "")
```

The research system prompt gets a retry appendix listing the missing areas
and the coverage assessment. The model is instructed to focus its tool calls
on filling the identified gaps, using the existing candidate tools and
tool_call_plan as a starting point.

### Tool call limits

Tool call counts are cumulative across the original run and retries — the
retry operates within remaining call limits. If limits are exhausted, the
retry may not be productive; the review router's budget check helps prevent
this scenario.

### Prompt addition

Add `research.system_retry_review` prompt section that provides:
- The missing areas from the review
- The coverage assessment
- Instructions to focus additional queries on the gaps

Register in `prompts.py` REQUIRED_SECTIONS.

---

## Planning Adaptations (`workflow/nodes/planning.py`)

Planning is re-entered from evaluation retry (via tool_identification). It
needs to see the evaluation feedback to understand why the previous approach
failed and replan accordingly.

### Retry detection

Planning is re-entered from evaluation retry when `research_retry_count > 0`
(same trigger as current). The evaluation feedback is available in
`evaluation_history[-1]`.

### Separate retry prompts (Decision: Q1 Option B)

Per design decision, use **separate** retry prompts rather than one unified
prompt. This keeps each prompt clean and simple for the model.

```python
research_retry_count = es.get("research_retry_count", 0)
if research_retry_count > 0:
    evaluation_history = knowledge.get("evaluation_history", [])
    last_eval = evaluation_history[-1] if evaluation_history else {}
    feedback = last_eval.get("goal_assessment", "")
    failed_conclusions = [
        r for r in last_eval.get("conclusion_results", [])
        if r.get("result") != "verified"
    ]
    system_prompt += "\n\n" + get_prompt("planning.system_retry_evaluation").format(
        evaluation_feedback=feedback,
        failed_conclusions=failed_conclusions,
    )
```

Add `planning.system_retry_evaluation` prompt section. The existing
`planning.system_retry` is renamed/repurposed (it previously referenced
verification_history).

### Fact filtering

Per design decision (Q2): planning targets facts with status `"unknown"` and
`"contradicted"` only. **Do not** automatically include `"unverified"` facts —
evaluation decides whether the verified set is sufficient. If evaluation
determined that unverified facts matter, it would have routed `retry`, and
planning sees the full fact list including unverified ones, but the
`_format_unknown_facts` helper only highlights unknown and contradicted facts
as requiring investigation.

The current `_format_unknown_facts` already filters for `"unknown"` and
`"contradicted"` — no change needed.

---

## Synthesis Adaptations (`workflow/nodes/synthesis.py`)

### Simplified retry handling

With the new flow, synthesis is re-entered as part of both retry cycles, but
in both cases it receives fresh research results:

- **research_review retry** (research → synthesis → research_review):
  Research re-ran with targeted queries. Synthesis sees updated facts and
  re-derives conclusions. No specific feedback needed — it's just working
  with better data.

- **evaluation retry** (tool_identification → planning → research →
  synthesis → research_review → evaluation): Full pipeline reset. Synthesis
  sees entirely new research results. No specific feedback needed.

### What changes

- Remove `synthesis_retry_count` from ExecutionState (no longer tracked)
- Remove `synthesis.system_retry` prompt logic — synthesis always derives
  conclusions from whatever facts it receives, regardless of retry context
- The `synthesis_retry_count` entry-count guard in the router is replaced
  by `evaluation_count`

```python
# REMOVED from synthesis node:
# retry_count = es.get("synthesis_retry_count", 0)
# if retry_count > 0: ... verification_history feedback ...
# "synthesis_retry_count": retry_count + 1,

# Synthesis now simply derives conclusions from facts, no retry-aware logic.
```

If evaluation feedback would help synthesis avoid repeating the same
reasoning errors, a `synthesis.system_retry_evaluation` prompt can be added
later. For now, keep it simple — the full pipeline reset provides fresh data
that should produce different conclusions.

---

## Report Generation Changes (`workflow/nodes/report_generation.py`)

### Generation path logic

```python
error = es.get("error", "")
if error:
    generation_path = "error"
elif knowledge.get("evaluation_history"):
    latest = knowledge["evaluation_history"][-1]
    route = latest.get("route", "accept")
    if latest.get("goal_met") and not any(
        c["status"] == "contradicted"
        for c in knowledge.get("conclusions", [])
    ):
        generation_path = "verified"
    elif route == "retry":
        generation_path = "retry_overruled"
    else:
        generation_path = "budget_exhausted"
else:
    generation_path = "budget_exhausted"
```

### Report content

The report's `verified_facts`, `verified_conclusions`, `contradicted`, and
`unknown_facts` lists remain the same — they read from fact/conclusion
statuses, which are now set by research_review (facts) and evaluation
(conclusions) instead of verification (both).

---

## Streaming Init Changes (`api/streaming.py`)

Replace:
```python
"verification_attempts": 0,
"synthesis_retry_count": 0,
```
With:
```python
"review_count": 0,
"evaluation_count": 0,
```

Both entry points (`run_message` and `rerun_message`) need this change.

---

## Stage Labels

### Backend (`workflow/run_manager.py`)

```python
STAGE_LABELS = {
    "decomposition": "Analyzing question",
    "tool_identification": "Identifying tools",
    "planning": "Planning research approach",
    "research": "Researching",
    "synthesis": "Synthesizing conclusions",
    "research_review": "Reviewing research",
    "evaluation": "Evaluating conclusions",
    "report_generation": "Generating report",
}
```

### Frontend (`stores/chat.ts`)

```typescript
const STAGE_LABELS: Record<string, string> = {
    decomposition: "Analyzing question",
    tool_identification: "Identifying tools",
    planning: "Planning research approach",
    research: "Researching",
    synthesis: "Synthesizing conclusions",
    research_review: "Reviewing research",
    evaluation: "Evaluating conclusions",
    report_generation: "Generating report",
};
```

Old runs with `node = "verification"` will show the raw node name — graceful
degradation is acceptable (Decision: Q4 Option B).

---

## Prompt Changes (`resources/prompts.md`, `prompts.py`)

### Remove

- `verification.system`
- `verification.user`
- `verification.evidence`

### Add

- `research_review.system`
- `research_review.user`
- `evaluation.system`
- `evaluation.user`
- `research.system_retry_review` — feedback from research_review retry
- `planning.system_retry_evaluation` — feedback from evaluation retry

### Modify

- `planning.system_retry` — remove or repurpose; the retry feedback now comes
  from `evaluation_history` (not `verification_history`), and uses the new
  `planning.system_retry_evaluation` prompt
- `report_generation.path_retry_overruled` — update wording to reference
  evaluation retry

### `prompts.py` REQUIRED_SECTIONS

Remove verification sections, add research_review, evaluation, and new retry
prompt sections.

---

## Frontend Changes

### `RunArtifacts.vue`

Update the node name check from `"verification"` to `"research_review"` and/or
`"evaluation"` for any step-specific rendering logic.

### `StructuredOutputRenderer.vue`

Add field renderers for the new structured output schemas:

- **research_review**: `{fact_results, coverage_assessment, missing_areas, route}`
- **evaluation**: `{conclusion_results, goal_met, goal_assessment, route}`

The `route` field should render as a badge (like the current verification route
badge), with variants:
- `continue` → info/default
- `retry` (from research_review) → warning
- `accept` → success
- `retry` (from evaluation) → warning

### `stores/chat.ts`

Update `STAGE_LABELS` as described above. Any code that checks for `"verification"`
node needs updating.

### Tests (`__tests__/`)

Update mock SSE events that use `node: "verification"` to use `node:
"research_review"` or `node: "evaluation"`.

---

## API Changes (`api/routes/conversations.py`)

Rename `_verification_attempts` and split into two helpers (Decision: Q3
Option A — no backward compatibility needed):

```python
def _review_attempts(steps: list[WorkflowStep]) -> list[dict[str, Any]]:
    """Extract research_review outcomes from step details."""
    ...

def _evaluation_attempts(steps: list[WorkflowStep]) -> list[dict[str, Any]]:
    """Extract evaluation outcomes from step details."""
    ...
```

Update the conversation detail response to include `review_attempts` and
`evaluation_attempts` instead of `verification_attempts`.

---

## File Deletion

- `backend/moira/workflow/nodes/verification.py` — deleted entirely

---

## Test Strategy

### New tests

- Tests for `research_review` node in `test_nodes.py`:
  - Basic fact marking (verified/contradicted/unverified)
  - Route `"continue"` when coverage is sufficient
  - Route `"retry"` when gaps identified
  - Budget exhaustion handling
  - Empty/missing claim handling
  - Parse failure (RuntimeError on unparseable JSON)

- Tests for `evaluation` node in `test_nodes.py`:
  - Basic conclusion marking (verified/contradicted/unverified)
  - Route `"accept"` when goal met
  - Route `"retry"` when conclusions flawed
  - Route `"accept"` even with some unverified facts (sufficient verified set)
  - Budget exhaustion handling
  - Parse failure handling

### Updated tests

- `test_integration.py`:
  - Full cycle test: assert both research_review and evaluation run
  - Retry path: research_review → research (light retry)
  - Retry path: evaluation → tool_identification (heavy retry)
  - `retry_overruled` generation path (from evaluation)
  - Budget exhausted path (no evaluation history)

- Graph router tests:
  - `make_review_router()` conditions
  - `make_evaluation_router()` conditions

- Budget tests:
  - `review_retry_cost()` and `evaluation_retry_cost()`

### Removed tests

- All tests in `test_nodes.py` that test the `verification` node directly
- Tests that assert `synthesis_retry_count` behavior (counter removed)

---

## Documentation Updates

### `core/research-loop-data-flow.md`

This is the primary living reference. Update:

- **State Model table**: replace `verification_history` with `review_history`
  + `evaluation_history`; replace `verification_attempts` with `review_count` +
  `evaluation_count`; remove `synthesis_retry_count`
- **Fact Status Lifecycle**: fact transitions now happen in research_review
  (not verification)
- **Conclusion Status Lifecycle**: conclusion transitions now happen in
  evaluation (not verification)
- **Per-Node Data Flow**: replace section 6 (Verification) with sections 6
  (Research Review) and 7 (Evaluation); renumber Report Generation to 8
- **Graph Routing**: update the ASCII diagram and routing condition tables
- **Budget Cost Summary**: update step costs and retry costs
- **Merged vs. Replaced Fields**: update `review_history` and
  `evaluation_history` entries
- **Design Notes**: add notes explaining why verification was split

### `core/structured-output-renderer.md`

Add the two new structured output schemas to the field renderer registry.

---

## Implementation Order

1. **State model** (`knowledge.py`): add new types, update Knowledge and
   ExecutionState
2. **Budget** (`budget.py`): add new step costs and retry cost functions
3. **New nodes**: `research_review.py`, `evaluation.py`
4. **Graph** (`graph.py`): wire new nodes, routers, remove verification
5. **Prompts** (`prompts.md`, `prompts.py`): add new sections, remove old
6. **Research** (`research.py`): add retry feedback from research_review
7. **Planning** (`planning.py`): evaluation retry feedback with separate prompt
8. **Synthesis** (`synthesis.py`): remove retry_count and retry prompt logic
9. **Report generation** (`report_generation.py`): update generation path logic
10. **Streaming init** (`streaming.py`): update state initialization
11. **Stage labels** (`run_manager.py`, `chat.ts`): update labels
12. **Frontend** (`RunArtifacts.vue`, `StructuredOutputRenderer.vue`): update rendering
13. **API** (`conversations.py`): rename endpoints
14. **Delete** `verification.py`
15. **Tests**: write new tests, update existing tests
16. **Documentation**: update `research-loop-data-flow.md` and
    `structured-output-renderer.md`

---

## Decisions Log

| Decision | Choice | Rationale |
|---|---|---|
| Tool calls in review/evaluation? | **Tool-free** | Eliminates format mixing entirely. Research already has tools. |
| research_review retry target? | **`research`** (light retry) | Go directly back to research with existing tools + plan. Incorporate missing_areas feedback. Skip tool_identification and planning. |
| evaluation retry target? | **`tool_identification`** (heavy retry) | Full pipeline reset — discover potentially new tools and take a completely different approach. |
| Budget split? | **review=3, evaluation=5** | Review is simpler (evaluate evidence). Evaluation is harder (evaluate logic + goal). Costs are notional; actual costs from runtime config. |
| Retry path through research_review on evaluation retry? | **Yes** | Always go synthesis → research_review → evaluation. Simpler graph. Ensures new conclusions are grounded. |
| verification.py disposition? | **Delete** | No dead code. Git history preserves it. |
| Planning retry prompt? | **Separate prompts** (Q1 Option B) | `planning.system_retry_evaluation` for evaluation retry. Keeps model input clean and simple. Fewer scenarios per prompt is better for small models. |
| Include unverified facts in replanning? | **No** (Q2) | Evaluation decides if the verified set is sufficient. It's OK if some facts remain unverified. Planning targets only unknown + contradicted. |
| API endpoint naming? | **Rename** (Q3 Option A) | `review_attempts` + `evaluation_attempts`. No backward compatibility needed. |
| Legacy verification node in old runs? | **Graceful degradation** (Q4 Option B) | Don't add legacy STAGE_LABELS entry. Old runs show raw node name. Will be deleted eventually. |
| Retry limits configurable? | **Yes** | System settings + conversation settings panels. Router reads effective limit at runtime. Defaults: research_review=3, evaluation=2. |
| Counter reset on evaluation retry? | **Yes** | Evaluation node resets `review_count` to 0 when route is `"retry"`. Each evaluation cycle gets a fresh set of research retries. Max total research runs: 6 (3 per evaluation cycle × 2 evaluation cycles). |

---

## Relationship to Existing Docs

- **`research-loop-overhaul.md`**: The original design spec. This plan modifies
  the verification section of that spec.
- **`research-loop-overhaul-plan.md`**: The original implementation plan. This
  plan supersedes the verification-related sections.
- **`core/research-loop-data-flow.md`**: Living reference — must be updated as
  part of this implementation.
- **`completed/verification-outcomes.md`**: Archived. The 11 verification
  outcome cases described there are replaced by the simpler review/evaluation
  routing model.
