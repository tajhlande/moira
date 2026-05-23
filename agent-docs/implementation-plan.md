# MOiRA Phased Implementation Plan

Each phase is a vertical spike: it touches the backend, frontend, persistence, and workflow layers just enough to produce a runnable, demonstrable increment. Later phases deepen existing components rather than introduce new categories.

---

## Phase 1: Skeleton — Bootable Full-Stack App

**Goal**: Establish the monorepo layout, get a bootable backend and frontend that talk to each other, wired to a real model endpoint. You can open the UI, type a message, and get a response from the LLM. Nothing that doesn't serve this goal ships in Phase 1.

### Monorepo layout

```
moira/
  package.json              # root workspace scripts (dev, build, test, lint)
  Makefile                  # make dev, make test, make lint
  config/
    moira-config-template.yaml   # full config schema template — copy to moira-config.yaml and edit

  backend/
    pyproject.toml
    moira/
      __init__.py
      config.py               # Pydantic model, validates full config schema at startup
      main.py                 # FastAPI app factory — calls init_services/shutdown_services on lifespan
      service_setup.py        # service registry: init_services, shutdown_services, service_provider
      models/
        __init__.py
        state.py              # all TypedDicts (defined up front, consumed by later phases)
      persistence/
        __init__.py
        interfaces.py         # abstract repository classes (interfaces only; LanceDB, tools repos deferred)
        sqlite/
          __init__.py
          repos.py            # SessionRepository, ModelPreferencesRepository only
          schema.py           # versioned migration runner + initial schema patch
      inference/
        __init__.py
        client.py             # OpenAI-compatible inference client
        registry.py           # model registry (logical name → endpoint)
      api/
        __init__.py
        router.py             # minimal API routes
    tests/

  frontend/
    package.json
    vite.config.ts
    tsconfig.json
    src/
      main.ts
      App.vue
      api/                    # hand-written API client
        client.ts
      components/
      views/
      stores/                 # Pinia state management
      themes/                 # default light + dark themes (directory layout = unzipped ZIP format)
    tests/
```

Packages deferred to Phase 2: `moira/embeddings/`, `moira/tools/`, `moira/workflow/`, `moira/observability/`, `moira/persistence/lancedb/`.

### Backend

1. **Project config**
   - `pyproject.toml`: Python 3.13, FastAPI, LangGraph, httpx, pytest, ruff (LanceDB, sentence-transformers deferred to Phase 2)
   - `config/moira-config-template.yaml`: complete config template reflecting all known phases — budget limits, cost weights, database paths, model endpoint URLs, tool definition schema (unused in Phase 1 but validated structurally), MCP server addresses (unused), embedding model config (unused)
   - Pydantic model in `moira.config` validates the full config schema at startup. Rejects typos, missing required fields, and invalid types. Sections not yet consumed are validated structurally but ignored functionally.
   - **Config template will mutate across phases.** The template defines the best-known schema as of Phase 1, but Phase 2 (tool definitions, embeddings) and later phases may reveal schema adjustments. Pre-1.0, edited configs may need re-merging against the updated template. The template header should note this.
   - `MOIRA_CONFIG_FILE` environment variable points to the active config file
   - `make dev` checks for `./config/moira-config.yaml`; if missing, prints "Copy `config/moira-config-template.yaml` to `config/moira-config.yaml`, edit it, and re-run" and exits. Application startup produces the same message if the file is missing.
   - Cost weight table defined in config: Planning=2, ToolDiscovery=1, ToolSelection=2, ResearchExecution=5, Compression=1, DraftSynthesis=3, Verification=4, ReportGeneration=3 (budget-exempt)

2. **Service dependency injection** (`moira/service_setup.py`)
   - `init_services(config)` — instantiate Phase 1 services (session repository, model preferences repository, inference client, model registry) and store in an internal dict keyed by name
   - `shutdown_services()` — tear down connections, release resources
   - `service_provider(name: str) -> Any` — retrieve a service by registered name; raises if not initialized
   - Called from FastAPI app lifespan: `init_services` on startup, `shutdown_services` on shutdown
   - API routes receive services via `Depends(lambda: service_provider("service_name"))`
   - Tests call `init_services()` directly with mock service instances, bypassing the real implementations

3. **State types** (`moira/models/state.py`)
   - `VerificationReport(TypedDict)`: failed_claims, contradictions, missing_evidence, suggestions
   - `Finding(TypedDict)`: content, source, citation_url (NotRequired), type
   - `ResearchReport(TypedDict)`: answer, citations, support, critiques, unverified_claims, budget_consumed
   - `ResearchState(TypedDict)`: question, plan, active_tools (model-selected subset), findings, compressed_findings, draft, verification, report, budget_remaining, budget_limit, verification_history: list[VerificationReport], unverified_claims
   - Defined up front so later phases depend on stable types, not the other way around

4. **Repository interfaces** (`moira/persistence/interfaces.py`)
   - `SessionRepository`: create session, insert message, fetch transcript (minimal — just enough for Phase 1 chat)
   - `ModelPreferencesRepository`: get/set intelligence and task model assignments for a user
   - Other interfaces (`ProjectRepository`, `ToolMetadataRepository`, `ToolEmbeddingRepository`, `CheckpointRepository`) deferred to the phases that consume them

5. **SQLite implementations**
   - Versioned migration runner in `moira/persistence/sqlite/schema.py`: `schema_version` meta table, numbered SQL patch files applied in order (e.g. `001_initial.sql`)
   - Initial schema (`001_initial.sql`): users, sessions, messages, model_preferences tables
   - `SessionRepository` implementation: create session, insert message, fetch transcript
   - `ModelPreferencesRepository` implementation: get/set per-user model assignments

6. **Default user**
   - Server-side constant `DEFAULT_USER_ID`, attached to all writes by the API layer
   - No client header or auth for single-user MVP
   - Default user row inserted by `001_initial.sql` migration

7. **Inference client** (`moira/inference/`)
   - Async httpx client for OpenAI-compatible completions/responses APIs
   - Model registry: reads available models from configured endpoints, maps "intelligence"/"task" to concrete models
   - Model assignments persisted in `model_preferences` table via `ModelPreferencesRepository`

8. **Minimal API routes** (`moira/api/router.py`)
   - `GET /api/health`
   - `POST /api/sessions` — create session
   - `GET /api/sessions/{id}` — get session with message history
   - `POST /api/sessions/{id}/messages` — send message, get a raw model response (no workflow yet)
   - `GET /api/models` — list available models from endpoint + current assignments
   - `PUT /api/models` — assign intelligence/task models (writes to `model_preferences`)

9. **Test infrastructure**
   - pytest with in-memory SQLite fixtures
   - Repository tests against SQLite implementations
   - Mock-server inference client tests
   - API integration tests via httpx AsyncClient with services replaced via `init_services()` with mocks

### Frontend

1. **Project scaffolding**
   - Vite + TypeScript + Vue 3
   - NaiveUI component library
   - API client module for backend endpoints

2. **Layout shell**
   - Sidebar: session list (no projects yet)
   - Main area: chat interface
   - Right panel: collapsible, placeholder for workflow state

3. **Chat interface (minimal)**
   - Message input and rendering
   - Calls `POST /api/sessions/{id}/messages`, displays response
   - Session creation wired to sidebar

4. **Theme scaffolding**
   - Default light and dark themes
   - Theme toggle in UI
   - Theme directory structure matches the unzipped ZIP format from Phase 6: `manifest.json` + CSS overrides + font references + image assets. Phase 6 adds ZIP packaging and upload only — no format migration.

5. **Tests**
   - Vitest component tests
   - Smoke test: render chat, send message

### Exit criteria
- `make dev` boots backend + frontend concurrently; prints helpful message if config is missing
- User can create a session, type a message, see an LLM response
- Repository tests pass for SQLite
- Unit and integration tests pass with mock endpoint (CI gate)
- Manual smoke test passes against a configured real endpoint (developer checklist, not CI gate)
- `make test` and `make lint` pass cleanly

---

## Phase 2: Workflow — LangGraph Research Loop

**Goal**: Replace the raw model pass-through with the full LangGraph research workflow. The UI shows workflow stages streaming in real time, with semantic tool discovery and a simple tool or two available. Prior ResearchReport carries forward as context for follow-up messages.

### Backend

1. **Tool catalog** (`moira/tools/`)
   - YAML config format for declaring tools with fields matching architecture spec: name, description (optimized for semantic retrieval), argument schema (JSON Schema), endpoint URL and HTTP method (REST tools), MCP server address (MCP tools), tags/domains, example use cases, reliability notes
   - Parser loads tool definitions at startup
   - Ingestion: metadata → SQLite, descriptions → embeddings → LanceDB
   - `BaseTool` abstract class with `RESTTool` implementation
   - Semantic discovery: embed query → LanceDB top-K search
   - **Tool categories**: catalog tools (registered in catalog, embedded, subject to semantic discovery, model-selected) and system tools (always present, bypass discovery, framework-injected). `conversation_history` will be the first system tool (implemented in Phase 4). At execution time, the node receives `active_tools` (model-selected catalog tools) merged with system tools. System tools are not present in `active_tools` state — they are injected by the graph, not chosen by the model.

2. **Embedding client** (`moira/embeddings/`)
   - Abstract interface: `embed(text) -> list[float]`, `embed_batch(texts) -> list[list[float]]`
   - Local implementation via sentence-transformers (all-MiniLM-L6-v2), runs on CPU
   - Swappable: local model or OpenAI-compatible embedding endpoint behind the same interface

3. **LanceDB** (`moira/persistence/lancedb/`)
   - `ToolEmbeddingRepository`: tool_embeddings table with vector + metadata columns
   - Implementation of semantic similarity search

4. **Tool executor** (`moira/tools/executor.py`)
   - Async execution with timeout
   - Basic retry for transient failures
   - Structured error results

5. **LangGraph graph** (`moira/workflow/graph.py`)
   - All 8 nodes: Planning, ToolDiscovery, ToolSelection, ResearchExecution, Compression, DraftSynthesis, Verification, ReportGeneration
   - All nodes are async functions. Graph uses LangGraph's async APIs throughout (required for SSE streaming).
   - Conditional routing after Verification: Pass / Fail + Budget Sufficient / Fail + Budget Insufficient
   - ReportGeneration always runs (budget-exempt, cost tracked for accounting only)
   - Full cycle cost = 18 for retry routing decision

6. **Node implementations** (`moira/workflow/nodes/`)
   - Each node: purpose, model, cost weight, input requirements, outputs, failure conditions
   - Nodes are functional but use simple prompts — sophistication comes in later phases
   - Verification produces `VerificationReport`, appends to `verification_history`
   - Report generates structured `ResearchReport`

7. **Budget enforcement** (`moira/workflow/budget.py`)
   - Deduct cost weight before each node
   - Block execution when budget < cost weight (except ReportGeneration)

8. **LangGraph checkpoints**
   - SQLite checkpoint saver for workflow state
   - LangGraph's `SqliteSaver` is an allowed exception to the persistence boundary — it is a framework-internal concern owned by LangGraph, not wrapped behind the repository interface
   - Enables resume from last successful node after service restart
   - `CheckpointRepository` (deferred to Phase 4 with observability) will be scoped to *reading* checkpoints for inspection, not writing them

9. **Streaming** (`moira/api/streaming.py`)
   - SSE endpoint: `POST /api/sessions/{id}/messages` starts a graph run and streams progress
   - Event types and payloads (architecture-level contract between backend and frontend):

   | Event | Payload |
   |---|---|
   | `node_start` | `{ node: str, timestamp: str }` |
   | `node_end` | `{ node: str, timestamp: str, budget_remaining: float }` |
   | `tool_call` | `{ tool: str, args: dict }` |
   | `tool_result` | `{ tool: str, output: str, duration_ms: int }` |
   | `budget_update` | `{ budget_remaining: float, budget_consumed: float, node: str }` |
   | `verification_report` | `{ report: VerificationReport, attempt: int }` |
   | `run_complete` | `{ report: ResearchReport }` |
    | `run_error` | `{ node: str, error: str, session_id: str }` |

10. **Node failure handling** (`moira/workflow/error_handler.py`)
    - Every node has a LangGraph `RetryPolicy` for transient failures (model timeout, network errors)
    - Every node has an `error_handler` that catches unrecoverable failures: writes failure description to state, routes to ReportGeneration with error flag
    - Checkpoint-based recovery is a separate path: LangGraph checkpoints capture state after each successful node, enabling resumption from the last checkpoint after a service restart (distinct from in-run error handling)
    - ReportGeneration handles three terminal conditions: verification pass, budget insufficient, node failure — each with distinct report behavior per the architecture
    - ReportGeneration must be robust to any missing prefix of state fields (e.g. `active_tools` empty if failure at Tool Selection, `findings` empty if failure at Research Execution)

11. **Conversation context**
    - Each user message starts a fresh graph run
    - If a prior ResearchReport exists for the session, it is included as context for the new run
    - Phase 2 sessions are multi-turn: follow-up messages see the prior report
    - Full transcript access via `conversation_history` tool deferred to Phase 4

### Frontend

1. **Streaming stage indicators**
   - Display current workflow stage: Planning → Discovering Tools → Selecting Tools → Researching → Summarizing → Drafting → Verifying → Generating Report
   - Budget remaining display

2. **Report rendering (basic)**
   - Render ResearchReport: answer text, citations list, critiques, unverified claims
   - Verification status display

3. **Streaming tests (frontend)**
   - Mocked SSE source feeding streaming UI components
   - Verify stage transitions, budget updates, and report rendering against known event sequences

### Tests
- Unit tests for each node (mocked model)
- Integration test: full graph run with mock inference
- Budget exhaustion test: retry loop stops, ReportGeneration still runs
- Verification pass/fail tests
- SSE streaming tests (backend)
- Partial-state ReportGeneration tests: one test per node boundary, each simulating a failure at that point and verifying ReportGeneration produces a valid report with incomplete state
- Discovery fixture tests: set of question → expected-tool pairs, verify top-1 match rate

### Exit criteria
- User sends a question, sees workflow stages stream through the UI
- Follow-up messages carry forward the prior ResearchReport as context
- Research loop completes with a structured report
- Budget enforcement works; ReportGeneration always terminates the graph
- Verification retry loop accumulates history and re-plans
- All tests pass, lint clean

---

## Phase 3: Tools and Discovery — REST, MCP, and Semantic Search

**Goal**: Full tool lifecycle — configure tools, discover them semantically, execute them reliably, see tool calls in the UI.

### Backend

1. **Tool catalog hardening**
   - Idempotent re-ingestion on startup (upsert semantics)
   - Tool validation at startup (endpoint reachable, schema valid)
   - `GET /api/tools` endpoint listing registered tools

2. **MCP client integration** (`moira/tools/mcp_client.py`)
   - Connect to configured MCP servers at startup
   - Discover available tools from MCP servers dynamically
   - Wrap each MCP tool into `BaseTool` interface
   - MCP tools ingested into catalog alongside REST tools

3. **Tool executor hardening**
   - Configurable retry policy (max retries, exponential backoff)
   - Per-tool timeout
   - Structured error capture: timeout, connection error, malformed response, auth failure
   - Errors returned as structured strings for model consumption

4. **Semantic discovery refinement**
   - Top-K tuning, relevance threshold
   - Discovery logged and traceable

### Frontend

1. **Tool call visibility**
   - Expandable tool call entries in the right panel
   - Show tool name, arguments, output, duration
   - Highlight which tools were semantically selected vs. available

2. **Model configuration UI**
   - Display available models from backend
   - Assign intelligence and task models
   - Show current assignments

3. **Tool management (read-only)**
   - List registered tools
   - Show tool metadata, schema, domains

### Tests
- Tool ingestion and discovery tests with realistic tool sets
- Discovery fixture tests: set of question → expected-tool pairs, verify top-1 match rate threshold
- MCP wrapper tests with mock MCP server
- Tool executor failure/retry tests
- Node failure → ReportGeneration integration test

### Exit criteria
- REST and MCP tools configure, ingest, and discover correctly
- Semantic discovery returns relevant tools for domain-specific queries
- Tool calls are visible in the UI with inputs and outputs
- Failures produce structured errors, not crashes
- All tests pass, lint clean

---

## Phase 4: Sessions, Projects, and Observability

**Goal**: Real session and project management, full execution tracing, on-demand transcript access. The app feels like a persistent workspace.

### Backend

 1. **Conversation semantics — `conversation_history` tool**
    - `conversation_history` is a system tool: always available, bypasses semantic discovery, always injected into Research Execution by the graph regardless of what Tool Selection produces
    - Retrieves prior messages from the session transcript on demand
    - Prior ResearchReport context (added in Phase 2) remains unchanged
    - This completes the conversation architecture: prior report always in context, transcript available on demand

 2. **Session and project persistence**
    - Phase 1 session storage (insert message, fetch transcript) expanded to: full CRUD with project scoping, multi-session navigation, message history with workflow state
    - `ProjectRepository`: create, get, list, update, delete
    - `SessionRepository`: expanded with project scoping, state snapshots, report history
    - `CheckpointRepository`: scoped to *reading* checkpoints for observability (LangGraph owns writing)
    - Sessions belong to projects, projects belong to users
    - `GET/POST /api/projects`, `GET /api/sessions` scoped to project

3. **Execution tracing** (`moira/observability/tracing.py`)
   - Structured logging: node transitions, tool selections, tool outputs, model prompts, verification failures, budget consumption
   - Traces persisted to SQLite alongside session data
   - `GET /api/sessions/{id}/trace` — full execution trace
   - `GET /api/sessions/{id}/trace/{node}` — per-node trace

4. **Metrics**
   - Budget consumed per node execution
   - Total budget per session
   - Tool call counts and latencies
   - Verification attempt counts

5. **Auth (single-user MVP)**
   - No authentication required
   - User ID field on sessions and projects for future multi-user
   - Default user created at startup

### Frontend

1. **Session/project management**
   - Create, list, switch projects in sidebar
   - Create, list, switch sessions within a project
   - Session history with persistent state
   - Resume a previous session and continue the conversation

2. **Workflow inspection**
   - Expandable node history per session in the right panel
   - Tool call details with inputs and outputs
   - Verification history with retry attempts
   - Budget display: remaining, consumed per node, total consumed

3. **Trace viewer**
   - Per-session execution trace
   - Per-node drill-down

### Tests
- Session lifecycle tests (create, message, resume, message again)
- Project scoping tests
- Trace completeness tests
- Conversation context tests (prior report carried forward)

### Exit criteria
- User can create projects, organize sessions, resume past conversations
- Every workflow run produces a queryable trace
- Conversation turns carry forward prior report context
- All tests pass, lint clean

---

## Phase 5: Verification, Citations, and Rich Reports

**Goal**: Adversarial verification with structured retry, rich citation handling, polished report rendering with export.

### Backend

1. **Verification hardening**
   - Adversarial prompts tuned for the verifier role
   - Structured `VerificationReport` with actionable suggestions for re-planning
   - Verification history feeds Planning node to avoid repeating failures

2. **Citation management** (`moira/citations/`)
   - Source deduplication across findings
   - Citation normalization (URLs, DOIs, titles)
   - Citation-graph construction for multi-source claims

3. **Enhanced report generation**
   - Inline citation markers in answer text
   - Evidence linking: each claim maps to supporting findings
   - Confidence indicators per claim
   - `verification_attempts` computed from `len(verification_history)` (not stored)
    - Three terminal conditions handled with distinct report behavior:
      - **Verification pass**: `unverified_claims` empty, `critiques` contains weaknesses/limitations, answer presented as verified
      - **Budget insufficient**: `unverified_claims` lists unverified claims with missing evidence, `critiques` includes consequences of incomplete verification, answer presented with caveats
      - **Node failure**: `unverified_claims` lists in-progress claims, `critiques` describes the failure and interrupted work, answer presented as incomplete

### Frontend

1. **Rich report rendering**
   - Formatted report view with clickable inline citations
   - Evidence panel showing source excerpts
   - Verification summary with pass/fail/retry details
   - Unverified claims highlighted with caveats

2. **Export**
   - Markdown export
   - PDF export

### Tests
- Verification retry tests with accumulated history improving re-planning
- Citation deduplication and normalization tests
- Report structure validation tests
- Export output tests

### Exit criteria
- Verification produces structured, actionable retry feedback
- Reports have clickable inline citations linking to evidence
- Export works for Markdown and PDF
- All tests pass, lint clean

---

## Phase 6: Theming and Polish

**Goal**: Full theme system, UI polish, accessibility, production readiness.

### Backend

1. **Theme API**
   - `GET /api/themes` — list installed themes
   - `POST /api/themes` — upload theme package (ZIP)
   - `GET /api/themes/{name}` — get theme manifest
   - `DELETE /api/themes/{name}` — remove theme

### Frontend

1. **Theme system**
   - Theme = ZIP archive: `manifest.json` (name, author, version, references to CSS/fonts/images, NaiveUI token overrides) + CSS files + font references + image assets
   - Default light and dark themes shipped as theme packages
   - Theme selection UI: pick one for light mode, one for dark mode
   - Auto day/night switching
   - Third-party theme installation via upload

2. **UI polish**
   - Responsive layout
   - Accessibility pass (keyboard navigation, ARIA labels, contrast)
   - Error states and loading states
   - Empty states for new users

3. **Settings UI**
   - Model configuration
   - Budget limit configuration
   - Theme selection

### Tests
- Theme loading and switching tests
- Accessibility audit (automated)
- Settings round-trip tests

### Exit criteria
- Themes are installable, switchable, and correctly override NaiveUI tokens, fonts, and images
- UI is responsive and accessible
- All tests pass, lint clean

---

## Phase 7: Advanced Capabilities

**Goal**: Extend the platform in whichever direction matters most.

> **Phase 7 is a menu, not a sequence.** Each capability below is largely independent. Pick whichever direction is most valuable when you reach this phase. Each could be its own phase of work.

### Backend

1. **Parallel research branches**
   - LangGraph subgraph for parallel tool execution
   - Fan-out/fan-in: Planning decomposes into sub-questions, each runs independently, results merge at compression

2. **Advanced planning**
   - Strategy selection: adapt workflow based on question type (factual, analytical, comparative)
   - Decomposition of complex questions

3. **Long-running tasks**
   - Background execution with notification on completion
   - User-interruptible workflows

4. **Human approval checkpoints**
   - Configurable nodes that pause for human review
   - API endpoints for approve/reject actions

5. **Persistent knowledge graph**
   - Accumulate verified findings across sessions
   - Cross-session citation and fact reuse

6. **Multi-agent workflows**
   - Specialized agent roles (researcher, verifier, synthesizer)
   - Inter-agent communication via shared state

### Frontend

1. **Parallel branch visualization**
   - Show concurrent research branches
   - Merge results view

2. **Human checkpoint UI**
   - Approval/rejection actions
   - Review intermediate state before continuing

3. **Long-running task management**
   - Background task list
   - Progress indicators
   - Interrupt/stop actions

4. **Knowledge graph browser**
   - Cross-session findings explorer
   - Citation graph visualization

### Tests
- Parallel branch execution and merge tests
- Human checkpoint pause/resume tests
- Long-running task interrupt tests
- Knowledge graph accumulation tests

### Exit criteria
- Complex questions decompose into parallel research branches
- Long-running tasks run in background with progress visibility
- Human checkpoints pause and resume correctly
- All tests pass, lint clean

---

## Dependency Summary

```
Phase 1 (skeleton — all layers bootable)
  └── Phase 2 (workflow + streaming + basic tools)
       └── Phase 3 (full tool lifecycle: REST, MCP, discovery)
            └── Phase 4 (sessions, projects, tracing, conversation)
                 └── Phase 5 (verification, citations, rich reports)
                      └── Phase 6 (theming, polish)
                           └── Phase 7 (advanced: parallel, multi-agent, knowledge graph)
```

Every phase touches every layer. No phase is purely backend or purely frontend.

---

## Architecture Alignment

All adjustments from `architecture-adjustments.md` have been merged into `architecture.md`. The adjustments file is now historical (can be moved to `archive/`). The implementation plan reflects the consolidated architecture, plus feedback from `implementation-feedback.md` (responses in `implementation-response.md`):

- **Split Tool Discovery / Selection** into separate nodes (Phase 2)
- **VerificationReport TypedDict** with structured fields (Phase 1 state types)
- **Finding TypedDict** for typed findings (Phase 1 state types)
- **Full cycle cost (18)** for retry routing (Phase 2)
- **ReportGeneration budget-exempt** (Phase 2)
- **unverified_claims in ResearchState** (Phase 1 state types)
- **verification_attempts computed** not stored (Phase 5)
- **Conversation/turn semantics** — prior report context in Phase 2, `conversation_history` system tool in Phase 4
- **Tool catalog provisioning** via config file (Phase 2, deepened in Phase 3)
- **Embedding model** with swappable interface in `moira/embeddings/` (Phase 2, separated from inference)
- **Single-user auth** with server-side `DEFAULT_USER_ID` constant (Phase 1), multi-user data model (Phase 4)
- **Node-level failure handling** with RetryPolicy, error_handler, and checkpoint recovery as separate paths (Phase 2)
- **Theme packaging** as ZIP archives (Phase 6), Phase 1 directory layout matches unzipped format
- **Routing terminology** matches architecture: Pass / Fail + Budget Sufficient / Fail + Budget Insufficient
- **SSE event schema** defined as architecture-level contract (Phase 2)
- **LangGraph async** throughout (Phase 2)
- **SqliteSaver** acknowledged as allowed exception to persistence boundary (Phase 2)
- **System tools vs catalog tools** — `conversation_history` is privileged, bypasses discovery (Phase 2/4)
- **Versioned schema migrations** with numbered SQL patches (Phase 1)
- **Full config schema** validated via Pydantic at startup, unused sections ignored (Phase 1)
- **Model preferences** stored per-user in SQLite `model_preferences` table (Phase 1)
- **Phase 7** is a menu of independent capabilities, not a sequence
