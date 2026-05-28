# Conversation State: Complete Data Inventory

This is a design document for the conversation state in MOiRA.

Section 0 defines the conceptual data model — what a conversation *is*, independent of how it is stored, transmitted, or rendered. Subsequent sections document the current implementation across all layers.

---

## 0. Conceptual Data Model

A conversation is a sequence of turns between the user and the system. Each user turn may trigger a research cycle — a structured workflow that produces a report. The data model is a tree rooted at the conversation.

### 0.1 Tree Structure

```
Conversation
├── id: UUID
├── title: string
├── created_at: datetime
│
├── Turn (one per user message)
│   ├── user_message
│   │   ├── id: int
│   │   ├── content: string              -- the user's research question
│   │   └── created_at: datetime
│   │
│   └── research_cycle (0 or 1)
│       ├── id: UUID
│       ├── thread_id: UUID              -- LangGraph checkpoint thread
│       ├── status: running | completed | error
│       ├── started_at: datetime
│       ├── completed_at: datetime
│       ├── total_elapsed_ms: int
│       │
│       ├── budget
│       │   ├── limit: int               -- starting budget (from config)
│       │   └── consumed: int            -- total spent
│       │
│       ├── error: string                -- non-empty if status is error
│       │
│       ├── prior_report_ref             -- cross-turn context (nullable)
│       │   └── report: ResearchReport   -- previous cycle's report, if any
│       │
│       ├── selected_tools: [ToolRef]    -- tools available for this cycle
│       │
│       ├── unverified_claims: [string]  -- claims from prior verifications that
│       │                                  are unsupported; empty if accepted
│       ├── contradicted_claims: [string] -- claims that actively conflict with
│       │                                  evidence; semantically distinct from
│       │                                  unverified (conflict vs. lack of support)
│       │
│       ├── execution_steps (ordered list, one entry per graph node invocation)
│       │   ├── node: string              -- graph node name: planning, tool_discovery,
│       │   │                              tool_selection, research_execution, compression,
│       │   │                              draft_synthesis, verification, report_generation
│       │   ├── label: string             -- human-readable display name (e.g. "Planning")
│       │   ├── status: not_started | running | completed | error
│       │   ├── started_at: datetime      -- when the node began executing
│       │   ├── ended_at: datetime        -- when the node finished (null if running)
│       │   ├── elapsed_ms: int           -- wall-clock duration (0 if not yet completed)
│       │   ├── cost: int                 -- budget units consumed by this step
│       │   ├── budget_remaining: int     -- budget remaining after this step
│       │   └── error: string             -- non-empty if status is error
│       │
│       ├── plan
│       │   ├── text: string             -- the research plan
│       │   └── model_thinking: string   -- chain-of-thought from the model
│       │
│       ├── tool_discovery
│       │   ├── candidates: [ToolRef]    -- tools found by semantic search
│       │   └── model_thinking: string
│       │
│       ├── tool_selection
│       │   ├── selected: [ToolRef]      -- tools chosen for this cycle
│       │   ├── selection_reasoning: string
│       │   └── model_thinking: string
│       │
│       ├── tool_executions (0 or more)
│       │   ├── tool: ToolRef
│       │   ├── arguments: object
│       │   ├── result: string
│       │   ├── success: bool
│       │   ├── duration_ms: int
│       │   └── model_thinking: string   -- reasoning that led to this call
│       │
│       ├── research_findings
│       │   ├── raw: [Finding]           -- unprocessed results
│       │   ├── compressed: [Finding]    -- summarized results
│       │   └── model_thinking: string
│       │
│       ├── draft
│       │   ├── text: string             -- pre-verification draft report
│       │   └── model_thinking: string
│       │
│       ├── verification_attempts (1 or more, one per retry cycle)
│       │   ├── attempt_number: int
│       │   ├── outcome: accept | retry | error
│       │   ├── case: int                -- 1-11, which verification case
│       │   ├── assessment: string
│       │   ├── supported_claims: [string]
│       │   ├── unsupported_claims: [string]
│       │   ├── contradictions: [string]
│       │   ├── relevance: on_topic | off_topic
│       │   ├── depth: sufficient | too_shallow
│       │   ├── guidance: string         -- retry guidance if outcome is retry
│       │   └── model_thinking: string
│       │
│       └── report (final output)
│           ├── generation_path: verified | budget_exhausted | error
│           ├── answer: string
│           ├── citations: [{ source, url?, excerpt? }]
│           ├── support: [{ content, source }]
│           ├── critiques: [string]
│           ├── unverified_claims: [string]
│           └── model_thinking: string
│
└── assistant_message (one per completed cycle)
    ├── id: int
    ├── content: string                  -- report answer text
    ├── report_json: object              -- full structured report
    └── created_at: datetime
```

### 0.2 Supporting Types

```
ToolRef
├── name: string
├── description: string
├── type: string                         -- "rest", "mcp", etc.
├── endpoint: string
├── method: string
├── argument_schema: object
├── tags: [string]
└── reliability: string

Finding
├── content: string
├── source: string
├── citation_url: string?
└── type: string
```

### 0.3 Lifecycle Notes

**Turn creation**: A turn is created when the user sends a message. The user_message is persisted immediately. If the message triggers a research cycle, the cycle begins.

**Research cycle**: The cycle passes through stages sequentially: plan → tool_discovery → tool_selection → (tool_executions)* → research_findings → draft → verification → report. The verification stage may loop back to planning (retry), which repeats the cycle. Each pass through the cycle consumes budget.

**Cycle completion**: When the cycle completes (verification accepts, or budget exhausted, or error), a report is generated and two assistant messages are created: one with the answer text, one with the structured JSON.

**Multi-turn context**: When a follow-up message is sent in the same conversation, the previous cycle's report is carried forward as context for the new planning stage. Each turn is independent — the new cycle does not inherit the old cycle's tools, findings, or draft.

**Inspectability**: Every stage of the research cycle is intended to be inspectable by the user. The `model_thinking` field at each node captures the model's chain-of-thought reasoning. The `plan`, `tool_discovery`, `tool_selection`, `research_findings`, `draft`, and `verification_attempts` capture the intermediate artifacts. These enable the user to understand *how* the system arrived at its answer, not just *what* the answer is.

### 0.4 Implementation Status of Conceptual Fields

| Conceptual field | Currently persisted | Currently transmitted | Currently displayed | Storage location |
|---|---|---|---|---|
| Conversation.id/title/created_at | yes | yes | yes | `conversations` table |
| user_message.id/content/created_at | yes | yes | yes | `messages` table |
| research_cycle.id | yes | no | no | `workflow_runs.id` |
| research_cycle.status | yes | yes (SSE) | yes | `workflow_runs.status` |
| research_cycle.started_at | yes | yes (SSE) | no | step[0].started_at |
| research_cycle.completed_at | yes | no | no | `workflow_runs.completed_at` |
| research_cycle.total_elapsed_ms | **no** | yes (SSE only) | yes (live only) | *missing from DB* |
| budget.limit | yes | yes | yes | `workflow_runs.budget_limit` |
| budget.consumed | yes | yes | yes | `workflow_runs.budget_consumed` |
| research_cycle.error | yes | yes (SSE) | yes | `workflow_runs.error` |
| research_cycle.thread_id | yes | no | no | `workflow_runs.thread_id` |
| research_cycle.prior_report_ref | **no** | no | no | *reconstructed from messages at runtime* |
| research_cycle.selected_tools | **no** | no | no | *in checkpoint only* |
| research_cycle.unverified_claims | yes | no | no | *only in state during execution, not persisted separately* |
| research_cycle.contradicted_claims | **no** | no | no | *in verification_reports only; not surfaced to planning* |
| execution_steps[*].node/label/status/started_at/elapsed_ms/cost/budget_remaining | yes | yes (SSE) | yes | `workflow_runs.steps` (JSON array) |
| execution_steps[*].ended_at | **no** | **no** | no | *not persisted — only elapsed_ms is stored* |
| plan.text | **no** | no | no | *in checkpoint only* |
| plan.model_thinking | partial | no | no | `workflow_runs.thinking_traces["planning"]` |
| tool_discovery.candidates | **no** | no | no | *in checkpoint only* |
| tool_discovery.model_thinking | partial | no | no | `thinking_traces["tool_discovery"]` |
| tool_selection.selected | **no** | no | no | *in checkpoint only* |
| tool_selection.model_thinking | partial | no | no | `thinking_traces["tool_selection"]` |
| tool_executions[*] | yes | yes (SSE) | yes | `workflow_runs.tool_calls` |
| tool_executions.model_thinking | **no** | no | no | *not captured* |
| research_findings.raw | **no** | no | no | *in checkpoint only* |
| research_findings.compressed | **no** | no | no | *in checkpoint only* |
| research_findings.model_thinking | partial | no | no | `thinking_traces["compression"]` |
| draft.text | **no** | no | no | *in checkpoint only* |
| draft.model_thinking | partial | no | no | `thinking_traces["draft_synthesis"]` |
| verification_attempts[*] | yes | yes (SSE) | partial (attempt count) | `workflow_runs.verification_reports` |
| verification.model_thinking | partial | no | no | `thinking_traces["verification"]` |
| report.generation_path | **no** | no | no | *not captured — derived at runtime from error/unverified_claims* |
| report.* | yes | yes (SSE) | yes | `workflow_runs.report` + `messages` (assistant_report) |
| report.model_thinking | partial | no | no | `thinking_traces["report_generation"]` |
| assistant_message.content | yes | yes | yes | `messages` table |
| assistant_message.report_json | yes | yes | yes | `messages` table (role=assistant_report) |

Key: "partial" for model_thinking means it is captured only if the model produces chain-of-thought output (depends on model capabilities and configuration).

---

## 1. Persistent Storage (SQLite)

### 1.1 conversations

| Column       | Type            | Notes                                                  |
|--------------|-----------------|--------------------------------------------------------|
| `id`         | TEXT PK         | UUID, generated on creation                            |
| `user_id`    | TEXT FK → users | Always `default` (single-user system)                  |
| `title`      | TEXT            | Initial value `"New Conversation"`, editable via PATCH |
| `created_at` | TEXT            | ISO datetime, server-generated                         |

### 1.2 messages

| Column            | Type                     | Notes                                                         |
|-------------------|--------------------------|---------------------------------------------------------------|
| `id`              | INTEGER PK AUTOINCREMENT | Stable server-assigned ID; used as FK target by workflow_runs |
| `conversation_id` | TEXT FK → conversations  |                                                               |
| `role`            | TEXT                     | `"user"`, `"assistant"`, or `"assistant_report"`              |
| `content`         | TEXT                     | Plain text for user/assistant; JSON for `assistant_report`    |
| `created_at`      | TEXT                     | ISO datetime, server-generated                                |

Role semantics:
- `user` — the human's query
- `assistant` — the report answer text (plain text)
- `assistant_report` — the full `ResearchReport` JSON (structured data for rendering citations, critiques, etc.)

A single workflow run produces **two** assistant messages: one `assistant` (answer text) and one `assistant_report` (structured JSON).

### 1.3 workflow_runs

| Column                 | Type                    | Notes                                                 |
|------------------------|-------------------------|-------------------------------------------------------|
| `id`                   | TEXT PK                 | UUID                                                  |
| `conversation_id`      | TEXT FK → conversations |                                                       |
| `user_message_id`      | INTEGER FK → messages   | Links the run to the user message that triggered it   |
| `thread_id`            | TEXT                    | LangGraph checkpoint thread ID                        |
| `steps`                | TEXT (JSON)             | Array of step objects (see §2.2)                      |
| `tool_calls`           | TEXT (JSON)             | Array of tool call objects (see §2.3)                 |
| `verification_reports` | TEXT (JSON)             | Array of verification report objects (see §2.4)       |
| `thinking_traces`      | TEXT (JSON)             | Object keyed by node name → thinking text             |
| `report`               | TEXT (JSON) or NULL     | The final ResearchReport (see §2.5)                   |
| `budget_limit`         | REAL                    | Starting budget for this run                          |
| `budget_consumed`      | REAL                    | Total budget consumed                                 |
| `error`                | TEXT                    | Error message if status is `"error"`, empty otherwise |
| `status`               | TEXT                    | `"running"`, `"completed"`, or `"error"`              |
| `created_at`           | TEXT                    | ISO datetime when run started                         |
| `completed_at`         | TEXT                    | ISO datetime when run finished                        |

Upsert semantics: `ON CONFLICT(id) DO UPDATE` — the streaming endpoint writes the run once after the entire stream completes.

### 1.4 checkpoints (LangGraph internal)

| Column                 | Type | Notes                             |
|------------------------|------|-----------------------------------|
| `thread_id`            | TEXT | Matches `workflow_runs.thread_id` |
| `checkpoint_ns`        | TEXT | Namespace (default `""`)          |
| `checkpoint_id`        | TEXT | Unique per checkpoint             |
| `parent_checkpoint_id` | TEXT | Previous checkpoint in the chain  |
| `type`                 | TEXT | Checkpoint type                   |
| `checkpoint`           | BLOB | Serialized `ResearchState`        |
| `metadata`             | BLOB | Checkpoint metadata               |

### 1.5 writes (LangGraph internal)

Pending state channel writes between graph steps. Used by LangGraph for resumability.

### 1.6 model_preferences

| Column                  | Type    | Notes                                |
|-------------------------|---------|--------------------------------------|
| `user_id`               | TEXT PK |                                      |
| `intelligence_endpoint` | TEXT    | Provider name for intelligence tasks |
| `intelligence_model`    | TEXT    | Model ID for intelligence tasks      |
| `task_endpoint`         | TEXT    | Provider name for task tasks         |
| `task_model`            | TEXT    | Model ID for task tasks              |

Per-user, not per-conversation. Included for completeness.

---

## 2. Workflow Run Sub-Documents (JSON within workflow_runs)

### 2.1 Step object (in `steps` array)

| Field | Type | Notes |
|---|---|---|
| `node` | string | Node name: `planning`, `tool_discovery`, `tool_selection`, `research_execution`, `compression`, `draft_synthesis`, `verification`, `report_generation` |
| `label` | string | Human-readable label (e.g. `"Planning"`, `"Researching"`) |
| `status` | string | `"completed"` or `"error"` |
| `cost` | number | Budget units consumed by this step |
| `budgetRemaining` | number | Budget remaining after this step |
| `started_at` | string | ISO datetime when node started |
| `elapsed_ms` | number | Wall-clock duration in milliseconds |

On error, also includes:
| `error` | string | Error message |

### 2.2 Tool call object (in `tool_calls` array)

| Field         | Type    | Notes                              |
|---------------|---------|------------------------------------|
| `tool`        | string  | Tool name                          |
| `output`      | string  | Tool output (truncated in display) |
| `duration_ms` | number  | Execution time in milliseconds     |
| `success`     | boolean | Whether the call succeeded         |

### 2.3 Verification report object (in `verification_reports` array)

| Field     | Type   | Notes                                 |
|-----------|--------|---------------------------------------|
| `report`  | object | Full `VerificationReport` (see §3.2)  |
| `attempt` | number | 0-indexed verification attempt number |

### 2.4 Research report (in `report` column)

| Field               | Type            | Notes                            |
|---------------------|-----------------|----------------------------------|
| `answer`            | string          | The research answer text         |
| `citations`         | array           | `{ source, url?, excerpt? }`     |
| `support`           | array           | `{ content, source }`            |
| `critiques`         | array of string | Quality critiques                |
| `unverified_claims` | array of string | Claims that couldn't be verified |
| `budget_consumed`   | number          | Total budget consumed by the run |

---

## 3. Workflow Graph State (ResearchState — in-memory, checkpointed)

This is the LangGraph `StateDict` that flows through the graph nodes. Checkpointed to SQLite after each node via `AsyncSqliteSaver`.

### 3.1 ResearchState

| Field                  | Type                     | Populated by                      | Notes                                  |
|------------------------|--------------------------|-----------------------------------|----------------------------------------|
| `question`             | string                   | Streaming endpoint                | User's original question               |
| `plan`                 | string                   | `planning` node                   | Research plan text                     |
| `active_tools`         | list[ToolDefinition]     | `tool_selection` node             | Tools selected for this run            |
| `findings`             | list[Finding]            | `research_execution` node         | Raw research findings                  |
| `compressed_findings`  | list[Finding]            | `compression` node                | Summarized findings                    |
| `draft`                | string                   | `draft_synthesis` node            | Draft report before verification       |
| `verification`         | string                   | `verification` node               | Raw verification output text           |
| `report`               | ResearchReport or null   | `report_generation` node          | Final structured report                |
| `budget_remaining`     | float                    | Each node via `deduct_cost`       | Current budget balance                 |
| `budget_limit`         | float                    | Streaming endpoint                | Starting budget (config default: 50)   |
| `verification_history` | list[VerificationReport] | `verification` node (accumulates) | All verification reports from this run |
| `unverified_claims`    | list[string]             | `verification` node               | Claims to address on retry             |
| `error`                | string                   | Any node on failure               | Non-empty means error state            |
| `thinking_traces`      | dict[string, string]     | Each node via `_extract`          | Keyed by node name                     |

### 3.2 VerificationReport

| Field                | Type         | Notes                                   |
|----------------------|--------------|-----------------------------------------|
| `outcome`            | string       | `"accept"`, `"retry"`, or `"error"`     |
| `case`               | int          | 1–11 matching verification prompt cases |
| `assessment`         | string       | Summary of the verification assessment  |
| `supported_claims`   | list[string] | Claims that are well-supported          |
| `unsupported_claims` | list[string] | Claims lacking support                  |
| `contradictions`     | list[string] | Internal contradictions found           |
| `relevance`          | string       | `"on_topic"` or `"off_topic"`           |
| `depth`              | string       | `"sufficient"` or `"too_shallow"`       |
| `guidance`           | string       | Retry guidance for the planning node    |

### 3.3 Finding

| Field          | Type              | Notes                       |
|----------------|-------------------|-----------------------------|
| `content`      | string            | Finding text                |
| `source`       | string            | Source identifier           |
| `citation_url` | string (optional) | URL for citation            |
| `type`         | string            | Finding type classification |

---

## 4. SSE Stream Events (backend → frontend)

These are the events streamed via `POST /api/conversations/{id}/messages` during a workflow run. Each event has an `event` type and a JSON `data` payload.

| Event                 | Payload                                              | When emitted             | Frontend effect                |
|-----------------------|------------------------------------------------------|--------------------------|--------------------------------|
| `budget_update`       | `{ budget_remaining, budget_consumed }`              | Before first node starts | Updates budget display         |
| `node_start`          | `{ node, started_at }`                               | Each node begins         | Creates a running step row     |
| `node_end`            | `{ budget_remaining, elapsed_ms, thinking_traces? }` | Each node completes      | Completes step, shows elapsed  |
| `tool_result`         | `{ tool, output, duration_ms, success }`             | After tool execution     | Adds to tool calls list        |
| `verification_report` | `{ report, attempt }`                                | After verification       | Increments attempt counter     |
| `run_complete`        | `{ report, total_elapsed_ms }`                       | After report_generation  | Shows report, total time       |
| `run_error`           | `{ node, error, conversation_id, total_elapsed_ms }` | On any error             | Shows error, marks step failed |

After the SSE stream ends, the backend persists:
- An `assistant` message (answer text)
- An `assistant_report` message (full JSON)
- A `workflow_runs` row (steps, tool calls, report, timing, etc.)

---

## 5. REST API Shapes

### 5.1 GET /api/conversations/{id} → ConversationDetail

```
{
  id: string,
  title: string,
  created_at: string,
  messages: [ MessageInfo ],
  runs: [ WorkflowRunInfo ]
}
```

### 5.2 MessageInfo (in API response)

```
{
  id: number,
  role: string,
  content: string,
  created_at: string
}
```

### 5.3 WorkflowRunInfo (in API response)

```
{
  id: string,
  user_message_id: number,
  steps: [ StepObject ],
  tool_calls: [ ToolCallObject ],
  verification_reports: [ { report, attempt } ],
  thinking_traces: { [node: string]: string },
  report: ResearchReport | null,
  budget_limit: number,
  budget_consumed: number,
  error: string,
  status: string,
  created_at: string,
  completed_at: string
}
```

Note: `total_elapsed_ms` is **not** currently included in the API response for persisted runs. It is only sent in the `run_complete` SSE event.

---

## 6. Frontend State (Pinia store)

### 6.1 Persisted state (hydrated from API)

| Store ref | Type | Source | Purpose |
|---|---|---|---|
| `conversations` | `ConversationInfo[]` | `GET /conversations` | Sidebar list |
| `currentConversationId` | `string \| null` | Local | Which conversation is open |
| `messages` | `MessageInfo[]` | `GET /conversations/{id}` | Message list |
| `runs` | `Map<number, WorkflowRunInfo>` | `GET /conversations/{id}` → `detail.runs` | Keyed by `user_message_id` |

### 6.2 Live streaming state (from SSE events)

| Store ref | Type | Source events | Purpose |
|---|---|---|---|
| `workflowSteps` | `WorkflowStep[]` | `node_end` | Completed steps during current run |
| `currentStep` | `WorkflowStep \| null` | `node_start` / `node_end` | Currently executing step |
| `toolCalls` | `ToolCallInfo[]` | `tool_result` | Tool calls from current run |
| `currentReport` | `ResearchReport \| null` | `run_complete` | Report from current run |
| `budgetRemaining` | `number \| null` | `budget_update` / `node_end` | Current budget balance |
| `budgetConsumed` | `number` | `budget_update` | Total budget consumed |
| `verificationAttempts` | `number` | `verification_report` | Number of verification rounds |
| `activeUserMessageId` | `number \| null` | `sendMessage` | Temp ID of triggering message |
| `totalElapsedMs` | `number \| null` | `run_complete` / `run_error` | Total wall-clock time |

### 6.3 UI state

| Store ref | Type | Purpose |
|---|---|---|
| `loading` | `boolean` | Whether a run is in progress |
| `error` | `string \| null` | Current error message |
| `isNewChat` | `boolean` | Whether we're in the "new chat" state |

---

## 7. State Lifecycle

### 7.1 Sending a message (happy path)

```
User types message
  → sendMessage()
    → resetWorkflowState()           clears live refs
    → push temp user message         id: -Date.now()
    → POST /conversations (if new)   creates conversation, gets real ID
    → streamMessage()
      → POST /conversations/{id}/messages   SSE stream begins
      ← budget_update                 → budgetRemaining, budgetConsumed
      ← node_start (planning)         → currentStep = {planning, running}
      ← node_end (planning)           → workflowSteps.push(currentStep), currentStep = null
      ← node_start (tool_discovery)   → currentStep = {tool_discovery, running}
      ← node_end (tool_discovery)     → workflowSteps.push, currentStep = null
      ... (more nodes) ...
      ← run_complete                  → currentReport = report, finalizeRun()
    ← finalizeRun()
      → build WorkflowRunInfo from live state
      → store in runs Map (keyed by temp user message ID)
      → live state NOT cleared (persists for rendering)
    → push assistant message
    → loading = false
```

### 7.2 Navigating to an existing conversation

```
User clicks conversation in sidebar
  → selectConversation(id)
    → resetWorkflowState()           clears live refs
    → GET /conversations/{id}        fetches messages + runs from API
    → messages = detail.messages
    → runs = Map keyed by user_message_id
```

### 7.3 Rendering decision (ChatView template)

For each user message in the messages array:
1. Check `runs.get(msg.id)` → if found, render `<RunArtifacts>` (persisted path)
2. Else, check if this is the last user message AND live state exists → render live streaming template

**Current gap**: After `finalizeRun`, the run is stored in the `runs` Map with a **temp negative ID**. When `selectConversation` re-fetches, the run is stored with the **real server-assigned message ID**. These IDs never match — the temp ID only exists in the frontend.

---

## 8. Data Gaps and Inconsistencies

### 8.1 `total_elapsed_ms` not in API response

The `run_complete` SSE event includes `total_elapsed_ms`, and the frontend stores it in the `runs` Map. But when the conversation is re-fetched via `GET /conversations/{id}`, the API response does not include `total_elapsed_ms` in the run data. The backend `workflow_runs` table also does not store this value.

**Impact**: Total elapsed time is lost on page reload / conversation navigation.

### 8.2 Step `started_at` and `elapsed_ms` in persisted runs

The SSE stream sends `started_at` and `elapsed_ms` per step. The step accumulator in `streaming.py` stores these. The `WorkflowRun.steps` JSON column in the database contains them. But the `GET /conversations/{id}` response passes `r.steps` through as-is (raw JSON from the DB), so these fields ARE preserved in the API response.

**Status**: Works correctly.

### 8.3 Temp vs real message IDs

`sendMessage` pushes a user message with `id: -Date.now()` (negative temp ID). The real message is created by the backend during SSE streaming, but the frontend never learns the real message ID. After `finalizeRun`, the run is keyed by the temp ID. After `selectConversation` re-fetches, the same run is keyed by the real server ID.

**Impact**: Two different keys for the same run. Not currently causing bugs because the persisted path always uses the real ID from the API.

### 8.4 Intermediate workflow content not persisted

The full `ResearchState` (plan, findings, compressed_findings, draft, thinking_traces per node, verification details) flows through the graph but only the final artifacts are persisted:
- Steps: node name, label, cost, budget, timing
- Tool calls: name, output, duration, success
- Verification reports: full report + attempt number
- Thinking traces: node → thinking text
- Report: final structured report

**Not persisted** (only in LangGraph checkpoints):
- The actual plan text (planning node output)
- Tool discovery results (which tools were available)
- Tool selection reasoning (why tools were chosen)
- Raw research findings
- Compressed/summarized findings
- Draft report (pre-verification version)
- Per-node prompts and responses

**Impact**: Future inspectability features (showing intermediate content) would need to either:
- Persist these in `workflow_runs` (e.g., new JSON columns or a separate table)
- Retrieve them from LangGraph checkpoints (requires checkpoint parsing)
- Re-derive them from `thinking_traces` (partial — only captures thinking, not content)

### 8.5 LangGraph checkpoints not cleaned up

The `checkpoints` and `writes` tables grow indefinitely as runs execute. There is no cleanup mechanism.

**Impact**: Database bloat over time. Low priority for now.

### 8.6 `assistant_report` message redundancy

The `assistant_report` message stores the same JSON as `workflow_runs.report`. Both are persisted for the same data.

**Impact**: Minor storage duplication. The `assistant_report` message enables simple message-list rendering (report content is in a message with role `assistant_report`). The `workflow_runs.report` enables structured run-centric rendering.

### 8.7 Thinking traces — stored but not displayed

`thinking_traces` are persisted in `workflow_runs` and available via API, but the frontend does not render them.

**Status**: Data is captured and persisted. Display is a future feature.
