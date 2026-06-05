# Draft-Only Retry Routing

Status: **Planned**

## Problem

When verification rejects a draft, the only retry option is routing back to `planning` — a full cycle costing 17 budget points. But some rejection reasons are synthesis problems, not research problems. The findings contain sufficient evidence, but the draft misrepresents it, goes off-topic, or contradicts itself. Re-running the entire research pipeline to gather the same evidence is wasteful.

## Solution

Add a third routing destination from verification: `retry_draft` → `draft_synthesis`. This re-synthesizes the existing findings into a new draft, skipping tool discovery, tool selection, and research execution. Cost: 7 points (draft_synthesis(3) + verification(4)) instead of 17 for a full cycle.

## Terminology

Rename the existing `"retry"` outcome to `"retry_plan"` to make the distinction explicit. The three retry outcomes are now:

- `"retry_plan"` — full cycle back to planning (cases 4, 6, 9). Need new research.
- `"retry_draft"` — draft-only retry to draft_synthesis (cases 7, 8). Re-synthesize existing findings.
- `"error"` — halt and generate report (cases 10, 11).

This rename affects the verification prompt, the router, the frontend (verification outcome badges), and existing tests. The old `"retry"` string should not remain anywhere.

## When to Use It

The verification step produces a structured outcome with a case number (1-11). The key distinction is whether the problem is in the **research** (findings are insufficient) or the **synthesis** (findings are fine but the draft is bad).

| Case | Problem | Root cause | Retry type |
|------|---------|------------|------------|
| 1-3, 5 | Acceptable or no claims | N/A (accept) | `accept` → report |
| 4 | Essential unsupported claims | Research gaps | `retry_plan` → planning |
| 6 | Factually wrong | Bad research or hallucination | `retry_plan` → planning |
| 7 | Off-topic | Synthesis ignored the question | **`retry_draft`** → draft_synthesis |
| 8 | Internal contradictions | Synthesis confused itself | **`retry_draft`** → draft_synthesis |
| 9 | Too shallow | Could be research OR synthesis | `retry_plan` → planning (ambiguous, safe default) |
| 10-11 | Technical failure | Model/infra problem | `error` → report |

Cases 7 and 8 are unambiguous synthesis failures. The findings contain relevant evidence but the draft failed to use it correctly. Case 9 is excluded because it could be either root cause — guessing wrong wastes 7 points and then requires a full cycle anyway (24 total vs 17).

## Guardrails

### One draft retry maximum

If the re-draft also fails verification, do not attempt another draft retry. Either fall through to a full cycle (`retry_plan` → planning) or accept the best available draft and generate a report. This prevents the costly loop: draft → verify → retry_draft → draft → verify → retry_draft → budget exhausted → bad report.

### Budget check

Draft retry costs 7 points. The router checks `budget_remaining >= 7` before routing to `draft_synthesis`. If budget is insufficient for even a draft retry, route directly to `report_generation`.

### Retry count in state

Add `draft_retry_count: int` to `ResearchState`, initialized to 0. The router only allows `retry_draft` when `draft_retry_count < 1`. After routing to `draft_synthesis` for retry, the draft synthesis node or the graph routing increments this counter.

## Changes Required

### 1. ResearchState

Add field:

```python
draft_retry_count: int  # tracks draft-only retries, max 1
```

File: `backend/moira/models/state.py`

### 2. Verification prompt

Update `verification.system` in `prompts.md` to explicitly map cases to outcomes:

```
Respond with a JSON object. The "outcome" field must be:
- "accept" for cases 1-3, 5
- "retry_plan" for cases 4, 6, 9 (need new research from planning)
- "retry_draft" for cases 7, 8 (findings are sufficient, draft needs re-synthesis)
- "error" for cases 10, 11
```

The model must understand that `retry_draft` means "the evidence is there but the draft misused it" — distinct from `retry_plan` which means "we need to gather different evidence."

Files: `backend/moira/resources/prompts.md`

### 3. Draft synthesis retry prompt

Add `draft_synthesis.system_retry` prompt section, similar to `planning.system_retry`. This variant includes the verification feedback so the draft synthesizer knows what went wrong:

```
The previous draft was rejected during verification. The verification assessment is below.
Produce a new draft that addresses these specific issues. The evidence has not changed —
use the same findings but produce a better synthesis.

Verification case: {case}
Assessment: {assessment}
Guidance: {guidance}
```

File: `backend/moira/resources/prompts.md`

Update `_build_draft_synthesis_messages()` in `research_nodes.py` to check for verification history and append the retry prompt when `draft_retry_count > 0`.

### 4. Graph routing

Update `make_verification_router` to handle three outcomes:

```python
{
    "planning": "planning",           # full cycle retry
    "draft_synthesis": "draft_synthesis",  # draft-only retry (NEW)
    "report_generation": "report_generation",  # terminal
}
```

Routing logic:

```
if outcome == "retry_draft":
    if draft_retry_count < 1 and budget_remaining >= DRAFT_RETRY_COST:
        return "draft_synthesis"
    else:
        # Fall through to full cycle or report
        if budget_remaining >= full_cycle_cost:
            return "planning"
        else:
            return "report_generation"

if outcome == "retry_plan":
    if budget_remaining >= full_cycle_cost:
        return "planning"
    else:
        return "report_generation"
```

File: `backend/moira/workflow/graph.py`

### 5. Budget

Add `DRAFT_RETRY_COST` constant (draft_synthesis(3) + verification(4) = 7) to `budget.py`.

File: `backend/moira/workflow/budget.py`

### 6. Initial state

Add `draft_retry_count: 0` to the initial state dict in `streaming.py`.

File: `backend/moira/api/streaming.py`

### 7. Run manager

Add `draft_retry_count` to `STAGE_LABELS` is not needed (it's not a node), but the replay and accumulation logic needs to handle the new state field.

### 8. Frontend

No frontend changes required. The draft retry appears as another `draft_synthesis` step in the execution steps list, followed by another `verification` step. The UI already handles repeating node names (from the existing retry loop).

## Cost Analysis

| Scenario | Budget cost | Notes |
|----------|-------------|-------|
| Draft accepted on first try | 17 | Normal path |
| Draft rejected, full retry | 17 + 17 = 34 | Current behavior |
| Draft rejected (case 7/8), draft retry succeeds | 17 + 7 = 24 | New path, saves 10 |
| Draft rejected (case 7/8), draft retry fails, full retry | 17 + 7 + 17 = 41 | Worse than ideal (34) by 7 |
| Draft rejected (case 7/8), draft retry fails, budget exhausted | 17 + 7 = 24 | Report generated from best draft |

The worst case (draft retry fails, then full retry) costs 7 more than going straight to full retry. This happens when the verification step incorrectly judges that findings are sufficient. For cases 7-8, this misjudgment is unlikely — off-topic and self-contradictory drafts are clearly synthesis problems.

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Draft retry wastes 7 points when findings are insufficient | Low for cases 7-8 | Medium (7 points lost) | Strict case-number gating; exclude case 9 |
| Draft retry loop (retry_draft → fail → retry_draft) | Prevented by design | N/A | `draft_retry_count < 1` guard |
| Model misclassifies case (says 7 but it's really 9) | Medium | Low (7 points) | Case mapping is explicit in prompt; model just picks a number |
| Verification guidance not specific enough for re-synthesis | Medium | Medium (another bad draft) | `system_retry_draft` prompt includes case, assessment, and guidance |

## Implementation Order

1. Add `draft_retry_count` to `ResearchState` and initial state
2. Add `DRAFT_RETRY_COST` to `budget.py`
3. Update verification prompt with explicit outcome-case mapping
4. Add `draft_synthesis.system_retry` prompt section
5. Update `draft_synthesis` node to read verification history on retry
6. Update `make_verification_router` with three-way routing
7. Add graph edge for `"draft_synthesis"` destination
8. Update `verification-outcomes.md` to reflect new case groupings
9. Tests: routing logic, draft retry prompt, budget checks, retry count guard
10. Full test suite

## Files to Modify

| File | Change |
|------|--------|
| `backend/moira/models/state.py` | Add `draft_retry_count` field |
| `backend/moira/workflow/budget.py` | Add `DRAFT_RETRY_COST = 7` |
| `backend/moira/workflow/graph.py` | Three-way conditional routing; rename `"retry"` → `"retry_plan"` |
| `backend/moira/workflow/nodes/research_nodes.py` | Draft synthesis retry prompt variant; rename outcome checks `"retry"` → `"retry_plan"` |
| `backend/moira/workflow/run_manager.py` | Rename outcome checks `"retry"` → `"retry_plan"` |
| `backend/moira/resources/prompts.md` | Verification case-outcome mapping with `"retry_plan"`/`"retry_draft"`; `draft_synthesis.system_retry` |
| `backend/moira/api/streaming.py` | `draft_retry_count: 0` in initial state |
| `agent-docs/verification-outcomes.md` | Update case groupings with new outcome names |
| `frontend/src/components/StepDetailContent.vue` | Update verification outcome badge class for `"retry_plan"` |
| `frontend/src/components/workflow-artifacts.css` | Update `.verification-outcome.retry` → `.verification-outcome.retry_plan` |
| `backend/tests/test_nodes.py` | Rename `"retry"` → `"retry_plan"` in assertions; draft retry routing tests |
| `backend/tests/test_graph.py` | Graph edge tests with new outcome names |
| `backend/tests/test_budget.py` | Draft retry cost constant |
| `backend/tests/test_integration.py` | Update outcome strings in mock responses |
