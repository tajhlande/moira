# Two-Pass Tool Discovery Plan

## Problem

The current workflow is: `planning → tool_discovery → tool_selection → research_execution`.

Planning happens before the system knows what tools are available. The planner cannot
make informed decisions about *how* to research because it doesn't know *what* it can
use. This produces generic plans that don't leverage the tool catalog.

Meanwhile, tool discovery embeds the *plan* to find matching tools — but since the plan
was written without tool awareness, the semantic match is weaker than it could be.

## Solution: Two-Pass Discovery

Restructure the graph so tool discovery runs twice — once before planning (driven by
the question) and once after (driven by the plan). Planning is informed by the first
round of discovered tools.

### New Graph Flow

```
START
  → tool_discovery (question-driven, round 1)
  → planning (question + round-1 tools)
  → tool_discovery (plan-driven, round 2)
  → tool_selection (plan + merged tool pool from both rounds)
  → research_execution
  → compression
  → draft_synthesis
  → verification
  → [conditional: planning on retry, report_generation otherwise]
  → report_generation
  → END
```

### Round 1: Question-Driven Discovery

- **Input**: the user's question text
- **Output**: `candidate_tools` (new state field)
- **Purpose**: give planning a preview of what's available so it can write a
  tool-aware plan. Even a rough semantic match from the question alone is enough
  to steer planning in the right direction.
- **No model call** — just embedding + vector search. Cost weight: 1.

### Planning (informed by tools)

- **Input**: question + round-1 discovered tools + verification history (on retry)
- **Output**: plan (same as today)
- **Change**: the planning prompt gets a list of available tools so the planner
  can reference specific tools and capabilities in the plan.
- **Model call** — intelligence model. Cost weight: 2 (unchanged).

### Round 2: Plan-Driven Discovery

- **Input**: the plan text
- **Output**: merged into `active_tools` (deduplicated union of round 1 + round 2)
- **Purpose**: the plan expands the semantic surface area beyond what the question
  alone could express. This catches tools the question didn't explicitly mention
  but the plan decided to investigate.
- **No model call** — embedding + vector search. Cost weight: 1.

### Tool Selection (unchanged logic, richer pool)

- **Input**: plan + merged tool pool (round 1 + round 2)
- **Output**: `active_tools` (final selected subset)
- **Purpose**: intelligence model picks the best tools from the full pool.
- **Model call** — intelligence model. Cost weight: 2 (unchanged).

## Changes Required

### 1. New State Field

Add `candidate_tools: list[ToolDefinition]` to `ResearchState`. This holds the
round-1 discovery results so planning can see them. Round 2's results merge into
`active_tools` as today.

File: `backend/moira/models/state.py`

### 2. Graph Edges

Change the linear path in `build_graph()`:

```python
# Current
graph.add_edge(START, "planning")
graph.add_edge("planning", "tool_discovery")

# New
graph.add_edge(START, "tool_discovery")        # round 1: question-driven
graph.add_edge("tool_discovery", "planning")    # planning sees round-1 tools
graph.add_edge("planning", "tool_discovery")    # round 2: plan-driven
```

Wait — LangGraph doesn't allow the same edge pair twice (planning → tool_discovery
and tool_discovery → planning would create a cycle in the linear path). We need
two separate node instances or a conditional edge.

**Better approach**: rename the nodes to avoid ambiguity:

```python
graph.add_edge(START, "tool_discovery")
graph.add_edge("tool_discovery", "planning")
graph.add_edge("planning", "plan_driven_discovery")
graph.add_edge("plan_driven_discovery", "tool_selection")
```

Where `plan_driven_discovery` is the same function as `tool_discovery` but reads
from state differently (searches on plan text, merges results).

**Simplest approach**: use a flag in state to distinguish rounds, and use conditional
edges from `tool_discovery` to route to either `planning` (round 1 done) or
`tool_selection` (round 2 done). But this adds complexity to the discovery node.

**Recommended approach**: create two distinct node functions that share a common
discovery helper:

```python
async def question_driven_discovery(state, config):
    """Round 1: embed the question, search for tools."""
    discovered = await _discover(state.get("question", ""), config)
    return {"candidate_tools": discovered, ...}

async def plan_driven_discovery(state, config):
    """Round 2: embed the plan, search for tools. Merge with round 1."""
    round1 = state.get("candidate_tools", [])
    round2 = await _discover(state.get("plan", ""), config)
    merged = _dedupe_tools(round1 + round2)
    return {"active_tools": merged, "candidate_tools": round1, ...}

async def _discover(query, config):
    """Shared discovery logic."""
    ...
```

File: `backend/moira/workflow/graph.py`, `backend/moira/workflow/nodes/research_nodes.py`

### 3. Planning Prompt Update

The planning system prompt should include the candidate tools from round 1 so the
planner can write a tool-aware plan. Update `_build_planning_messages()` to accept
and include `candidate_tools`.

Add a new prompt section `planning.system_tools`:

```
The following tools are available and relevant to your question:
{tool_descriptions}

Reference these tools in your plan where appropriate.
```

Files: `backend/moira/resources/prompts.md`, `backend/moira/workflow/nodes/research_nodes.py`

### 4. Budget

Current cost per full cycle: 2+1+2+5+1+3+4+3 = 21
New cost per full cycle:    1+2+1+2+5+1+3+4+3 = 22

The additional tool_discovery call costs 1 point. In a 50-point budget this is
negligible. No changes to `CostWeights` defaults needed — both discovery nodes use
the same `tool_discovery` cost weight of 1.

### 5. Retry Cycle

When verification fails and routes back to planning, the retry goes:
`verification → planning → plan_driven_discovery → tool_selection → ...`

It does **not** re-run `question_driven_discovery`. The question hasn't changed and
the tool catalog hasn't changed. The retry loop starts at planning, which uses the
already-discovered `candidate_tools`.

Update `route_after_verification` to route to `"planning"` (unchanged target), and
the graph edge from planning goes to `plan_driven_discovery` naturally.

### 6. SSE Event Labels

Add new stage labels for the UI:

```python
STAGE_LABELS = {
    ...
    "question_driven_discovery": "Discovering Tools",
    "plan_driven_discovery": "Refining Tool Selection",
    ...
}
```

Or keep the existing "Discovering Tools" label for both rounds — the user doesn't
need to know there are two discovery passes internally.

### 7. Initial State

Add `candidate_tools: []` to the initial state dict in `streaming.py`.

File: `backend/moira/api/streaming.py`

### 8. Tests

- Update `test_graph.py` to verify new edge order
- Update `test_nodes.py` to test both discovery nodes
- Update `test_integration.py` response counts (two discovery nodes now)
- Update `test_budget.py` to reflect new per-cycle cost (22 instead of 21)

## Files to Modify

| File | Change |
|------|--------|
| `backend/moira/models/state.py` | Add `candidate_tools` field |
| `backend/moira/workflow/graph.py` | New edge order, add `plan_driven_discovery` node |
| `backend/moira/workflow/nodes/research_nodes.py` | Split discovery into two nodes, update planning |
| `backend/moira/resources/prompts.md` | Add `planning.system_tools` prompt section |
| `backend/moira/api/streaming.py` | Add `candidate_tools: []` to initial state |
| `backend/moira/workflow/run_manager.py` | Add new stage label if desired |
| `backend/tests/test_graph.py` | Update edge assertions |
| `backend/tests/test_nodes.py` | Test new nodes |
| `backend/tests/test_integration.py` | Update response sequences |
| `backend/tests/test_budget.py` | Update cycle cost expectations |

## Open Questions

1. **Deduplication strategy**: When merging round-1 and round-2 results, should we
   deduplicate by tool name (keep highest-scoring) or keep both entries? Recommend:
   dedupe by name, keep the entry with the higher similarity score.

Deduplicate. There is no point in repeating tool names. Keep the highest score. 

2. **UI labeling**: Should the two discovery rounds show as separate steps in the UI,
   or collapse into one "Discovering Tools" step? Recommend: show both for
   transparency — "Discovering Tools" (round 1) and "Refining Tool Selection"
   (round 2).

the UI steps should match the graph steps, always. 

