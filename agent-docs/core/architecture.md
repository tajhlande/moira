# MOiRA Research Agent Platform — Project Goals, Architecture, and Implementation Guidelines

## Overview

The MOiRA project is a self-hosted interactive research agent platform built around:

* local and self-hosted LLM inference
* structured agentic workflows
* dynamic tool usage
* verification-oriented research loops
* conversational UI interaction

The system is not intended to be a generic chatbot. It is intended to function as a:

> stateful, tool-using research and reasoning system with interactive conversational access.

The platform should support:

* Adapting REST APIs as tools
* Using MCPs as tools
* retrieval systems
* semantic tool discovery
* multi-model orchestration
* verification and citation workflows

The system must prioritize:

* transparency
* inspectability
* deterministic orchestration
* extensibility
* robustness under large tool catalogs


## The Name

"MOiRA" stands for "My Open intelligent Research Agent".  Use `moira` as the base name or namespace for code or configuration for this project. 

---

# System Components

The frontend is NaiveUI and Typescript.

Icons use Tabler Icons via `@vicons/tabler`, rendered inside NaiveUI's `NIcon` wrapper.

The backend is Python 3.13, with the following additional component libraries:

* FastAPI
* LangGraph
* httpx
* LanceDB
* sentence-transformers

The system uses two storage backends, both in-process with file-on-disk persistence and no required infrastructure:

* **SQLite** — relational data: conversations, users, projects, tool metadata, workflow checkpoints (via LangGraph's `SqliteSaver`)
* **LanceDB** — vector data: tool embeddings, semantic tool discovery, embedding search

All database-specific code must live behind an agnostic persistence API layer. See Loose Coupling and Component Replaceability below.





# Core Architectural Principles

## Loose coupling and component replaceability

All database-specific code must be contained behind an agnostic API layer of repository classes and interface methods. No part of the application outside the persistence layer should import or depend on database-specific modules directly.

### Store Responsibilities

* **SQLite** — relational data: conversations, users, projects, tool metadata, workflow state, LangGraph checkpoints
* **LanceDB** — vector data: tool description embeddings, semantic similarity search, tool discovery queries

### Repository Pattern

Each store should be accessed through repository classes that define a stable internal API:

```text
Application Code
        ↓
Repository Interfaces (moira.persistence.interfaces)
        ↓
Repository Implementations (moira.persistence.sqlite, moira.persistence.lancedb)
        ↓
Database Drivers (sqlite3, lancedb)
```

Repository interfaces must define all operations the application needs (e.g., `get_tools_by_domain`, `search_tools_by_embedding`, `save_conversation`, `load_checkpoint`) without exposing the underlying database technology.

### Migration Path

The initial implementation uses SQLite and LanceDB for zero-infrastructure local deployment. The agnostic API layer must support a future migration path to:

* **PostgreSQL + pgvector** — consolidating both relational and vector data into a single database
* **Other RDBMS** — MariaDB or similar for relational data

When this migration occurs, only the repository implementations should change. Application code, workflow nodes, and the API layer should remain unchanged.


# Application Design Principles 

## Service Dependency Injection

Application services (repositories, inference clients, model registries, tool catalogs, workflow runners) must never be instantiated directly inside API route handlers, workflow nodes, or other services.

Instead, all services are registered in a central `service_setup` module:

```text
service_setup.init_services()          # create all service instances
service_setup.shutdown_services()      # tear down connections, release resources
service_setup.service_provider(name)   # retrieve a service by registered name
```

FastAPI route handlers receive services via `Depends`:

```python
session_service: ConversationService = Depends(
    lambda: service_provider("conversation_service")
)
```

This ensures:

* **independent testability** — tests call `init_services()` with mock implementations and verify behavior without touching real databases or model endpoints
* **replaceable implementations** — swapping SQLite for PostgreSQL, or a local embedding model for a remote endpoint, only requires a new service class registered under the same name
* **explicit dependency graphs** — every service dependency is visible in the registration, not scattered across import statements

The `service_setup` module is the single place where concrete service classes are instantiated and wired together. No other module should import a concrete service implementation directly.

## The UI is NOT the agent

The conversational UI is only a frontend interface.

The agent runtime owns:

* orchestration
* workflow state
* tool selection
* verification
* model routing
* execution tracing

The architecture should always follow:

```text
UI
  ↓
Agent API / LangGraph Runtime
  ↓
Workflow Graph
  ↓
Tool Layer
  ↓
Model Router (e.g. llama-swap)
  ↓
Inference Backends (e.g. llama.cpp, vLLM)
```

The UI must never directly manage:

* tools
* planning
* workflow sequencing
* retries
* verification logic

---

## The system is a workflow engine, not a prompt

Avoid architectures based on:

* giant prompts
* hidden chain abstractions
* implicit orchestration

All meaningful execution should occur through:

* explicit graph nodes
* explicit state transitions
* explicit tool provisioning

---

## Tool usage must be structured and constrained

The system must never expose the full tool catalog to the model indiscriminately.

Tool exposure should be:

* contextual
* dynamically selected
* semantically filtered

The model should choose among:

* a small, relevant subset of tools
* selected specifically for the current workflow step

---

## Verification is a separate phase

Verification is not part of drafting.

The system must explicitly:

1. produce a draft answer
2. verify the draft against tools and sources
3. route back to planning if verification fails and budget remains, or proceed to report generation

Verification nodes should:

* aggressively use tools
* identify unsupported statements
* identify contradictions
* identify missing evidence

---

## Conversation and Turn Semantics

The system is conversational, but each user message starts a **fresh graph run**. Research workflows do not persist across turns as running graphs.

### Per-Turn Context

Each new run receives:

* **The prior ResearchReport** — always included as context for the new run. The report is a sufficient summary of prior work for planning and research.
* **The user's current message** — the new question or follow-up

### Transcript Access Tool

The full chat transcript is **not** included in context by default. Instead, the agent has access to a `conversation_history` tool that retrieves prior messages on demand.

This ensures the context window is not always stuffed with conversation history that may not be relevant. The report summarizes what matters; the transcript provides detail when the agent needs it.

### Conversation Continuity

* Each conversation contains multiple turns
* Each turn produces a ResearchReport, persisted in the database
* The UI displays the sequence of reports as a conversation
* Follow-up questions see the prior report, enabling iterative research without full context reloading

---

## Auth and Identity

The system is single-user by default for the MVP. No authentication is required.

The data model supports user identity from the start — conversations, projects, and research state include a user ID field. This allows multi-user support to be added later without schema migration.

Future capabilities:

* Simple authentication (username/password or API key)
* Per-user tool catalogs and model preferences
* Access control for shared projects

---

# High-Level Workflow

## Canonical Research Workflow

```text
                              ┌──────────────┐
                              │              │
User Query                    │              │
    ↓                         │              │
Planning ─────────────────────┤  Retry Loop  │
    ↓                         │              │
Tool Discovery                │              │
    ↓                         │              │
Tool Selection                │              │
    ↓                         │              │
Research Execution            │              │
    ↓                         │              │
Compression                   │              │
    ↓                         │              │
Draft Synthesis               │              │
    ↓                         │              │
Verification ─────────────────┘              │
    ↓                                         │
    ├─ Pass ──────────────┐                   │
    │                     ↓                   │
    └─ Budget Exhausted → Report Generation   │
                              ↓
                        Final Response
```

### Verification Routing Logic

After the Verification node, the graph conditionally routes based on:

1. **Pass** — verification found no unsupported claims, contradictions, or missing evidence. Route to Report Generation.
2. **Fail + Budget Sufficient** — verification identified issues and `budget_remaining` is at least the cost of a full retry cycle (Planning + Tool Discovery + Tool Selection + Research Execution + Compression + Draft Synthesis + Verification). Append the `VerificationReport` to `verification_history`, route back to Planning. The next planning pass uses the accumulated verification history to adjust the research plan.
3. **Fail + Budget Insufficient** — budget is insufficient for another full cycle. Route to Report Generation. The verification node writes unverified claims to `unverified_claims` in state so Report Generation can include them.

Report Generation is budget-exempt — it always executes regardless of `budget_remaining`. Its cost weight is tracked for accounting and logging but never gates execution.

Both the pass and budget-insufficient paths converge on Report Generation. The graph always terminates through this node. See the Verification Retry Loop section for full details on the retry mechanism.

The budget is decremented at each node execution. When `budget_remaining` falls below a node's cost weight, that node cannot execute and the graph must terminate.

---

# LangGraph Design Guidelines

## State-Centric Design

All workflows should operate on explicit shared state.

Example state structure:

```python
class VerificationReport(TypedDict):
    failed_claims: list[str]
    contradictions: list[str]
    missing_evidence: list[str]
    suggestions: list[str]

class Finding(TypedDict):
    content: str
    source: str
    citation_url: NotRequired[str]
    type: str  # "evidence" | "citation" | "contradiction" | "note"

class ResearchReport(TypedDict):
    answer: str
    citations: list[dict]
    support: list[dict]
    critiques: list[str]
    unverified_claims: list[str]
    budget_consumed: float

class ResearchState(TypedDict):
    question: str
    plan: str
    active_tools: list  # model-selected subset from Tool Selection node
    findings: list[Finding]
    compressed_findings: list[Finding]
    draft: str
    verification: str
    unverified_claims: list[str]
    report: ResearchReport
    budget_remaining: float
    budget_limit: float
    verification_history: list[VerificationReport]
```

State must remain:

* inspectable
* serializable
* resumable

---

## Node Design

Each node should define:

* purpose
* model selection
* available tools
* cost weight
* input state requirements
* output state modifications
* failure conditions

Example:

```text
Node: Verification

Purpose:
    Validate factual claims in draft output.

Model:
    intelligence

Cost Weight:
    4

Tools:
    web_search
    paper_search
    citation_lookup

Failure Conditions:
    unsupported claims
    contradictory evidence
    missing citations
    budget insufficient for retry

Routing on Failure:
    if budget_remaining >= full_cycle_cost:
        produce VerificationReport
        append to verification_history
        route to Planning
    else:
        write unverified claims to state
        route to Report Generation

Outputs:
    VerificationReport (structured: failed_claims, contradictions, missing_evidence, suggestions)
    verification_history (appended)
    unverified_claims (populated on budget exhaustion)
    budget_remaining (decremented)
```

---

## Graph Design

Prefer:

* explicit nodes
* explicit conditional routing
* visible loops

Avoid:

* hidden recursion
* implicit autonomous loops
* opaque framework abstractions

---

## REST API Routing Conventions

Routes with path parameters (e.g. `/tools/{name}`) will match any string in that segment, including words that look like sub-resources. This causes ambiguous routing when fixed-path endpoints share the same prefix (e.g. `PATCH /tools/bulk` vs `PATCH /tools/{name}`).

**Rule:** Endpoints that operate on collections, perform bulk operations, or are administrative in nature must use a distinct prefix that cannot collide with identifier-based routes.

| Pattern | Use for | Example |
|---|---|---|
| `/tools/{name}` | Single-resource CRUD | `PATCH /tools/my_tool` |
| `/tool-admin/bulk` | Bulk/multi-resource operations | `PATCH /tool-admin/bulk` |
| `/tool-admin/groups/{name}` | Group-level operations | `DELETE /tool-admin/groups/weather_api` |

This avoids relying on route registration order, which is fragile across frameworks and refactors.

---

## Step-Cost Budget

### Purpose

The step-cost budget limits the total computational effort a research workflow can expend. It acts as a proxy for inference cost, tool usage cost, and overall resource consumption.

### Mechanism

Each graph node declares a **cost weight** — a relative cost value that is deducted from `budget_remaining` before the node executes.

When `budget_remaining` falls below a node's cost weight, that node cannot execute and the graph must terminate.

Budget is initialized per research conversation from a configurable `budget_limit`.

### Default Cost Weights

| Node | Cost Weight | Rationale |
|---|---|---|
| Planning | 2 | Intelligence model, moderate prompt |
| Tool Discovery | 1 | LanceDB embedding search, no model |
| Tool Selection | 2 | Intelligence model, chooses from top-K |
| Research Execution | 5 | Intelligence model + multiple tool calls |
| Compression | 1 | Task model, cheap |
| Draft Synthesis | 3 | Intelligence model, heavy reasoning |
| Verification | 4 | Intelligence model + tool calls |
| Report Generation | 3 | Intelligence model, structured synthesis (budget-exempt) |

The full retry cycle cost is the sum of Planning through Verification: 2 + 1 + 2 + 5 + 1 + 3 + 4 = 18.

Report Generation is budget-exempt. Its cost weight (3) is tracked for accounting and visibility but never prevents execution. Report Generation always runs as the terminal node.

### Configurability

Cost weights and the default `budget_limit` must be configurable. For the minimum viable prototype, configuration should be stored in a source file (e.g., YAML or Python config). Future versions may support per-user or per-conversation budget overrides from the UI.

### Budget and Verification Interaction

The budget governs the verification retry loop. See the Verification Retry Loop section in Research and Verification Strategy for the full retry mechanism.

The retry loop checks `budget_remaining` against the full cycle cost (18 with default weights) — not just the cost of the Planning node alone.

Report Generation is budget-exempt. It runs on every terminal path (verification pass, budget exhaustion, and node failure) regardless of remaining budget. Its cost is tracked for accounting but never gates execution.

### Budget Visibility

The budget is part of the workflow state and must be visible to the user. The UI should present:

* current budget remaining
* budget consumed per node execution
* total budget consumed per research conversation

This supports the system's transparency and inspectability principles.

---

# Model Architecture

## Model Strategy

### Intelligence Model: larger model for intelligent reasoning

Typical size:

* 25B–100B parameters

Responsibilities:

* planning
* tool selection
* reasoning
* synthesis
* verification
* report generation

### Task Model: smaller model for tasks

Typical size:

* 2B-4B parameters

Responsibilities:

* summarization
* compression
* chunk reduction
* formatting
* lightweight transforms

The task model should never be used to:

* perform verification
* choose tools
* perform critical reasoning
* evaluate factual correctness

---

# Inference Infrastructure

## Inference Backend

Inference should be configurable, with a list of configured
models available from a list of endpoints.

Primary inference backends supported:

* OpenAI compatible completions API
* OpenAI compatible responses API

Any backend exposing an OpenAI-compatible API is supported. Reference implementations include llama.cpp (local inference) and vLLM (high-throughput serving), but these are not required.

Available models should be read from the API and assigned to purpose (intelligence or task) by the user.


## Model Routing

LangGraph should not directly manage model loading.

LangGraph should instead target stable routed endpoints such as:

```text
model="intelligence"
model="task"
```

where the user has selected the intelligence and task models.

## Embedding Model

Semantic tool discovery requires an embedding model to produce vectors from tool descriptions and queries.

### MVP: Local Embedding Model

Use a small local embedding model via `sentence-transformers` (e.g. `all-MiniLM-L6-v2`, ~80MB). Runs on CPU, no GPU required. This keeps the system fully self-contained.

### Scale

The embedding workload is small. The tool catalog is dozens to low hundreds of entries. Embedding happens at:

* **Ingestion time** — when tools are loaded from config or auto-ingested
* **Query time** — once per workflow run, when the current step is embedded for similarity search

### Separation of Concerns

Embedding is a separate concern from vector storage. The embedding model produces vectors; LanceDB stores and queries them. The embedding interface should be swappable behind an abstraction layer:

```text
Application Code
        ↓
Embedding Interface (moira.embeddings.interfaces)
        ↓
Embedding Implementation (sentence-transformers, OpenAI-compatible endpoint, etc.)
```

Future implementations may use an OpenAI-compatible embedding endpoint (local or remote) for larger-scale deployments.

---

# Tool Architecture

## Supported Tool Types

The system should support:

* REST APIs
* OpenAI-compatible tools
* MCP tools
* retrieval systems
* local databases
* search indexes

---

## MCP Integration

MCP tools should be wrapped into a unified internal tool interface.

Example pattern:

```python
@tool
def mcp_search(query: str) -> str:
    return mcp_client.call("search", {"query": query})
```

The workflow engine should not distinguish between:

* REST tools
* MCP tools
* internal tools

All should expose:

* structured schemas
* typed arguments
* deterministic execution

---

## Tool Catalog Provisioning

Tools must be registered in the system before they can be discovered and used by the workflow.

### MVP: Configuration File

For the minimum viable prototype, tools are defined in a YAML or Python configuration file. Each entry specifies:

* name
* description (optimized for semantic retrieval)
* argument schema (JSON Schema format)
* endpoint URL and HTTP method (for REST tools)
* MCP server address (for MCP tools)
* tags/domains
* example use cases
* reliability notes

The configuration is parsed at startup. Tool metadata is stored in SQLite and tool descriptions are embedded and indexed in LanceDB.

### Future: Auto-Ingestion

The system should eventually support creating tools from external sources with minimal user effort:

* **OpenAPI endpoints** — user provides a URL and auth info; the system fetches the OpenAPI spec and generates tool entries for each operation
* **MCP servers** — user provides a server address; the system queries the server's tool list and generates typed, schema-bearing entries
* **UI form** — user adds tools through the web interface

The tool provisioning interface should be designed to support these ingestion methods without restructuring the catalog.

---

# Semantic Tool Discovery

## Problem

Large tool catalogs degrade:

* context quality
* tool selection accuracy
* reasoning reliability

---

## Required Solution

Implement semantic tool retrieval using LanceDB.

Tool descriptions are embedded and indexed in LanceDB. At workflow execution time, the current step or question is embedded and used to query LanceDB for the top-K most relevant tools.

Workflow:

```text
Question / Current Step
        ↓
Embedding Search over Tool Catalog
        ↓
Top-K Relevant Tools
        ↓
Model Tool Selection
```

The model should never receive the full tool catalog.

---

## Tool Metadata Requirements

Every tool should include:

* name
* description
* argument schema
* example use cases
* tags/domains
* reliability notes

Tool descriptions should be optimized for:

* semantic retrieval
* model comprehension

---

# Research and Verification Strategy

## Research Phase

Goals:

* gather evidence
* retrieve sources
* collect structured information
* identify competing claims

Research nodes should:

* aggressively use tools
* avoid premature synthesis


---

## Compression Phase

Large tool outputs should be compressed before entering long-context reasoning.

Use the task model to:

* summarize
* normalize
* deduplicate
* extract key facts

This prevents:

* context exhaustion
* degraded reasoning quality

---

## Verification Phase

Verification should:

* re-check claims using tools
* compare against sources
* identify unsupported conclusions
* identify contradictions

Verification should be adversarial.

The verifier should assume:

> the draft may contain hallucinations or unsupported synthesis.

### Verification Retry Loop

When verification identifies unsupported claims, contradictions, or missing citations, the system does not immediately return a failed result. Instead:

1. The verification node produces a structured `VerificationReport` (failed claims, contradictions, missing evidence, suggestions) and appends it to `verification_history` in the workflow state.
2. The graph checks `budget_remaining` against the cost of a full retry cycle (Planning + Tool Discovery + Tool Selection + Research Execution + Compression + Draft Synthesis + Verification = 18 with default weights).
3. If `budget_remaining >= 18`, the graph routes back to the Planning node. The next planning pass receives the accumulated `verification_history`, allowing it to adjust the research plan to address specific failures.
4. If `budget_remaining < 18`, the verification node writes unverified claims to `unverified_claims` in state and routes to Report Generation. Report Generation is budget-exempt and always executes.

Both the pass and budget-insufficient paths converge on Report Generation. The graph always terminates through this node. Report Generation also serves as the terminal node for node-level failures (see Node-Level Failure Handling).

The verification retry loop is bounded exclusively by the step-cost budget. The length of `verification_history` serves as the de facto attempt counter.

---

## Report Generation Phase

Report Generation is the terminal node of every research workflow. It is budget-exempt — it always executes regardless of `budget_remaining`. Its cost weight is tracked for accounting and visibility but never prevents execution.

Three paths converge on Report Generation:

1. **Verification pass** — the draft was fully verified
2. **Budget insufficient** — verification failed and budget cannot support another retry cycle
3. **Node failure** — an error occurred during execution that could not be recovered (see Node-Level Failure Handling)

### Purpose

Produce a structured research report for the user that presents the answer, its supporting evidence, and an honest assessment of the result's quality.

### Model

Intelligence model. Report generation requires reasoning about what to include, assessing the severity of critiques, and synthesizing citations — the task model must not make these judgments.

### Report Structure

The output is a `ResearchReport` object written to state:

* **answer** — the final answer to the user's question, synthesized from the verified draft
* **citations** — sources referenced in the answer, as structured records (source, url or reference, excerpt, relevance)
* **support** — corroborating evidence gathered during research that substantiates the answer, as structured records
* **critiques** — weaknesses, limitations, or objections discovered during verification and research. Includes any unsupported claims folded into critiques when the verification-pass path is taken
* **unverified_claims** — populated when the report is generated after budget exhaustion or node failure. Lists specific claims that could not be verified, with an explanation of what evidence was missing
* **budget_consumed** — total budget consumed across all attempts in this research conversation

The number of verification attempts is computed at report generation time from `len(verification_history)` and is not stored as a separate field.

### Behavior by Path

On the **verification pass** path:
* `unverified_claims` is empty
* `critiques` contains any weaknesses or limitations noted during research and verification
* The answer is presented as verified

On the **budget insufficient** path:
* `unverified_claims` lists claims that could not be verified, with what evidence was needed
* `critiques` includes both research limitations and the consequences of incomplete verification
* The answer is presented with explicit caveats about unverified material

On the **node failure** path:
* `unverified_claims` lists any claims that were in progress when the failure occurred
* `critiques` includes a description of the failure and what work was interrupted
* The answer is presented as incomplete with a description of what went wrong

---

# UI / UX Guidelines

## User separated workspace environments

The UI should present each user with their own distinct workspace environment.
Each research task with questions, agent state, agent action history, and conclusions should be persisted in the database and reviewed whenever the user needs. 
Research should be organizable into user-named projects, with shared conclusion and other context between tasks in a project.

## Conversational Interface

The UI should remain chat-oriented.

However, the UI should expose:

* workflow stages
* tool calls
* intermediate outputs
* verification steps
* citations and evidence

Users should be able to inspect:

* why a conclusion was reached
* what tools were used
* what evidence was gathered
* current step-cost budget remaining
* budget consumed per node and per conversation
* verification history and retry attempts

---

## Streaming Execution Visibility

The UI should stream intermediate execution state such as:

```text
[Planning]
[Discovering Tools]
[Selecting Tools]
[Researching]
[Summarizing]
[Drafting]
[Verifying]
[Verifying — issues found, retrying (budget remaining: 12)]
[Generating Report]
```

This improves:

* transparency
* trust
* debuggability

## Visual theming

The UI should natively support themes and day/night mode. 

Themes can adjust any of the following, primarily through CSS:

* NaiveUI theme overrides
* Fonts for variable and fixed width body text, as well as headings
* Image placement in designated areas of the UI (like the title bar or footer)


The user can select a theme for each of light and dark mode.
The app should ship with default light and dark themes, and eventually have the ability to add third party or custom themes.

### Theme Packaging Format

A theme is a ZIP archive containing:

* `manifest.json` — theme metadata (name, author, version), references to all other assets, NaiveUI theme token overrides
* CSS files — style overrides for layout, typography, and custom components
* Font files or font URL references — for variable-width, fixed-width, and heading fonts
* Image assets — for designated areas such as title bar or footer

The app ships with default light and dark themes in this format. Third-party themes are distributed as ZIP archives following the same structure.

---

# Reliability Principles

## Avoid Pure Autonomous Agent Behavior

The system should favor:

* constrained workflows
* bounded execution
* explicit graph structure

over:

* unconstrained recursive agents
* open-ended self-directed loops

---

## Prefer Deterministic Scaffolding

The workflow should guide the model.

The model should not:

* invent workflow structure
* invent execution order
* self-manage orchestration

---

## Tool Failures Must Be First-Class

Tool execution should support:

* retries
* timeout handling
* structured error propagation
* fallback behavior

---

## Node-Level Failure Handling

Tool failures are covered by retries and timeouts, but nodes can also fail in other ways: model timeout, malformed structured output, transient API exceptions, or unexpected errors.

### Per-Node Error Handling

Every graph node should define:

* a LangGraph `RetryPolicy` for transient failures (model timeout, network errors)
* an `error_handler` that catches unrecoverable failures and routes to Report Generation with an error flag

### Error Handler Behavior

When a node fails and retries are exhausted, the error handler:

1. Writes a description of the failure to state
2. Routes to Report Generation (budget-exempt, always executes)
3. Report Generation produces a report with the failure described in `critiques` and any incomplete work flagged in `unverified_claims`

### Checkpoint-Based Recovery

LangGraph checkpoints capture state after each successful node. If a run fails mid-execution, the system can resume from the last checkpoint rather than restarting the entire workflow. This is especially important for long research runs with large tool calls.

The checkpoint-based recovery path is separate from the error handler — it is for resumable failures (e.g., the user restarts the service). The error handler is for in-run failures that cannot be retried.

---

# Observability and Debugging

The system should log:

* graph execution traces
* node transitions
* tool selections
* tool outputs
* model prompts
* verification failures
* verification retry attempts and history
* budget consumption per node
* report generation outputs
* node-level errors and error handler invocations

Execution history should be inspectable per conversation.

---

# Long-Term Goals

Potential future capabilities:

* multi-agent collaboration
* hypothesis branching
* citation graphs
* persistent memory
* domain-specialized research modes
* autonomous long-running research tasks
* user-interruptible workflows
* human approval checkpoints

---

# Recommended Initial Scope

## Phase 1

Build:

* LangGraph workflow runtime
* basic research loop
* persistence API layer (repository interfaces and SQLite/LanceDB implementations)
* REST tool integration
* MCP wrapper support
* semantic tool retrieval (LanceDB vector indexing of tool metadata)
* simple conversational UI

## Phase 2

Add:

* execution tracing
* streaming node visibility
* citation management
* conversation persistence
* structured report rendering in UI

## Phase 3

Add:

* advanced planning
* parallel research branches
* multi-agent workflows
* long-running tasks
* persistent knowledge graph
