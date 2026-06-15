# Research Loop Overhaul — Implementation Plan

This document describes the design and implementation of the overhauled research
workflow for MOiRA. It covers the knowledge model schema, graph structure,
persistent storage, message schemas, and code components.

The current `ResearchState`, `Finding`, `VerificationReport`, and `ResearchReport`
types are replaced entirely by the new knowledge model.

---

## 1. Knowledge Model Schema

The knowledge model is a single TypedDict that serves as the LangGraph state. It
is created at decomposition, carried through every step, and updated in place.
It is the *only* state object the graph operates on.

### 1.1 Core types

```python
class Citation(TypedDict):
    id: str                        # e.g., "cit001", assigned by orchestration
    source: str                    # tool name or publication name
    url: NotRequired[str]
    title: NotRequired[str]
    excerpt: NotRequired[str]      # relevant snippet from the source


class Fact(TypedDict):
    id: str                        # e.g., "f001", assigned by orchestration
    subject: str                   # entity or topic this fact is about
    fact_needed: str               # what we want to know (set at decomposition)
    claim: NotRequired[str]        # the actual factual statement (set at research)
    relation: NotRequired[str]     # optional predicate: "weak_to", "has_type", etc.
    value: NotRequired[str]        # optional value: "Fighting x4", "Rock/Dark"
    citation_ids: NotRequired[list[str]]  # references into the citation list, e.g. ["cit001"]
    status: str                    # "unknown" | "unverified" | "verified" | "contradicted"
    verification_note: NotRequired[str]  # set by verification: why it failed or what evidence was found


class Conclusion(TypedDict):
    id: str                        # e.g., "c001", assigned by orchestration
    conclusion: str                # the reasoning result
    supporting_fact_ids: list[str] # facts this conclusion is derived from, e.g. ["f001", "f003"]
    citation_ids: NotRequired[list[str]]  # any additional citations from verification
    status: str                    # "unverified" | "verified" | "contradicted"
    reasoning: NotRequired[str]    # chain of reasoning from facts to conclusion
    verification_note: NotRequired[str]  # set by verification: why it failed or what evidence was found


    args: dict                     # arguments for the call
    target_fact_ids: list[str]     # which facts this call aims to resolve, e.g. ["f001", "f002"]
    cost: float                    # cost of this specific call


class VerificationOutcome(TypedDict):
    fact_results: list[dict]       # [{fact_id: "f001", result: "verified"|"contradicted"|...}]
    conclusion_results: list[dict] # [{conclusion_id: "c001", result: "verified"|"contradicted"|...}]
    new_unknown_facts: list[str]   # descriptions of facts the verifier thinks are needed
    goal_met: bool                 # does the evidence answer the user's question?
    goal_assessment: str           # explanation of why/why not
    route: str                     # "accept" | "retry_research" | "retry_synthesis"
```

### 1.2 Knowledge model (graph state)

The graph state is a single TypedDict with two sub-objects: `knowledge` (the
research artifact) and `execution_state` (orchestration mechanics). This
separation keeps the knowledge artifact semantically clean — it contains only
the research content that flows through the pipeline — while execution concerns
(budget, routing, tool plans) live in their own namespace.

LangGraph serializes the entire state as one object for checkpointing. The
sub-object structure is for conceptual clarity and prompt construction: nodes
that need only knowledge content receive `state["knowledge"]` or subparts within it, while orchestration
logic accesses `state["execution_state"]` or subparts within it.

```python
class Knowledge(TypedDict):
    # --- Set at decomposition, immutable thereafter ---
    question: str
    user_goal: str
    topic: str
    entities: list[str]
    concepts: list[str]

    # --- Accumulated across steps ---
    facts: list[Fact]              # grows during research, statuses updated during verification
    conclusions: list[Conclusion]  # set at synthesis, statuses updated during verification
    citations: list[Citation]      # grows during research and verification
    verification_history: list[VerificationOutcome]

    # --- Report (terminal output) ---
    report: NotRequired[ResearchReport]
    generation_path: NotRequired[str]  # "verified" | "budget_exhausted" | "error"


class ExecutionState(TypedDict):
    # --- Set per-step, consumed by downstream ---
    candidate_tools: list[ToolDefinition]  # set by tool identification
    tool_call_plan: list[ToolCallPlan]     # set by planning

    # --- Budget ---
    budget_remaining: float
    budget_limit: float
    step_costs: dict[str, float]           # per-node step costs
    tool_costs: dict[str, float]           # per-tool invocation costs
    tool_call_counts: dict[str, int]       # per-tool calls made this run (for call limits)
    total_tool_cost_consumed: float

    # --- Routing and retry ---
    error: str
    synthesis_retry_count: int
    verification_attempts: int


class ResearchState(TypedDict):
    knowledge: Knowledge
    execution_state: ExecutionState
```

### 1.3 Report output (what the user sees)

The `ResearchReport` is produced only by the report generation node and is
written to the knowledge model and persisted as the run's result. Its shape is
similar to the current report but sourced entirely from the knowledge model:

```python
class ResearchReport(TypedDict):
    answer: str                      # narrative, derived from facts + conclusions only
    citations: list[dict]            # [{id, source, url, title, excerpt}]
    verified_facts: list[dict]       # facts with status "verified"
    verified_conclusions: list[dict] # conclusions with status "verified"
    contradicted: list[dict]         # facts and conclusions that were contradicted
    unknown_facts: list[dict]        # facts still in status "unknown"
    critiques: list[str]             # weaknesses and limitations
    total_cost: float                # total budget consumed (step costs + tool costs)
    tool_call_total_cost: float      # portion of total_cost from tool invocations only
    generation_path: str             # "verified" | "budget_exhausted" | "error"
```

### 1.4 ID management

All IDs (`Fact.id`, `Conclusion.id`, `Citation.id`) are prefixed strings assigned
by orchestration code. The prefixes distinguish item types at a glance and give
the model semantic context when referencing IDs in structured output.

**ID format:** `{prefix}{NNN}` — three-digit zero-padded sequential number.

| Type | Prefix | Example |
|------|--------|---------|
| Fact | `f` | `f001`, `f012` |
| Conclusion | `c` | `c001`, `c003` |
| Citation | `cit` | `cit001`, `cit005` |

**Assignment pattern:**

- A shared helper function `next_id(prefix: str, existing_list: list) -> str`
  generates the next ID by counting existing items: `f"{prefix}{len(existing_list) + 1:03d}"`.
- When a node adds items, the orchestration code calls this for each new item.
- On retry loops, new items appended to existing lists get IDs continuing the
  sequence (e.g., if `facts` already has `f001`–`f012`, new facts start at `f013`).

**Model interaction with IDs:**

The model sees IDs in its input (the knowledge model presented in prompts) and
references them in its structured output. For example:

- **Synthesis prompt** shows facts as `f001: Tyranitar is Rock/Dark type` and asks
  the model to reference `"supporting_fact_ids": ["f001", "f003"]`.
- **Verification prompt** shows facts with their IDs and asks the model to return
  `"fact_id": "f001", "result": "verified"`.
- The model never *generates* IDs — it references IDs that were provided to it
  in the prompt. Orchestration code validates that referenced IDs exist.

This design means the model's output is ID-aware but not ID-creative. The
prefixed format helps the model distinguish fact references from conclusion
references from citation references, reducing confusion in structured output.

---

## 2. Graph Structure

### 2.1 Nodes and flow

```
START
  ↓
decomposition
  ↓
tool_identification
  ↓
planning
  ↓
research
  ↓
synthesis
  ↓
verification ───────────────────────────────────────────┐
  │                                                      │
  ├─ accept ──────────────→ report_generation → END      │
  │                                                      │
  ├─ retry_synthesis ──→ synthesis ──────────────────────┤
  │   (budget check)                                     │
  │                                                      │
  ├─ retry_research ──→ tool_identification ─────────────┤
  │   (budget check)                                     │
  │                                                      │
  └─ budget exhausted ──→ report_generation → END        │
                                                          │
error ────────────────────→ report_generation → END      │
                                                          │
```

Verification routes back to `tool_identification` (not decomposition) because
decomposed state (goal, entities, concepts) is still valid. New unknown facts
from verification are added to `facts` with status `"unknown"` and will be
picked up by tool identification and research.

### 2.2 Node specifications

For each node, the following tables define inputs, model/tool requirements,
outputs, cost, and routing.

---

#### decomposition

| Property                        | Value                                                                               |
|---------------------------------|-------------------------------------------------------------------------------------|
| **Inputs from knowledge model** | `question`                                                                          |
| **Tool calling**                | No                                                                                  |
| **Intelligence model**          | Yes — needs world knowledge to identify relevant entities and concepts              |
| **Task model**                  | No                                                                                  |
| **Outputs added**               | `user_goal`, `topic`, `entities`, `concepts`, `facts` (all with status `"unknown"`) |
| **Step cost**                   | 2                                                                                   |
| **Can be followed by**          | `tool_identification`                                                               |

Notes: The model receives the user's question and produces the decomposition
JSON. The node code assigns fact IDs and populates the knowledge model. The
prompt must instruct the model to produce structured JSON matching the
decomposition schema. The model should produce fact entries that are specific
enough to guide tool matching — "Tyranitar typing" rather than "general
information about Tyranitar."

---

#### tool_identification

| Property                        | Value                                                                   |
|---------------------------------|-------------------------------------------------------------------------|
| **Inputs from knowledge model** | `facts` (fact_needed + subject fields), `topic`, `entities`, `concepts` |
| **Tool calling**                | No (uses LanceDB vector search, not LLM tools)                          |
| **Intelligence model**          | No                                                                      |
| **Task model**                  | No (or optionally: task model rewrites facts into search queries)       |
| **Outputs added**               | `candidate_tools`                                                       |
| **Step cost**                   | 1                                                                       |
| **Can be followed by**          | `planning`                                                              |

Notes: For each fact with status `"unknown"`, embed `fact_needed` + `subject`
and search LanceDB. Merge results across all facts, deduplicate by tool name,
keep highest similarity score. Also include default tools. The result is a list
of `ToolDefinition` objects with their invocation costs attached.

The LanceDB query should combine the `fact_needed` text with the `subject` for
better semantic matching — e.g., "Tyranitar typing" rather than just "typing".
If performance is a concern, batch the embeddings and do a single vector search
with the concatenated query.

On retry (when re-entered from verification), only facts that are still
`"unknown"` or `"contradicted"` trigger new tool searches. Previously discovered
tools are preserved in `candidate_tools`.

---

#### planning

| Property                        | Value                                                                                                                          |
|---------------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| **Inputs from knowledge model** | `facts` (unknown facts), `candidate_tools` (with costs), `execution_state.budget_remaining`, `execution_state.tool_costs`, `knowledge.entities`, `knowledge.concepts`, `knowledge.user_goal` |
| **Tool calling**                | No                                                                                                                             |
| **Intelligence model**          | Yes — needs to reason about tool capabilities and cost tradeoffs                                                               |
| **Task model**                  | No                                                                                                                             |
| **Outputs added**               | `tool_call_plan`                                                                                                               |
| **Step cost**                   | 2                                                                                                                              |
| **Can be followed by**          | `research`                                                                                                                     |

Notes: The planner receives the list of unknown facts and the available tools
with their per-call costs. It produces a `tool_call_plan`: an ordered list of
proposed tool calls, each specifying the tool, arguments, which facts it targets,
and its cost. The planner should consider:

- Which facts each tool can address
- The cost of each call vs. remaining budget (minus step costs for remaining
  pipeline: synthesis + verification + report_generation, plus one more potential
  cycle)
- Whether some tool calls can resolve multiple facts at once

The planner's output is advisory — the research step may deviate from the plan
when tools return unexpected results. But the plan constrains the research step's
tool calls to the planned set unless the research step explicitly requests new
tools (by adding new unknown facts to the model).

---

#### research

| Property | Value |
|----------|-------|
| **Inputs from knowledge model** | `facts` (unknown facts), `tool_call_plan`, `candidate_tools`, `execution_state.tool_costs`, `execution_state.budget_remaining` |
| **Tool calling** | Yes — executes tools from the plan |
| **Intelligence model** | Yes — interprets tool results, decides follow-up calls |
| **Task model** | No |
| **Outputs added** | Updates `facts` (claims, statuses, citation_ids), appends to `citations`, updates `execution_state.budget_remaining`, updates `execution_state.total_tool_cost_consumed`, updates `execution_state.tool_call_counts` |
| **Step cost** | 10 |
| **Tool call costs** | Deducted from `execution_state.budget_remaining` per call, using `execution_state.tool_costs[tool_name]`. Call limits enforced via `execution_state.tool_call_counts`. |
| **Can be followed by** | `synthesis` |

Notes: The research step executes the tool call plan. For each call:

1. Check that `execution_state.budget_remaining >= execution_state.tool_costs[tool_name]`
   and that the tool's `call_limit_per_run` has not been reached. If either check fails,
   skip the call and inform the model.
2. Execute the tool call.
3. The model interprets the result and produces: updated claims for target facts,
   new citations, and optionally new unknown facts.
4. Node code assigns IDs, updates fact claims and statuses to `"unverified"`,
   and appends citations.

After executing the plan, if the model identifies additional facts needed, it
adds them as new facts with status `"unknown"`. The research step can make
additional tool calls for these new facts if budget permits, up to a configurable
maximum number of extra rounds (default: 2).

Tool call budget is deducted per-call, not per-round. If the budget runs out
mid-plan, the remaining planned calls are skipped. The model is informed of the
skip so it can work with whatever facts were gathered.

---

#### synthesis

| Property | Value |
|----------|-------|
| **Inputs from knowledge model** | `user_goal`, `facts` (all with claims), `conclusions` (empty on first pass), `entities`, `concepts` |
| **Tool calling** | No |
| **Intelligence model** | Yes — derives conclusions from facts |
| **Task model** | No |
| **Outputs added** | `conclusions` (replaced on retry, not appended) |
| **Step cost** | 5 |
| **Can be followed by** | `verification`, or `report_generation` on error |

Notes: Synthesis receives the full set of facts (with claims, statuses, and
citations) and derives conclusions. Each conclusion references supporting fact
IDs and includes a `reasoning` field showing the logical chain. The model is
instructed to derive conclusions *only* from the provided facts, not from world
knowledge.

On retry (re-entered from verification), conclusions are replaced entirely. The
model receives the verification feedback alongside the facts and produces a
revised set of conclusions.

The prompt must be strict: "Derive conclusions using only the facts provided. If
the facts are insufficient to support a conclusion, do not draw that conclusion.
Instead, note what additional facts would be needed."

---

#### verification

| Property | Value |
|----------|-------|
| **Inputs from knowledge model** | `knowledge.user_goal`, `knowledge.facts`, `knowledge.conclusions`, `knowledge.citations`, `execution_state.budget_remaining` |
| **Tool calling** | Yes — re-checks claims and logic against tools/sources |
| **Intelligence model** | Yes — evaluates fact accuracy, conclusion logic, and goal attainment |
| **Task model** | No |
| **Outputs added** | Appends to `verification_history`, updates fact and conclusion statuses, may add new unknown facts, updates `execution_state.budget_remaining` |
| **Step cost** | 8 |
| **Tool call costs** | Deducted per verification tool call from `execution_state.budget_remaining`. Call limits enforced. |
| **Can be followed by** | `report_generation` (accept or budget exhausted), `synthesis` (retry_synthesis), `tool_identification` (retry_research) |

Notes: Verification performs three tasks in a single model call:

**Task 1 — Fact verification:** For each fact with a claim, evaluate whether the
claim is supported by the cited source. If the source is a tool result, the
verifier may call tools to re-check. Facts are updated to `"verified"` or
`"contradicted"`.

**Task 2 — Conclusion verification:** For each conclusion, evaluate whether the
reasoning from supporting facts is logically valid and whether the supporting
facts are themselves verified. Conclusions are updated to `"verified"` or
`"contradicted"`.

**Task 3 — Goal assessment:** Evaluate whether the verified facts and conclusions
sufficiently address the `user_goal`.

The verifier outputs a `VerificationOutcome` with:
- Per-fact and per-conclusion results
- Whether the goal is met
- New unknown facts the verifier thinks would help
- A routing recommendation

**Routing logic** (in the conditional edge function):

```
if outcome.goal_met and no contradictions:
    route = "accept" → report_generation

elif contradictions and budget >= cost_of(retry_path):
    if only conclusion problems (facts are fine):
        route = "retry_synthesis" → synthesis
    else:
        route = "retry_research" → tool_identification
else:
    route = "budget_exhausted" → report_generation
```

The cost check for retry includes: the step cost of the retry target + remaining
pipeline steps + one more verification pass. For `retry_research`:
tool_identification(1) + planning(2) + research(10) + synthesis(5) + verification(8)
= 26 step-cost minimum, plus estimated tool costs. For `retry_synthesis`:
synthesis(5) + verification(8) = 13 step-cost minimum.

`synthesis_retry_count` limits synthesis retries (default: 1). If already
exhausted, route to `report_generation` instead of `retry_synthesis`.

---

#### report_generation

| Property                        | Value                                                                                                   |
|---------------------------------|---------------------------------------------------------------------------------------------------------|
| **Inputs from knowledge model** | `user_goal`, `question`, `facts`, `conclusions`, `citations`, `verification_history`, `generation_path` |
| **Tool calling**                | No                                                                                                      |
| **Intelligence model**          | Yes — produces narrative from structured data                                                           |
| **Task model**                  | No                                                                                                      |
| **Outputs added**               | `report`, `generation_path`                                                                             |
| **Step cost**                   | 3 (budget-exempt — always runs)                                                                         |
| **Can be followed by**          | END                                                                                                     |

Notes: Report generation is the terminal node. It produces a `ResearchReport`
from the knowledge model. The prompt is strict: "Write the report using only the
facts and conclusions in the knowledge model. Do not use world knowledge. If a
fact is contradicted or unknown, say so explicitly."

Three paths converge here, as in the current system:

1. **Verified** — verification judged that materially required facts are verified
   and conclusions adequately answer the user's question. Some non-material facts
   may remain unknown.
2. **Budget exhausted** — verification found problems but budget is insufficient
   for retry
3. **Error** — a node failed and the error handler routed here

The `generation_path` field records which path was taken. The report includes
appropriate caveats for paths 2 and 3.

---

### 2.3 Budget model

The budget has two layers:

**Step costs** (per-node, as in the current system):

| Node                | Step Cost  |
|---------------------|------------|
| decomposition       | 2          |
| tool_identification | 1          |
| planning            | 2          |
| research            | 10         |
| synthesis           | 5          |
| verification        | 8          |
| report_generation   | 3 (exempt) |

**Tool invocation costs** (per-call, deducted from budget during research and
verification):

| Tool               | Cost        | Call limit per run |
|--------------------|-------------|-------------------|
| web_search         | 5           | 10                |
| url_content        | 3           | 15                |
| calculator         | 0.1         | —                 |
| datetime           | 0.1         | —                 |
| ingested API tools | 1 (default) | —                 |

Tool costs and call limits are stored in the `tools` table (new columns:
`invocation_cost` and `call_limit_per_run`) and also loaded into
`execution_state` so the planner and executor can see them.

The tool executor enforces call limits: before each tool call, it checks the
count in `execution_state.tool_call_counts` against the tool's
`call_limit_per_run`. If the limit is reached, the executor returns a
limit-reached message instead of executing the call. The model is informed of
the limit so it can choose a different tool or approach.

**Repeat cycle cost** for retry_research: 1 + 2 + 10 + 5 + 8 = 26 (step costs only,
tool costs are additional).

**Repeat cycle cost** for retry_synthesis: 5 + 8 = 13 (step costs only).

Report generation is budget-exempt: its step cost is tracked for accounting but
never prevents execution.

**Default budget:** 60. This covers two full passes through the graph
(2 × 28 = 56 step cost for decomposition through verification) plus headroom
for tool invocation costs and the exempt report_generation step (3). The budget
range clamp remains `[35, 150]` as in the current system.

---

## 3. Persistent Storage Design

The overhaul replaces the current state model entirely. Tables that stored the
old state structure are dropped and recreated.

### 3.1 Migration strategy

A single migration drops and recreates affected tables. Existing conversation
data, messages, and tool catalog data are preserved where possible. Workflow run
and step data are not preserved — they reference the old state model.

### 3.2 Table changes

**`workflow_runs`** — simplified, drops thread_id (checkpoint state is now the
knowledge model JSON, not a LangGraph thread):

```sql
CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    user_message_id INTEGER NOT NULL REFERENCES messages(id),
    status TEXT NOT NULL DEFAULT 'running',
    budget_limit REAL NOT NULL,
    total_cost REAL NOT NULL DEFAULT 0,
    generation_path TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    total_elapsed_ms INTEGER,
    updated_at TEXT,
    knowledge_snapshot TEXT        -- JSON: full ResearchState (knowledge + execution_state)
);
```

**`workflow_steps`** — similar to current, but `detail` JSON schema changes:

```sql
CREATE TABLE workflow_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id),
    node_name TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    cost REAL,
    tool_call_cost REAL NOT NULL DEFAULT 0,  -- NEW: tool invocation cost consumed in this step
    budget_remaining REAL,
    started_at TEXT NOT NULL,
    elapsed_ms INTEGER,
    purpose TEXT,
    model TEXT,
    call_count INTEGER,
    input_tokens INTEGER,
    thinking_tokens INTEGER,
    output_tokens INTEGER,
    prompt_time_ms REAL,
    gen_time_ms REAL,
    error TEXT NOT NULL DEFAULT '',
    detail TEXT,                    -- JSON: node-specific detail (prompts, responses, tool results)
    tool_call_count INTEGER NOT NULL DEFAULT 0
);
```

**`tools`** — add invocation cost and call limit columns:

```sql
-- Migration adds:
ALTER TABLE tools ADD COLUMN invocation_cost REAL NOT NULL DEFAULT 1.0;
ALTER TABLE tools ADD COLUMN call_limit_per_run INTEGER;
-- Then update specific tools:
-- web_search: invocation_cost=5.0, call_limit_per_run=10
-- url_content: invocation_cost=3.0, call_limit_per_run=15
-- calculator: invocation_cost=0.1
-- datetime: invocation_cost=0.1
```

The `call_limit_per_run` column is nullable — tools without a limit have `NULL`,
meaning unlimited. The tool executor checks the current call count from
`execution_state.tool_call_counts` before executing and returns a limit-reached
message if the count equals or exceeds the limit. The model is informed so it
can choose a different tool.

**Tables preserved unchanged:** `users`, `conversations`, `messages`,
`model_preferences`, `tool_groups`, `credentials`, `tool_metrics`,
`inference_metrics`, `settings`, `api_sources`.

**Tables dropped and recreated:** `workflow_runs`, `workflow_steps`.

### 3.3 Knowledge model persistence

The knowledge model is serialized as JSON and stored in two ways:

1. **LangGraph checkpoint** — the standard LangGraph checkpoint mechanism stores
   the full `ResearchState` (including both `knowledge` and `execution_state`
   sub-objects) after each node. This enables resume/reconnect.
   Uses `AsyncSqliteSaver` as currently.

2. **Run-level snapshot** — on run completion, the full `ResearchState`
   (both `knowledge` and `execution_state`) is serialized to
   `workflow_runs.knowledge_snapshot`. This provides a queryable record of the
   final state without needing to reconstruct from checkpoints.

3. **Step-level detail** — each node produces a detail JSON object stored in
   `workflow_steps.detail`. This contains the node-specific operational data
   (prompt text, model response, tool call inputs/outputs) separate from the
   knowledge model state.

### 3.4 LanceDB

No schema changes to the LanceDB tool embeddings store. The tool identification
node queries it the same way, just with different query inputs (fact_needed
strings instead of a plan string).

The tool ingest enhancement (task model writing richer descriptions) changes the
*content* stored in LanceDB, not the schema. The LanceDB table continues to
store `{name, vector, description, enabled}`.

---

## 4. Message Schemas (UI Communication)

### 4.1 SSE events

The SSE architecture remains the same: the backend broadcasts `run_snapshot`
events to the frontend, and the frontend processes them reactively. The shape
of the snapshot changes to reflect the new knowledge model.

**`run_snapshot` event payload:**

```typescript
interface RunSnapshot {
    id: string;
    conversation_id: string;
    user_message_id: number;
    status: "running" | "completed" | "error" | "stopped";
    budget_limit: number;
    total_cost: number;
    tool_call_total_cost: number;        // NEW: total tool invocation cost
    total_elapsed_ms: number | null;
    error: string;
    report: ResearchReport | null;
    generation_path: string | null;    // NEW
    execution_steps: ExecutionStep[];
    knowledge_summary: KnowledgeSummary | null;  // NEW: lightweight summary for UI
    state_version: number;
    started_at: string;
    completed_at: string | null;
    updated_at: string;
    input_tokens: number;
    output_tokens: number;
    thinking_tokens: number;
}
```

**`KnowledgeSummary`** — a lightweight view of the knowledge model for the
streaming UI. The full knowledge model is only fetched on demand (for the detail
view). The summary contains counts and status distributions:

```typescript
interface KnowledgeSummary {
    topic: string;
    entities: string[];
    concepts: string[];
    fact_counts: {
        unknown: number;
        unverified: number;     // NEW: has a claim but not yet verified
        verified: number;
        contradicted: number;
    };
    conclusion_counts: {
        unverified: number;
        verified: number;
        contradicted: number;
    };
    citation_count: number;
    verification_attempts: number;
    candidate_tool_count: number;
    planned_tool_calls: number;
}
```

**`ResearchReport`** (frontend type):

```typescript
interface ResearchReport {
    answer: string;
    citations: CitationRecord[];
    verified_facts: FactRecord[];
    verified_conclusions: ConclusionRecord[];
    contradicted: (FactRecord | ConclusionRecord)[];
    unknown_facts: FactRecord[];
    critiques: string[];
    total_cost: number;
    tool_call_total_cost: number;
    generation_path: string;
}

interface CitationRecord {
    id: string;    // e.g., "cit001"
    source: string;
    url?: string;
    title?: string;
    excerpt?: string;
}

interface FactRecord {
    id: string;    // e.g., "f001"
    subject: string;
    fact_needed: string;
    claim?: string;
    relation?: string;
    value?: string;
    citation_ids: string[];
    status: string;
}

interface ConclusionRecord {
    id: string;    // e.g., "c001"
    conclusion: string;
    supporting_fact_ids: string[];
    reasoning?: string;
    status: string;
}
```

### 4.2 REST API changes

**New endpoint for knowledge model inspection:**

```
GET /api/conversations/{id}/runs/{run_id}/knowledge
```

Returns the full `ResearchState` JSON (both `knowledge` and `execution_state`
sub-objects) from `workflow_runs.knowledge_snapshot`.
Only available for completed runs. Used by the UI detail view.

**Step detail endpoint remains:**

```
GET /api/conversations/{id}/runs/{run_id}/steps/{step_id}/detail
```

The shape of the detail JSON changes per node but follows the same lazy-load
pattern. See section 5.4 for per-node detail shapes.

**Tool cost is now included in tool catalog responses:**

```
GET /api/tools/{name}
```

Response includes `invocation_cost` field.

---

## 5. Code Components

### 5.1 Files to create

| File | Purpose |
|------|---------|
| `moira/models/knowledge.py` | `ResearchState`, `Knowledge`, `ExecutionState`, `Fact`, `Conclusion`, `Citation`, `VerificationOutcome`, `ToolCallPlan`, `ResearchReport` TypedDicts |
| `moira/workflow/nodes/decomposition.py` | Decomposition node |
| `moira/workflow/nodes/tool_identification.py` | Tool identification node |
| `moira/workflow/nodes/planning.py` | Planning node |
| `moira/workflow/nodes/research.py` | Research (fact discovery) node |
| `moira/workflow/nodes/synthesis.py` | Synthesis (fact-to-conclusion) node |
| `moira/workflow/nodes/verification.py` | Verification node |
| `moira/workflow/nodes/report_generation.py` | Report generation node |

### 5.2 Files to modify

| File | Changes |
|------|---------|
| `moira/models/state.py` | Remove entirely (replaced by `knowledge.py`) |
| `moira/workflow/graph.py` | Rebuild graph with new nodes and routing |
| `moira/workflow/budget.py` | Add tool invocation cost deduction, update cycle costs |
| `moira/workflow/run_manager.py` | Update snapshot building for new state model, add `knowledge_summary` |
| `moira/workflow/nodes/research_nodes.py` | Remove (replaced by individual node files) |
| `moira/tools/discovery.py` | Update to accept fact queries instead of plan text |
| `moira/tools/catalog.py` | Add invocation cost to tool loading |
| `moira/prompts.py` | Update required sections list for new prompts |
| `moira/resources/prompts.md` | Replace all prompt sections for new workflow |
| `moira/persistence/sqlite/schema.py` | Add migration for table changes |
| `moira/persistence/sqlite/migrations/` | New migration file |
| `moira/api/streaming.py` | Update initial state construction for `ResearchState` |

### 5.3 Files to remove

| File | Reason |
|------|--------|
| `moira/models/state.py` | Replaced by `moira/models/knowledge.py` |
| `moira/workflow/nodes/research_nodes.py` | Replaced by individual node files |

### 5.4 Per-node detail JSON shapes

Each node stores its operational detail in `workflow_steps.detail` as JSON.
The knowledge model state changes are separate from this detail.

**decomposition detail:**

```json
{
    "prompt": "...",
    "response": "...",
    "model": "intelligence",
    "thinking": "...",
    "structured_output": { ... }
}
```

**tool_identification detail:**

```json
{
    "queries": [
        {"fact_id": "f001", "query": "Tyranitar typing", "top_results": ["pokeapi", "pokemon_db"]}
    ],
    "candidate_tools": ["pokeapi", "pokemon_db", "web_search"],
    "default_tools_included": ["calculator", "datetime"]
}
```

**planning detail:**

```json
{
    "prompt": "...",
    "response": "...",
    "thinking": "...",
    "structured_output": {
        "planned_calls": [
            {"tool": "pokeapi", "args": {"query": "Tyranitar"}, "target_facts": ["f001", "f002", "f003"], "cost": 1.0}
        ]
    },
    "budget_snapshot": {
        "remaining": 45,
        "planned_tool_cost": 12,
        "reserved_for_remaining_steps": 14
    }
}
```

**research detail:**

```json
{
    "prompt": "...",
    "tool_calls": [
        {"tool": "pokeapi", "args": {"query": "Tyranitar"}, "output": "...", "duration_ms": 340, "success": true}
    ],
    "facts_resolved": ["f001", "f002", "f003"],
    "facts_newly_unknown": ["f013", "f014"],
    "rounds_used": 2
}
```

**synthesis detail:**

```json
{
    "prompt": "...",
    "response": "...",
    "thinking": "...",
    "structured_output": {
        "conclusions": [
            {"id": "c001", "conclusion": "...", "supporting_facts": ["f001", "f003", "f005"], "reasoning": "..."}
        ]
    }
}
```

**verification detail:**

```json
{
    "prompt": "...",
    "response": "...",
    "thinking": "...",
    "tool_calls": [
        {"tool": "pokeapi", "args": {"query": "Tyranitar types"}, "output": "...", "duration_ms": 280}
    ],
    "structured_output": {
        "fact_results": [{"fact_id": "f001", "result": "verified"}],
        "conclusion_results": [{"conclusion_id": "c001", "result": "contradicted", "reason": "..."}],
        "goal_met": false,
        "route": "retry_synthesis"
    }
}
```

**report_generation detail:**

```json
{
    "prompt": "...",
    "response": "...",
    "thinking": "...",
    "generation_path": "verified"
}
```

### 5.5 Prompt system — full prompt drafts

The prompt file (`moira/resources/prompts.md`) is replaced with new sections for
the overhauled workflow. The section naming convention stays the same
(`## node.purpose`). Template variables use Python `.format()` syntax.

The required sections are:

```
decomposition.system
decomposition.user
planning.system
planning.user
planning.system_retry
planning.system_prior_report
planning.system_earlier_turns
research.system
research.user
synthesis.system
synthesis.user
synthesis.system_retry
verification.system
verification.user
verification.fact_check.system
verification.fact_check.user
verification.evidence
report_generation.system
report_generation.path_verified
report_generation.path_budget_exhausted
report_generation.path_error
report_generation.user
```

**Prompt composition rules:** On retry, the `_retry` variant is appended to the
base `.system` prompt for that node (not replacing it). The `_prior_report` and
`_earlier_turns` sections are appended to the base system prompt when prior
conversation context exists. A node's full system prompt is always:
`{node}.system` + any applicable retry/context appendices.

---

#### decomposition.system

```
You are a research question analyst. Given a user's research question, your job is
to decompose it into a structured analysis that identifies what information must be
discovered to answer the question.

You must NOT answer the question. You must NOT draw conclusions. Your only job is to
identify what facts would need to be known to answer the user's question.

Focus on facts that are MATERIALLY REQUIRED to answer the question — facts without
which the answer would be incomplete or incorrect. Do not enumerate every possible
fact about the domain. Instead, identify the specific, verifiable facts that are
directly necessary to address what the user is asking.

Each fact should be narrow enough that a single tool call or source could provide it.
Avoid vague facts like "general information about X" — instead, enumerate the specific
data points needed.

Respond with a JSON object with these keys:
- "user_goal": a one-sentence description of what the user wants to accomplish, written in plain language
- "topic": the broad domain of the question (e.g., "competitive pokemon", "climate science")
- "entities": list of specific named entities mentioned or implied in the question or needed to answer it
- "concepts": list of abstract concepts involved with the question or that will be needed to answer it
- "unknown_facts": list of objects, each with:
  - "subject": what entity or topic this fact is about
  - "fact_needed": a specific description of what needs to be known (e.g., "Tyranitar's typing", "OU legality rules for Gen9")

Produce enough specific facts to materially answer the question. Do not pad the list
with facts that are merely interesting about the domain but not needed for the answer.
```

#### decomposition.user

```
Research question: {question}
```

---

#### planning.system

```
You are a research planning assistant. Your job is to design a set of tool calls that
will discover the facts needed to answer a research question.

You have access to a set of candidate tools. Each tool has a cost per invocation and
may have a call limit per run. You also know the remaining budget and the cost of the
remaining pipeline steps after research. Your plan must fit within the available budget.

Rules:
- Each tool call should target one or more specific unknown facts, referenced by ID
- Prefer specialized tools over generic ones (e.g., a Pokemon API over web_search)
- Do not plan calls that exceed the available budget
- Respect call limits — if a tool has already been called up to its limit this run,
  do not plan additional calls to that tool
- If you cannot afford to resolve all facts, prioritize facts most central to the
  user's goal
- Multiple facts can be resolved by a single well-chosen tool call if the tool
  returns structured data about a subject

You will receive unknown facts in the format: ID | subject | fact_needed
Reference facts by their IDs in your plan.

Respond with a JSON object with key "calls": a list of objects, each with:
- "tool": the tool name
- "args": an object of arguments for the tool call
- "target_fact_ids": a list of fact IDs this call aims to resolve (e.g., ["f001", "f003"])
- "rationale": one sentence explaining why this call was chosen
```

#### planning.user

```
User goal: {user_goal}

Topic: {topic}
Entities: {entities}
Concepts: {concepts}

Unknown facts to resolve (ID | subject | fact_needed):
{unknown_facts}

Available tools (name | description | cost per call | calls remaining):
{tool_descriptions_with_costs_and_limits}

Budget remaining: {budget_remaining}
Cost reserved for remaining pipeline steps (synthesis + verification + report): {reserved_budget}
Available for tool calls: {available_for_tools}
```

#### planning.system_retry

```
Previous research plan was insufficient. Verification feedback:

{verification_feedback}

Facts that remain unresolved:
{unresolved_facts}

Revised unknown facts (including new facts identified during verification):
{all_unknown_facts}

Produce a revised plan that addresses the verification feedback.
```

#### planning.system_prior_report

```
Previous question: {prior_question}

Prior research report (answering the previous question):
{prior_report_answer}

This is provided for context. Key findings from prior research that are relevant to
the current question may reduce the number of new facts needed.
```

#### planning.system_earlier_turns

```
Earlier conversation history (question-answer pairs from previous turns):
{earlier_turns}

This is provided for context only. The most recent prior report is provided separately.
```

---

#### research.system

```
You are a research assistant performing fact discovery. Your job is to call tools to
find the specific facts identified in the research plan, interpret the results, and
record what you learned.

You will receive a tool call plan and a list of unknown facts with their IDs. Execute
the plan by calling tools. After each round of results, you may:
- Record discovered facts (updating claims for target facts)
- Request additional tool calls if results were incomplete
- Identify new facts that need to be discovered

Rules:
- You must NOT draw conclusions or synthesize answers — your only job is fact discovery
- When a tool returns structured data, extract specific fact claims from it
- If a tool call fails or returns empty results, try a different approach
- If a specialized tool is available for the domain, prefer it over web_search
- You may make up to {max_extra_rounds} additional rounds of tool calls beyond the
  plan if needed to fill gaps
- Respect tool call limits — if a tool returns a limit-reached message, do not call it
  again

For each fact you discover, record:
- The ID of the fact this resolves (e.g., "f001") — or if this is a newly identified
  fact, describe it
- The subject it is about
- A specific, precise claim (e.g., "Tyranitar is Rock/Dark type", not "Tyranitar has
  a type")
- An optional relation (e.g., "has_type", "weak_to", "has_ability")
- An optional value (e.g., "Rock/Dark", "Fighting x4")

Respond with a JSON object with these keys:
- "tool_calls": an array of tool call objects, each with "tool" and "args". Use an
  empty array when you are done.
- "discovered_facts": an array of objects, each with "fact_id" (the ID of the fact
  this resolves, e.g., "f001"), "subject", "claim", and optionally "relation" and
  "value". For newly identified facts not in the original list, use "fact_id": null
  and include "fact_needed" describing what the new fact is about.
- "sources": an array of objects with "source" (tool name), "url" (if applicable),
  "title", and "excerpt" (relevant snippet from the tool output). These become
  citations.
```

#### research.user

```
User goal: {user_goal}

Unknown facts (ID | subject | fact_needed):
{unknown_facts}

Tool call plan (tool | args | target fact IDs):
{tool_call_plan}

Available tools:
{tool_descriptions}
```

---

#### synthesis.system

```
You are a synthesis assistant. Your job is to derive conclusions from a set of
discovered facts. You must NOT use any world knowledge beyond what is in the facts
provided to you.

Rules:
- Derive conclusions ONLY from the provided facts
- Each conclusion must reference the specific facts that support it by ID
- Show your reasoning chain: how do the supporting facts lead to the conclusion?
- If the facts are insufficient to support a conclusion, do NOT draw that conclusion.
  Instead, note what additional facts would be needed.
- Do not combine facts in ways that introduce new information not present in the
  facts themselves. For example, if fact f005 says "X is weak to Y" and fact f008
  says "Z resists Y", you may conclude "Z covers X's weakness to Y" but you may NOT
  conclude "Z is a good teammate for X" without additional facts about team
  evaluation criteria.
- Be precise. Avoid vague conclusions that could be interpreted multiple ways.

You will receive facts in the format: ID | subject | fact_needed | claim | status
Only use facts with status "verified" or "unverified" as support. Do not draw
conclusions from facts with status "unknown" or "contradicted".

Respond with a JSON object with key "conclusions": a list of objects, each with:
- "conclusion": the derived conclusion (one clear statement)
- "supporting_fact_ids": list of fact IDs that support this conclusion (e.g., ["f001", "f005"])
- "reasoning": step-by-step explanation of how the supporting facts lead to this
  conclusion
```

#### synthesis.user

```
User goal: {user_goal}
Topic: {topic}
Entities: {entities}
Concepts: {concepts}

Discovered facts:
{facts_with_claims}

{prior_conclusions_section}
```

Where `{prior_conclusions_section}` is populated on retry:

```
Previous conclusions were rejected by verification. Feedback:
{verification_feedback}

Produce revised conclusions addressing this feedback.
```

#### synthesis.system_retry

```
Previous conclusions were rejected during verification. The verification assessment
is below.

Facts have NOT changed — only conclusions need revision.

Verification feedback:
{verification_feedback}

Produce revised conclusions that address these specific issues. The facts are
provided again for reference.
```

---

#### verification.system

```
You are a verification judge. Your job is to evaluate whether the discovered facts
and derived conclusions are correct and whether they sufficiently answer the user's
question. You have three tasks:

TASK 1: FACT VERIFICATION
For each fact that has a claim, evaluate whether the claim is accurate:
- "verified": the claim is confirmed by the cited source or by new evidence from
  tool calls
- "contradicted": the claim is contradicted by evidence
- "unverified": insufficient evidence to confirm or refute (no new source found)

You may call tools to re-check claims against independent sources. Be skeptical —
do not assume a claim is true just because it came from a tool earlier.

TASK 2: CONCLUSION VERIFICATION
For each conclusion, evaluate:
- Is the logical reasoning valid? Does it follow from the supporting facts?
- Are the supporting facts themselves verified?
- Does the combination of facts actually support the conclusion as stated?

A conclusion is:
- "verified": reasoning is sound and all supporting facts are verified
- "contradicted": reasoning contains a logical error, or a supporting fact is
  contradicted
- "unverified": one or more supporting facts are unverified, so the conclusion
  cannot be confirmed

TASK 3: GOAL ASSESSMENT
Evaluate whether the verified facts and conclusions together sufficiently address
the user's goal. The goal is met when the MATERIALLY REQUIRED facts are verified
and support conclusions that answer the user's question. Not every decomposed fact
must be resolved — only the facts necessary to support the answer. Consider:
- Are the material facts verified, or are key ones still unknown or contradicted?
- Do the conclusions drawn from verified facts adequately answer what the user asked?
- Would resolving remaining unknown facts meaningfully improve the answer?

For all three tasks together, respond with a single JSON object with these keys:
- "fact_results": list of objects with "fact_id" (the fact's ID, e.g. "f001"),
  "result" (verified/contradicted/unverified), and "evidence" (brief note on
  what confirmed or contradicted it)
- "conclusion_results": list of objects with "conclusion_id" (the conclusion's
  ID, e.g. "c001"), "result" (verified/contradicted/unverified), and "reason"
  (explanation)
- "new_unknown_facts": list of strings describing additional facts that should be
  researched to improve the answer (empty if none needed)
- "goal_met": true/false — does the evidence sufficiently answer the user's question?
- "goal_assessment": explanation of why the goal is or isn't met
- "route": choose one of:
  - "accept": facts and conclusions are verified, goal is met
  - "retry_research": some facts are contradicted or unverified and need new tool
    calls to resolve, and goal is not met
  - "retry_synthesis": facts are fine but conclusions have logical errors — research
    is not needed, only re-synthesis, but goal is not met
```

#### verification.user

```
User goal:
{user_goal}

Question:
{question}

Facts to verify (ID | subject | fact_needed | claim | status | citations):
{facts_with_claims_and_sources}

Conclusions to verify (ID | conclusion | supporting fact IDs | reasoning | status):
{conclusions_with_supporting_facts}

Available tools for re-checking:
{tool_descriptions}
```

#### verification.fact_check.system

```
You are a fact-checking assistant. Your job is to independently verify specific
claims using available tools. Be thorough and skeptical — your goal is to confirm or
refute claims using independent evidence.

You must NOT rely on your training knowledge to judge whether claims are true or
false. You must verify every factual claim using tools. Even if a claim seems
obviously correct, you must still find independent evidence to confirm it.

For each claim:
1. Identify the specific factual assertion
2. Use the appropriate tool to find independent evidence
3. Compare what you find against what is claimed

Respond ONLY with a raw JSON array of tool calls. Each call should be an object with
"tool" (tool name) and "args" (object of arguments). When finished, respond with [].
```

#### verification.fact_check.user

```
Claims to verify:
{claims_list}

Available tools:
{tool_descriptions}
```

#### verification.evidence

```
Independent fact-checking evidence gathered from tools:

{evidence}

Use this evidence to ground your verification verdict.
```

---

#### report_generation.system

```
You are the final stage of a research agent. Write a coherent research report
answering the user's question, using ONLY the facts and conclusions provided.

Rules:
- Write the report using ONLY the facts and conclusions in the knowledge model
- Do NOT use any built-in world knowledge — if a fact is not in the provided facts,
  it does not appear in the report
- Write as the agent that performed the research. Do not use phrases like "based on
  the provided evidence" or "the research suggests" — you did the research. Own the
  conclusions.
- Use third-person voice only (no "I" statements)
- For contradicted or unknown facts, state explicitly what is uncertain and why
- Include inline citation markers [n] referencing the citation list where the
  report draws directly from a cited source
- For multiple citations on the same point, use sequential markers like [2][4]
- Do not manufacture or invent any URLs or citations not provided in the data

{path_instruction}

Respond with a JSON object with these keys:
- "answer": the narrative answer text with inline [n] citation markers
- "citations": list of {id, source, url, title, excerpt} objects
- "verified_facts": list of the verified facts used in the report
- "verified_conclusions": list of the verified conclusions used in the report
- "contradicted": list of facts and conclusions that were contradicted, with brief
  explanation of what went wrong
- "unknown_facts": list of facts that remain unknown, with what evidence was needed
- "critiques": list of strings describing limitations, caveats, or weaknesses of
  the report
```

#### report_generation.path_verified

```
The answer has been verified. Present it with confidence.
```

#### report_generation.path_budget_exhausted

```
Verification could not be completed for all claims. Some facts and conclusions remain
unverified. Present the answer with explicit caveats about unverified material.
Clearly distinguish what is verified from what is uncertain.
```

#### report_generation.path_error

```
The workflow was interrupted by an error: {error}. Present whatever findings are
available and clearly note the interruption.
```

#### report_generation.user

```
Question: {question}

User goal: {user_goal}

Verified facts:
{verified_facts}

Verified conclusions:
{verified_conclusions}

Contradicted facts and conclusions:
{contradicted_items}

Unknown facts:
{unknown_facts}

All citations:
{citations}
```

---

## 6. Tool Ingest Enhancement

### 6.1 Enriched tool descriptions

When a tool is ingested (at startup for built-in tools, or when registered by
the user), the task model writes an enhanced description that focuses on **what
questions the tool answers**. This is the key input that makes LanceDB semantic
search effective for fact-to-tool matching.

The enriched description is stored in `tools.description` (replacing the
current description) and embedded into LanceDB for semantic search. The original
description is preserved in `tools.original_description` (already exists via
migration 014).

The enrichment happens at ingest time, not at query time. It is a one-time
operation per tool.

### 6.2 Enrichment prompt

The task model receives the tool's name, argument schema, original description,
and group/tags. It produces an enriched description using the following prompt:

```
You are a tool description writer for a research agent. Given a tool's name,
schema, and original description, write an enriched description that helps a
semantic search system match user questions to this tool.

Your description should describe what QUESTIONS this tool can answer and what
FACTS it can provide. Do NOT describe how to call the tool — the schema already
covers that. Focus on the information the tool returns and the domains it covers.

For example, instead of "API endpoint that accepts a Pokemon name and returns
JSON data", write "Answers questions about Pokemon species: typing, base stats,
abilities (including hidden abilities), evolution chains, and move pools. Provides
factual data about individual Pokemon including type matchups, stat distributions,
and ability lists."

Be specific about domains and data types. Mention entities and topics the tool
covers. Include synonyms and related terms that a user might search for.

Respond with ONLY the enriched description text. No JSON, no markdown, no
explanation.
```

#### Enrichment user prompt

```
Tool name: {tool_name}
Group: {group_name}
Tags: {tags}
Original description: {original_description}
Argument schema: {argument_schema}
```

### 6.3 Implementation approach

In the `BaseTool` class or the catalog ingest flow:

1. After a tool is registered, check if `original_description` is empty (meaning
   enrichment hasn't happened yet).
2. If not enriched, call the task model with the enrichment prompt.
3. The task model produces an enriched description.
4. Store the enriched description in `description`, move the original to
   `original_description`.
5. Re-embed and upsert into LanceDB.

This runs at startup for built-in tools and at registration time for user-added
tools. It adds a few seconds of startup time per tool but only runs once per
tool (subsequent startups skip enrichment if `original_description` is populated).

The current `_HINT_GENERATION_PROMPT` in `tool_provisioner.py` serves a similar
purpose but produces shorter usage hints rather than full enriched descriptions.
It should be replaced by this enrichment prompt.

---

## 7. Frontend Changes

### 7.1 New components

The frontend needs to display the knowledge model as a navigable object. This
is a new UI surface, likely a panel or expandable section within the run view.

**Knowledge panel** — shows the knowledge summary from the run snapshot:

- Topic and entities (from decomposition)
- Fact status breakdown (verified / unverified / contradicted / unknown)
- Conclusion status breakdown
- Citation count
- Verification attempt count
- Current candidate tools

**Knowledge detail view** — fetched on demand via the new API endpoint:

- Full fact list, filterable by status
- Full conclusion list with reasoning chains
- Full citation list with excerpts
- Tool call plan and execution results

### 7.2 Updated types

The TypeScript types in `frontend/src/api/client.ts` need updating to match the
new `RunSnapshot`, `ResearchReport`, `KnowledgeSummary`, `FactRecord`,
`ConclusionRecord`, and `CitationRecord` shapes.

### 7.3 Step labels

Step labels in the UI update to match the new node names:

| Node | Label |
|------|-------|
| decomposition | "Decomposing question" |
| tool_identification | "Identifying tools" |
| planning | "Planning research" |
| research | "Researching" |
| synthesis | "Synthesizing conclusions" |
| verification | "Verifying" |
| report_generation | "Generating report" |

---

## 8. Testing Strategy

### 8.1 Unit tests per node

Each node gets its own test file with mocked model responses. Tests verify:

- Correct inputs are extracted from the knowledge model
- Structured output is correctly parsed and IDs assigned
- State updates are correct (facts, conclusions, citations)
- Budget is correctly deducted
- Edge cases: empty facts, budget insufficient, model returns malformed JSON

### 8.2 Integration tests

- Full graph run with mock inference (as in the current test suite)
- Budget exhaustion path
- Verification retry paths (retry_research, retry_synthesis)
- Multiple verification cycles
- Error routing to report generation

### 8.3 Tool identification tests

- LanceDB query produces correct tool candidates for given fact queries
- Deduplication works correctly
- Default tools are included
- Enriched descriptions improve search quality (regression test)

### 8.4 Knowledge model serialization tests

- Full knowledge model round-trips through JSON serialization
- Citation references are valid after deserialization
- Fact/conclusion references are consistent

---

## 9. Open questions

- **Tool enrichment quality:** Will the task model produce useful enriched
  descriptions? This needs to be tested early. If the task model is too weak,
  the enrichment step may need to use the intelligence model, which is more
  expensive but only runs once per tool.

  A: let's try the task model and see how it does.

- **Synthesis retry effectiveness:** Is it useful to retry synthesis without
  re-researching? If the facts are correct but the conclusions are wrong, a
  synthesis retry with the same facts may just produce the same wrong
  conclusions. The `synthesis_retry_count` limit (default 1) mitigates this.

  A: Synthesis retry also includes the contex provided by verification, which 
  we hope will nudge the synthesis step out of the incorrect conclusion. 
  The point here is that this retry loop is a smaller correction, not 
  a larger one.

- **Verification tool call budget:** How much budget should verification be
  allowed to consume on tool calls? A fixed cap per verification pass, or a
  share of remaining budget? Recommendation: cap at 30% of remaining budget per
  verification pass.

  A: I don't know yet if this needs to be capped.  Let's observe and see

- **Knowledge model size limits:** For complex questions with many facts and
  conclusions, the knowledge model could grow large enough to strain context
  windows. Need monitoring and potentially a compression strategy for the model
  prompt (not the stored state — the stored state is always complete).

  A: I don't know if this is going to be a real issue or not. Let's observe and see.
  And let's set a threshold size for knowing if we need to do something about it. 

---

## 10. Phased Implementation Strategy

Each phase produces a testable, runnable system. The key constraint is that the
knowledge model and new graph structure are a breaking change — the old graph
and state model cannot coexist with the new one. The strategy is to capture a
baseline before starting, then replace the system completely.

**Before starting Phase A:**

1. Tag the current codebase: `git tag pre-overhaul-baseline`
2. Copy `data/moira.db` to `data/moira-baseline.db` for baseline runs
3. Run the evaluation harness (Phase 0) against the baseline and save results

The old system remains accessible via the tagged commit and separate database.
No feature flag or parallel graph path is needed — just a git checkout and a
different database file.

### Phase 0: Evaluation harness and baseline

**Goal:** Define success metrics and capture baseline behavior before making any
changes. This phase produces no code changes to the application — only test
fixtures and a benchmark script.

**What to build:**

1. **Canary benchmark** — the Tyranitar Gen9 OU question:

   ```
   What Pokemon synergize well with Tyranitar in Gen9 OU? Please pay special
   attention to verifying the type strengths and weaknesses, typical abilities,
   typical moves, and OU eligibility.
   ```

2. **Artifact capture script** — a script or manual procedure that, for a given
   run, captures and saves to a JSON file:
   - full tool-call trace
   - total tool calls and total `web_search` calls
   - candidate tools or tool-ranking output
   - final answer
   - citations
   - verification output
   - final report status and budget consumption

3. **Scoring rubric** — score each run on 8 categories (0/1/2 each, total 16):

   | Category | Pass (2) | Fail (0) |
   |----------|----------|----------|
   | Tool choice | Pokemon-specific tools used first for species, abilities, moves, legality | `web_search` used first for facts specialized tools should cover |
   | Search discipline | Generic search limited, only for facts structured tools don't cover | Most effort on generic search |
   | Type correctness | No material type matchup errors | Recommendation depends on incorrect type claim |
   | Ability correctness | Abilities correctly attributed and described | Wrong ability, wrong description, or niche treated as typical |
   | Typical-move discipline | Distinguishes learnset from typical OU moves | Overclaims from species data alone |
   | OU legality and metagame | Separates legal from credible/common in OU | Treats legality as relevance |
   | Synthesis discipline | Synergy claims are modest and fact-backed | Jumps from isolated facts to broad team advice |
   | Verification quality | Flags weak support, contradictions, overreach | Rubber-stamps a draft with shaky claims |

   Hard-fail categories (must score 2): type correctness, ability correctness,
   OU legality and metagame, verification quality. A run that fails any hard-fail
   category is not a success regardless of total score.

4. **Offline test fixtures** (pytest):

   - **Verification stress fixture:** a short Tyranitar-partner draft with
     planted mistakes in type, ability, typical moves, legality, and synergy
     reasoning. The verifier should catch them.
   - **Tool-routing fixture:** the decomposed facts from the canary question,
     run against a fixed tool catalog. The system should rank Pokemon-specific
     tools ahead of `web_search` for structured facts.
   - **Synthesis trap fixture:** a fixed fact bundle containing individually
     true facts that tempt an unjustified recommendation. The system should
     qualify the claim or refuse to overstate it.

**Testability:** Run the canary benchmark against the current system 1-2 times
(same model, same tools, same budget, same credentials). Save artifacts. Score
using the rubric. This establishes the baseline that the overhaul must beat.

**Exit criteria:**
- Baseline artifacts saved
- Baseline score recorded
- All three offline test fixtures defined (they will fail against the current
  system — that's expected)
- `uv run pytest` passes for existing tests (no application code changed)

---

### Phase A: Knowledge model and new graph skeleton

**Goal:** Replace the state model and graph structure. The new graph runs
end-to-end with stub prompts and produces reports. All existing tests are
replaced.

**What to build:**

1. `moira/models/knowledge.py` — all TypedDicts (ResearchState, Knowledge,
   ExecutionState, Fact, Conclusion, Citation, VerificationOutcome,
   ToolCallPlan, ResearchReport)
2. `moira/workflow/nodes/` — one file per node, each with a function that
   accepts `ResearchState` and returns partial state updates. Nodes use stub
   prompts initially — enough to produce valid structured output for testing.
3. `moira/workflow/graph.py` — new `build_graph()` that wires the 7 nodes with
   the conditional routing described in section 2.2.
4. `moira/workflow/budget.py` — updated cost weights, tool invocation cost
   deduction logic, and call limit enforcement.
5. `moira/models/state.py` — removed.
6. `moira/workflow/nodes/research_nodes.py` — removed.
7. Database migration — drop and recreate `workflow_runs` and `workflow_steps`,
   add `invocation_cost` and `call_limit_per_run` to `tools`.

**Testability:** Unit tests for each node with mocked model responses. Full
graph integration test with mock inference that runs start to end and produces
a `ResearchReport`. Budget exhaustion test. Verification routing test (accept,
retry_research, retry_synthesis).

**Exit criteria:**
- `uv run pytest` passes with all new tests
- `uv run ruff check .` clean
- Graph runs end-to-end with mock inference producing valid reports on all
  three paths (verified, budget_exhausted, error)

---

### Phase B1: Prompt system wiring and contract alignment

**Status: COMPLETE**

**What was built:**

1. **Replace `moira/resources/prompts.md`** — replace all old-loop sections with the
   new section names and prompt text from section 5.5. The old sections
   (`planning.system`, `tool_selection.*`, `research_execution.*`, `compression.*`,
   `draft_synthesis.*`, `verification.system`, `verification.user`, etc.) are
   removed. The new sections are:
   - `decomposition.system`, `decomposition.user`
   - `planning.system`, `planning.user`, `planning.system_retry`,
     `planning.system_prior_report`, `planning.system_earlier_turns`
   - `research.system`, `research.user`
   - `synthesis.system`, `synthesis.user`, `synthesis.system_retry`
   - `verification.system`, `verification.user`
   - `verification.fact_check.system`, `verification.fact_check.user`,
     `verification.evidence`
   - `report_generation.system`, `report_generation.user`,
     `report_generation.path_verified`, `report_generation.path_budget_exhausted`,
     `report_generation.path_error`
   - Keep `tool_discovery.query_rewrite.system` and
     `tool_discovery.query_rewrite.user` for the tool identification node's
     optional query rewriting feature.

2. **Update `moira/prompts.py`** — replace `REQUIRED_SECTIONS` with the new section
   names listed above.

3. **Wire all nodes to `get_prompt()`** — each node file replaces its hardcoded
   `_STUB_SYSTEM` and `_STUB_USER` strings with calls to `get_prompt()`. Template
   variables are filled with `.format(**kwargs)` as before.

4. **Fix research response contract** — the research prompt and node must agree on
   one response format. The model returns a JSON object every round with keys:
   - `tool_calls`: array of `{tool, args}` objects (empty `[]` when done)
   - `discovered_facts`: array of `{fact_id, subject, claim, relation?, value?}`
   - `sources`: array of `{source, url?, title?, excerpt?}`
   
   The loop parser reads `tool_calls` from that object each round.
   `discovered_facts` and `sources` are applied each round, not only at the end.
   The parse-correction prompt must ask for the object format, not a raw array.
   This is the most critical contract fix because the research node drives all
   downstream data.

5. **Fix verification response contract** — the verification prompt output keys
   must match the current node expectations:
   - `fact_results` (not `supported_claims`/`unsupported_claims`)
   - `conclusion_results`
   - `new_unknown_facts`
   - `goal_met`
   - `goal_assessment`
   - `route` with values `"accept"`, `"retry_research"`, `"retry_synthesis"`
   
   Old-loop outcome names (`retry_plan`, `retry_draft`) must not appear.

6. **Fix report generation response contract** — the prompt must instruct the model
   to produce a JSON object matching the current `ResearchReport` shape:
   - `answer`, `citations`, `critiques`
   - `verified_facts`, `verified_conclusions`, `contradicted`, `unknown_facts`
   - `total_cost`, `tool_call_total_cost`, `generation_path`
   
   Old-loop fields like `support` and `unverified_claims` must be removed.

7. **Add prompt contract tests** — tests that verify:
   - All required sections load without errors
   - Each node's template variables match what the node code provides
   - Research response parsing handles the `{tool_calls, discovered_facts, sources}`
     object format
   - Verification response parsing handles the new route names

**Prompt composition rules (unchanged from section 5.5):** On retry, the `_retry`
variant is appended to the base `.system` prompt. The `_prior_report` and
`_earlier_turns` sections are appended when prior conversation context exists.
A node's full system prompt is always: `{node}.system` + applicable appendices.

**Key implementation details:**

- The research node's multi-round loop must parse the object format, not raw
  arrays. The `_parse_tool_calls` helper extracts from `parsed["tool_calls"]`
  rather than treating the entire response as an array.
- The research node's correction prompt must say: "Respond with a JSON object
  with keys tool_calls, discovered_facts, and sources" rather than "Respond with
  a raw JSON array."
- The research node must apply `discovered_facts` and `sources` each round,
  not only after the loop ends.
- The planning node's user prompt must include the full template variables from
  section 5.5 (`topic`, `entities`, `concepts`, `reserved_budget`,
  `available_for_tools`, `tool_descriptions_with_costs_and_limits`) in addition
  to the variables the stub used.
- The synthesis node must handle retry by appending `synthesis.system_retry` to
  the base system prompt when `synthesis_retry_count > 0`.
- The planning node must handle retry by appending `planning.system_retry` and
  handle prior context by appending `planning.system_prior_report` and
  `planning.system_earlier_turns`.

**Testability:** All existing unit and integration tests continue to pass (they
mock model responses in the correct format). New prompt contract tests verify
that real prompt text loads and renders. A real-model smoke test (one conversation)
should produce valid structured output at every node.

**Exit criteria:**
- All old-loop prompt sections removed from `prompts.md`
- All new sections present and loadable via `get_prompt()`
- All nodes use `get_prompt()` instead of stub strings
- Research, verification, and report_generation response contracts aligned
- `uv run pytest` passes (437 passed), `uv run ruff check .` clean
- Real-model conversation produces valid output at every step
- Post-loop fact extraction handles models that omit discovered_facts
- execute_batch kwarg bug fixed, plan-matching no longer marks failed calls

---

### Phase B: Prompts and real model execution

**Status: COMPLETE** (merged with Phase B1)

**Goal:** Replace stub prompts with the full prompt drafts from section 5.5.
Run the graph against a real model and verify it produces meaningful
decomposition, research, synthesis, and reports.

**What to build:**

1. `moira/resources/prompts.md` — replaced with new section structure and full
   prompt text from section 5.5.
2. `moira/prompts.py` — updated `REQUIRED_SECTIONS` list.
3. Each node file — wire up real prompt loading and template variable
   substitution. Handle prompt composition (base system + retry/context
   appendices).
4. `moira/api/streaming.py` — update initial state construction to build a
   `ResearchState` with `question` and budget fields.

**Testability:** Prompt validation tests (all required sections present, all
template variables used by node code are provided). Run the graph with a real
model on a few test questions and inspect the knowledge model at each step.

**Exit criteria:**
- All prompt sections load and render without errors
- Graph runs against real model produce valid decomposition, tool calls,
  facts, conclusions, and reports
- `uv run pytest` passes, `uv run ruff check .` clean

---

### Phase C: Tool identification and budget integration

**Goal:** Wire up LanceDB-based tool identification with fact queries and
per-tool invocation costs. The planner sees tool costs and produces cost-aware
plans.

**What to build:**

1. Update `tool_identification` node to embed each `fact_needed` + `subject`
   and search LanceDB, merging and deduplicating results across all facts.
2. Update `tools` table loading to include `invocation_cost` and
   `call_limit_per_run`.
3. Update `planning` node to include tool costs, call limits, and budget
   information in its prompt.
4. Update `research` node to deduct tool invocation costs from budget per call
   and enforce call limits.
5. Update `verification` node to deduct tool invocation costs from budget per
   call and enforce call limits.

**Testability:** Tool identification unit tests with a test LanceDB fixture.
Planning tests verify cost-aware plans. Research and verification tests verify
budget deduction per tool call. Call limit enforcement tests.

**Exit criteria:**
- LanceDB search with fact queries returns relevant tools
- Planner produces cost-aware plans that respect budget
- Tool invocation costs are deducted and tracked
- Call limits are enforced (executor returns limit-reached message)
- `uv run pytest` passes, `uv run ruff check .` clean

---

### Phase D: Run manager, SSE streaming, and API updates

**Goal:** Update the run manager and SSE streaming to work with the new
knowledge model. The frontend displays the new run structure.

**What to build:**

1. `moira/workflow/run_manager.py` — update snapshot building to produce the
   new `RunSnapshot` shape including `KnowledgeSummary` and updated
   `ResearchReport`.
2. `moira/api/streaming.py` — update event handling for new snapshot shape.
3. Frontend TypeScript types — updated `RunSnapshot`, `ResearchReport`,
   `KnowledgeSummary`, `FactRecord`, `ConclusionRecord`, `CitationRecord`.
4. Frontend step labels — updated to new node names.
5. `GET /api/conversations/{id}/runs/{run_id}/knowledge` endpoint.

**Testability:** API tests verify new snapshot shape. Frontend renders reports
with new structure. Knowledge endpoint returns valid JSON for completed runs.

**Exit criteria:**
- SSE streaming works end-to-end with new snapshot shape
- Frontend renders reports with verified facts, conclusions, and citations
- Knowledge model endpoint returns valid data for completed runs
- `uv run pytest` passes, `vue-tsc` clean, `ruff` clean

---

### Phase D1: Verification fact-checking with tool calls

**Status: COMPLETE (implemented via inline tool-calling loop, not separate sub-loop)**

**Implementation deviation:** The plan described a separate fact-check sub-loop
using `verification.fact_check.system` and `verification.fact_check.user` prompts.
The actual implementation takes a simpler approach: when the model returns
`tool_calls` instead of a verification verdict, the node executes them and
re-prompts with evidence (using `verification.evidence`). The
`verification.fact_check.*` prompts were removed as dead config. The
`verification.system` prompt instructs the model that it may call tools to
re-check claims, which is sufficient to trigger inline fact-checking.
cannot re-check them against independent sources. This is a significant
quality gap: the verification rubric's hard-fail category "verification quality"
requires that the system flags weak support and contradictions, which tool-backed
verification can do much more reliably than model-only judgment.

**Goal:** Wire the verification node to use the tool executor for independent
fact-checking. The model identifies checkable claims, calls tools to verify them,
and uses the evidence to ground its verdict. This produces the `verification.evidence`
section that the verification prompt already references.

**What to build:**

1. **Fact-check sub-loop in `verification.py`** — after the model produces its
   initial verification verdict, if `route != "accept"`, extract checkable claims
   and run a fact-checking sub-loop:
   - Identify facts with `status="unverified"` or claims the model flagged as
     uncertain
   - For each checkable claim, call the model with
     `verification.fact_check.system/user` to produce tool calls
   - Execute those tool calls via the tool executor
   - Collect evidence
   - Append `verification.evidence` to the verification prompt
   - Re-run the verification model with the additional evidence

2. **Budget-aware fact-checking** — each tool call during verification deducts from
   the remaining budget. The fact-check loop must respect the budget and stop when
   remaining budget is insufficient for more calls. Track tool costs and call counts
   in `execution_state` (same as research node).

3. **Evidence integration** — the evidence from fact-checking is included in the
   verification step detail under `tool_results` and in the `structured_output`
   so the frontend can display what was checked and what was found.

4. **Routing impact** — fact-checking evidence may change the verification route.
   A fact that appeared unverified may become verified (or contradicted) after
   independent checking. The re-run after evidence must produce an updated route.

**Key implementation details:**

- The fact-check model call uses `verification.fact_check.system` which instructs
  the model to return raw JSON arrays of tool calls (same format as research loop).
- Parse and execute these calls the same way as the research loop (with allowed
  tool filtering).
- The evidence summary is formatted using `verification.evidence` template.
- The full verification prompt becomes: `verification.system` + evidence section +
  original `verification.user` content.
- Only run fact-checking if budget allows at least one tool call after the
  verification step cost.
- The fact-check sub-loop is bounded (max 2 rounds, max 3 tool calls per round).

**Testability:**
- Unit test: verification with fact-checking produces `tool_results` in step detail
- Unit test: fact-checking evidence changes verification route (contradicted → verified)
- Unit test: fact-checking respects budget (skips when insufficient)
- Unit test: fact-checking loop bounded by max rounds

**Exit criteria:**
- Verification node calls tools to independently verify claims
- Evidence is recorded in step detail
- Budget is deducted for verification tool calls
- `uv run pytest` passes, `uv run ruff check .` clean
- Real-model run shows tool calls during verification step

---

### Phase D2: Tool ingest enrichment

**Status: COMPLETE**

**What was built:**

1. Added `tool_enrichment.system` and `tool_enrichment.user` prompts to
   `prompts.md`. The batch prompt asks the task model to write rich descriptions
   describing what QUESTIONS each tool answers and what FACTS it provides.
2. Created `moira/tools/enrichment.py` — a reusable `enrich_tool_descriptions()`
   function that sends tools to the task model and returns enriched descriptions.
   Handles model unavailability, parse errors, and call failures gracefully.
3. Updated `ToolProvisioner` — replaced `_HINT_GENERATION_PROMPT` and
   `_generate_usage_hints` with the new enrichment module. API tools now get
   full enriched descriptions (replacing, not appending to, the raw spec text).
4. Updated `service_setup.py` startup flow:
   - Enrichment step after catalog loading: enriches any tool whose
     `original_description` is empty (not yet enriched). Enriched descriptions
     are persisted to DB and reflected in the catalog before LanceDB ingest.
   - Startup sync preserves enriched descriptions across restarts: if
     `original_description` matches the current code-level description, the
     enriched `description` is kept. If the code description changed,
     `original_description` is reset so enrichment re-runs automatically.
   - Fixed LanceDB ingest gate: tools are always ingested at startup
     (previously gated on `if config.tools:`, which skipped built-in tools
     when no YAML tools were configured).
5. 7 unit tests in `tests/test_enrichment.py`.

---

### Phase F: Knowledge panel and frontend polish

**Goal:** Add the knowledge model visualization to the frontend. Users can
inspect facts, conclusions, citations, and verification results as a
navigable object.

**What to build:**

1. `KnowledgePanel` component — shows knowledge summary from run snapshot.
2. `KnowledgeDetail` view — fetched on demand, shows full fact/conclusion/
   citation lists with filtering.
3. Citation linking in report view — `[n]` markers link to citation detail.

**Testability:** Manual UI review. Component tests for knowledge panel.

**Exit criteria:**
- Knowledge panel renders during and after runs
- Fact/conclusion lists are filterable by status
- Citation markers in reports are clickable
- `vue-tsc` clean

---

### Dependency order

```
Phase 0 (evaluation harness + baseline)
  └── Phase A (knowledge model + graph skeleton)
       └── Phase B1/B (prompts + real model execution)
            └── Phase C (tool identification + budget)
                 └── Phase D (run manager + SSE + API)
                      ├── Phase D1 (verification fact-checking with tools)
                      ├── Phase D2 (frontend ChatView spec fixes)
                      ├── Phase E (tool ingest enrichment)
                      └── Phase F (knowledge panel + frontend)
```

Phases D1, D2, E and F are independent of each other once D is complete. Phase A
is the only phase that breaks the existing system — after A, the old graph and
state model are gone. This is intentional: the new system replaces the old one
completely, and there is no migration path for old run data.

The baseline captured in Phase 0 provides the comparison point. After Phase B,
run the canary benchmark against the new system and score it. The overhaul is
successful if the new system scores higher than the baseline on the rubric,
with no hard-fail categories failing.
  
