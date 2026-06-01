# MOiRA Phased Implementation Plan

Each phase is a vertical spike: it touches the backend, frontend, persistence, and workflow layers just enough to produce a runnable, demonstrable increment. Later phases deepen existing components rather than introduce new categories.

---

## Phase 1: Skeleton — Bootable Full-Stack App [COMPLETE]

**Goal**: Establish the monorepo layout, get a bootable backend and frontend that talk to each other, wired to a real model endpoint. You can open the UI, type a message, and get a response from the LLM.

### What was built

- Monorepo layout with `backend/` (Python/FastAPI) and `frontend/` (Vue 3/NaiveUI/Vite)
- `config/moira-config-template.yaml` with full config schema; validated at startup by Pydantic
- `MOIRA_CONFIG_FILE` env var; startup checks for config file existence
- `service_setup.py` DI container: `init_services`, `shutdown_services`, `service_provider`
- SQLite persistence with versioned migration runner (`CURRENT_VERSION = 6`)
- `ConversationRepository`: CRUD + messages + runs
- `ModelPreferencesRepository`: get/set model assignments
- Inference client (`moira/inference/client.py`): async httpx, OpenAI-compatible
- Model registry (`moira/inference/registry.py`): maps "intelligence"/"task" to endpoints
- API routes: health, conversations CRUD, model list/assign
- Frontend: Vite + Vue 3 + NaiveUI + Pinia stores + API client

### Deviations from original plan

- Terminology: "conversation" / `conversation_id` everywhere, not "session"
- `service_provider()` returns `object`; callers must `cast()`
- Config uses `providers` list with explicit `intelligence_endpoint`/`intelligence_model`/`task_endpoint`/`task_model` fields
- Data path unification: `resolve_data_dir()` uses `_repo_root()` for absolute path

---

## Phase 2: Workflow — LangGraph Research Loop [COMPLETE]

**Goal**: Replace the raw model pass-through with the full LangGraph research workflow. The UI shows workflow stages streaming in real time, with semantic tool discovery and built-in tools available. Prior ResearchReport carries forward as context for follow-up messages.

### What was built

**Backend:**

- **Tool system** (`moira/tools/`)
  - `BaseTool` ABC with self-describing spec: `tool_name`, `tool_description`, `tool_argument_schema`, `tool_group`, `tool_config_schema`
  - `make_definition()` classmethod produces `ToolDefinition` from class-level spec
  - `get_spec()` classmethod returns config/secret schemas
  - `ToolDefinition` dataclass: name, description, argument_schema, config, tags, reliability, is_default, enabled, built_in, implementation, group_name
  - Three built-in tool implementations: `CalculatorTool`, `UrlContentTool`, `WebSearchTool`
  - `ToolExecutor` with timeout, retry, batch execution, `allowed_tools` filtering
  - `ToolDiscovery` with LanceDB semantic search + default tools
  - `ToolCatalog` for tool management

- **Embedding client** (`moira/embeddings/`)
  - Local sentence-transformers (all-MiniLM-L6-v2), runs on CPU
  - Swappable interface: local model or OpenAI-compatible endpoint

- **LanceDB** (`moira/persistence/lancedb/`)
  - `ToolEmbeddingRepository`: vector + metadata columns, semantic similarity search
  - Upsert handles both `FileNotFoundError` and `ValueError` for missing tables
  - `prefilter=True` on `enabled` column for pre-ranking filtering

- **LangGraph graph** (`moira/workflow/graph.py`)
  - 7 active nodes: planning → tool_discovery → tool_selection → research_execution → draft_synthesis → verification → report_generation
  - Compression node exists in cost weights but is **bypassed** (findings flow directly from research_execution to draft_synthesis to preserve citation provenance)
  - Conditional routing: `make_verification_router(config)` checks outcome + budget
  - `route_after_error` shortcut to report_generation
  - `AsyncSqliteSaver` for async checkpointing

- **Multi-round tool execution** (`_run_tool_loop` / `_execute_tool_round`)
  - Up to `DEFAULT_MAX_TOOL_ROUNDS = 3` rounds per node
  - Shared by both `research_execution` and verification fact-check
  - Line-delimited JSON parser fallback for local models

- **Tool-assisted verification** (two-phase):
  - Phase 1: fact-check with tools via `_run_tool_loop`
  - Phase 2: verdict LLM call with evidence
  - Verification detail includes `fact_check_prompt`, `tool_results`, merged `structured_output`
  - Retry-declined transparency: amber warning when budget insufficient for retry

- **Budget enforcement** (`moira/workflow/budget.py`)
  - Full cycle cost = 17 (excludes compression and report_generation)
  - ReportGeneration is budget-exempt (always runs as terminal node)

- **Run persistence & SSE streaming** (`moira/workflow/run_manager.py`)
  - `RunManager` + `ActiveRun`: background graph execution, incremental persistence
  - Per-subscriber asyncio.Queue for SSE broadcast
  - Event replay on subscribe for late subscribers
  - POST returns JSON (`run_id`), GET streams SSE — decoupled start/stream
  - 409 on concurrent run for same conversation
  - SSE events: `node_start`, `node_end` (with timing), `tool_result`, `verification_report`, `run_complete`, `run_error`

- **Prompt system** (`moira/resources/prompts.md`, `moira/prompts.py`)
  - Prompt template file with required sections, cached for process lifetime
  - `MOIRA_PROMPT_FILE` override, server restart required for changes
  - Research prompt is pure tool-use (no synthesis phase)
  - Verification fact-check prompts: must use tools for every claim

**Frontend:**

- SSE streaming display with per-message run artifacts
- RunArtifacts / ReportPanel SFCs with shared CSS
- Live clock: 1-second interval timer for running step elapsed
- Markdown rendering: `marked` + `shiki` with 10 languages, dual themes
- Step detail content: expandable sections for prompts, responses, thinking, structured output, tool results
- Tool call count indicators: `Tool` icon next to step titles
- Tool executions in expandable scrollable panel
- Tool Calls in dedicated panel separate from Structured Output
- Conversation title generation (AI) and manual `Wand` button
- Conversation deletion (full stack)
- Assistant message deduplication; `assistant_report` messages filtered from API

### Deviations from original plan

- **Compression bypassed**: destroyed citation provenance, ran lossy step on wrong model. Findings now flow directly to `draft_synthesis`. Compression code retained but dormant.
- **7 nodes, not 8**: compression removed from active graph path. Full cycle cost = 17, not 18.
- **Multi-round tool execution**: not in original plan. `_run_tool_loop` enables iterative tool use within a single node.
- **Tool-assisted verification**: not in original plan. Verification now independently fact-checks claims before producing a verdict.
- **Run persistence & reconnection**: `RunManager` + `ActiveRun` decouple graph execution from HTTP requests.
- **Tool implementations as authoritative spec source**: `BaseTool` subclasses produce their own `ToolDefinition` via `make_definition()`, replacing the planned YAML config format.
- **`built_in` boolean on tools**: tools distributed with MOiRA, immutable except `enabled` flag. Separate from `is_default`.
- **Error handling**: no separate `error_handler.py` — error routing handled in `graph.py` via `route_after_error`.
- **Line-delimited JSON parser**: local models (Qwen, Gemma) may produce `{}\n{}` instead of `[{},{}]`.

### Tests

- 243 backend tests across 12 test files
- Unit tests for each node (mocked model)
- Integration test: full graph run with mock inference
- Budget exhaustion test
- Verification pass/fail/retry tests including tool-assisted verification
- Tool implementation tests: CalculatorTool (56), UrlContentTool (19), WebSearchTool (18)
- Prompt validation tests
- Config file tests
- ruff clean, vue-tsc clean

---

## Phase 3: Tools, Discovery, and Tool Lifecycle [IN PROGRESS]

**Goal**: Complete the tool lifecycle — encrypted secrets, MCP integration, two-pass discovery, richer tool UI, and the `user_question` tool.

### Backend

1. **Tool secrets** (`moira/tools/secrets.py`)
   - `secrets` TEXT column on tools table (Migration 007)
   - Fernet encryption via `cryptography` package, per-tool key derived from `PBKDF2(MOIRA_SECRETS_KEY, salt=tool_name)`
   - Plaintext fallback when `MOIRA_SECRETS_KEY` not set (with startup warning)
   - `PATCH /api/tools/{name}` accepts `secrets` dict, encrypts before storage
   - `GET /api/tools` and `GET /api/tools/{name}` return `secret_keys` list only, never values
   - `BaseTool.secret_schema` class attribute for declaring required secrets
   - See `agent-docs/tool-secrets-and-spec.md` for full design

2. **MCP client integration** (`moira/tools/mcp_client.py`)
   - Connect to configured MCP servers at startup
   - Discover available tools from MCP servers dynamically
   - Wrap each MCP tool into `BaseTool` interface
   - MCP tools ingested into catalog alongside built-in tools
   - Config: `mcp_servers` list in `moira-config.yaml`

3. **Tool executor hardening**
   - Configurable retry policy (max retries, exponential backoff) — currently fixed at `DEFAULT_MAX_RETRIES = 1`
   - Per-tool timeout from `ToolDefinition.config`
   - Structured error capture: timeout, connection error, malformed response, auth failure

4. **Two-pass tool discovery**
   - Restructure graph: `tool_discovery (question-driven)` → `planning (informed by tools)` → `plan_driven_discovery` → `tool_selection`
   - Round 1: embed question → LanceDB search (no model call, cost weight: 1)
   - Round 2: embed plan → LanceDB search, merge with round 1 results (dedupe by name, keep highest score)
   - Planning prompt includes candidate tools from round 1
   - New state field: `candidate_tools`
   - See `agent-docs/two-pass-discovery.md` for full design

5. **`user_question` tool**
   - System tool: model can ask the user a follow-up question with multiple-choice options
   - User answer appended to prompt, step re-run
   - See `agent-docs/standard-tools.md` for requirements

6. **`url_content` summarize flag**
   - Wire up `summarize` boolean parameter as bridge to sub-agent call
   - Summarization via task model on long content

7. **Stale run cleanup**
   - On startup, scan for `status="running"` runs and mark as `status="error"` with server-restart note

### Frontend

1. **Tool secrets UI**
   - `ToolDetailView`: secrets section with key names, lock/unlock placeholder
   - Config schema form from `GET /tools/{name}/spec`

2. **TipTap rich-text input**
   - Replace plain `NInput` with TipTap editor
   - Markdown bridge: `editor.getMarkdown()` → API receives plain string
   - Packages: `@tiptap/vue-3`, `@tiptap/starter-kit`, `@tiptap/markdown`
   - See `agent-docs/tiptap-input.md` for full design

3. **Tool wizard (create tool)**
   - `ToolWizardView` currently a placeholder
   - Allow users to register REST tools or MCP-discovered tools

### Tests

- Encryption round-trip tests (with and without `MOIRA_SECRETS_KEY`)
- Spec endpoint tests
- Secret storage and retrieval tests
- MCP wrapper tests with mock MCP server
- Two-pass discovery integration tests
- `user_question` tool tests
- Updated budget tests (new cycle cost with two discovery rounds)
- TipTap input component tests

### Exit criteria

- Tool secrets are encrypted at rest and never exposed in API responses
- MCP tools appear alongside built-in tools in catalog
- Two-pass discovery produces better tool selection
- `user_question` tool works end-to-end
- All tests pass, ruff clean, vue-tsc clean

---

## Phase 4: Observability and Conversation Depth

**Goal**: Execution tracing and inspection, the `conversation_history` system tool, and settings UI. The app provides full transparency into every research run.

### Backend

1. **`conversation_history` system tool**
   - Always available, bypasses semantic discovery, injected by graph regardless of Tool Selection
   - Retrieves prior messages from the conversation transcript on demand
   - Prior ResearchReport context (already built) remains unchanged
   - This completes the conversation architecture: prior report always in context, transcript available on demand

2. **Execution tracing** (`moira/observability/tracing.py`)
   - Structured logging: node transitions, tool selections, tool outputs, model prompts, verification failures, budget consumption
   - Traces persisted to SQLite alongside conversation data
   - `GET /api/conversations/{id}/trace` — full execution trace
   - `GET /api/conversations/{id}/trace/{node}` — per-node trace

3. **Metrics**
   - Budget consumed per node execution
   - Total budget per conversation
   - Tool call counts and latencies
   - Verification attempt counts

4. **Settings API**
   - `GET /api/settings` — current configuration (safe subset)
   - `PATCH /api/settings` — update runtime-configurable settings (budget limit, model assignments)

### Frontend

1. **Workflow inspection**
   - Expandable node history per conversation in the right panel
   - Tool call details with inputs and outputs (already partially built in step detail)
   - Verification history with retry attempts
   - Budget display: remaining, consumed per node, total consumed

2. **Settings UI**
   - `SettingsView` currently a placeholder
   - Model configuration (already functional via model API)
   - Budget limit configuration
   - System info display

3. **Trace viewer**
   - Per-conversation execution trace
   - Per-node drill-down

### Tests

- `conversation_history` tool tests
- Trace completeness tests
- Settings round-trip tests
- Conversation context tests (prior report carried forward)

### Exit criteria

- Every workflow run produces a queryable trace
- Conversation turns carry forward prior report context + on-demand transcript
- Settings UI is functional for model and budget configuration
- All tests pass, ruff clean, vue-tsc clean

---

## Phase 5: Citations and Rich Reports

**Goal**: Rich citation handling, polished report rendering with export, citation management system.

### Backend

1. **Citation management** (`moira/citations/`)
   - Source deduplication across findings
   - Citation normalization (URLs, DOIs, titles)
   - Citation-graph construction for multi-source claims
   - Link claims in report to supporting evidence

2. **Enhanced report generation**
   - Inline citation markers in answer text
   - Evidence linking: each claim maps to supporting findings
   - Confidence indicators per claim
   - Three terminal conditions already implemented (verified, budget_exhausted, error) — enhance with citation data

3. **Report export**
   - `GET /api/conversations/{id}/report/export?format=markdown`
   - `GET /api/conversations/{id}/report/export?format=pdf`

### Frontend

1. **Rich report rendering**
   - Formatted report view with clickable inline citations
   - Evidence panel showing source excerpts
   - Verification summary with pass/fail/retry details
   - Unverified claims highlighted with caveats

2. **Export UI**
   - Download buttons for Markdown and PDF on report panel

### Tests

- Citation deduplication and normalization tests
- Report structure validation tests with citations
- Export output tests (Markdown and PDF)

### Exit criteria

- Reports have clickable inline citations linking to evidence
- Export works for Markdown and PDF
- Citation management deduplicates and normalizes sources
- All tests pass, ruff clean, vue-tsc clean

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

3. **Settings UI (theme section)**
   - Theme selection in settings view

### Tests

- Theme loading and switching tests
- Accessibility audit (automated)
- Settings round-trip tests (themes)

### Exit criteria

- Themes are installable, switchable, and correctly override NaiveUI tokens, fonts, and images
- UI is responsive and accessible
- All tests pass, ruff clean, vue-tsc clean

---

## Phase 7: Advanced Capabilities

**Goal**: Extend the platform in whichever direction matters most.

> **Phase 7 is a menu, not a sequence.** Each capability below is largely independent. Pick whichever direction is most valuable when you reach this phase. Each could be its own phase of work.

### Backend

1. **Parallel research branches**
   - LangGraph subgraph for parallel tool execution
   - Fan-out/fan-in: Planning decomposes into sub-questions, each runs independently, results merge at synthesis

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
   - Accumulate verified findings across conversations
   - Cross-conversation citation and fact reuse

6. **Multi-agent workflows**
   - Specialized agent roles (researcher, verifier, synthesizer)
   - Inter-agent communication via shared state

7. **Projects**
   - Group conversations into projects
   - `ProjectRepository`: create, get, list, update, delete
   - `GET/POST /api/projects`, conversations scoped to project

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
   - Cross-conversation findings explorer
   - Citation graph visualization

5. **Project management UI**
   - Create, list, switch projects in sidebar
   - Conversations grouped by project

### Tests

- Parallel branch execution and merge tests
- Human checkpoint pause/resume tests
- Long-running task interrupt tests
- Knowledge graph accumulation tests
- Project scoping tests

### Exit criteria

- Complex questions decompose into parallel research branches
- Long-running tasks run in background with progress visibility
- Human checkpoints pause and resume correctly
- All tests pass, ruff clean, vue-tsc clean

---

## Dependency Summary

```
Phase 1 (skeleton) [COMPLETE]
  └── Phase 2 (workflow + streaming + tools) [COMPLETE]
       └── Phase 3 (tool lifecycle: secrets, MCP, two-pass discovery) [IN PROGRESS]
            └── Phase 4 (observability, conversation_history, settings)
                 └── Phase 5 (citations, rich reports, export)
                      └── Phase 6 (theming, polish)
                           └── Phase 7 (advanced: parallel, multi-agent, knowledge graph, projects)
```

Every phase touches every layer. No phase is purely backend or purely frontend.

---

## Key Design Decisions (Accumulated)

Decisions made during implementation that affect the plan going forward:

- **Compression bypassed**: destroyed citation provenance, ran lossy step on wrong model. Findings flow directly to `draft_synthesis`. Full cycle cost = 17, not 18. Compression code retained but dormant.
- **Multi-round tool execution**: `_run_tool_loop` enables iterative tool use (up to 3 rounds) within research_execution and verification. Shared helper, not per-node duplication.
- **Tool-assisted verification**: verification independently fact-checks claims before verdict. Same tool set as research. Two-phase: fact-check → verdict.
- **Research prompt is pure tool-use**: no synthesis in research_execution. Synthesis belongs in `draft_synthesis` downstream.
- **Implementation class is authoritative spec source**: `BaseTool.make_definition()` produces `ToolDefinition`, replacing planned YAML tool config.
- **`built_in` separate from `is_default`**: built-in = distributed with MOiRA (immutable except `enabled`); is_default = user-editable toggle.
- **Run persistence via RunManager**: decoupled from HTTP requests. POST returns JSON, GET streams SSE. Event replay on subscribe.
- **Line-delimited JSON parser**: local models (Qwen, Gemma) may produce `{}\n{}` instead of `[{},{}]`.
- **`service_setup` preserves user config**: startup upsert preserves `enabled` and `config` for built-in tools.
- **Prompt caching**: prompts loaded once, cached for process lifetime. Server restart required for prompt changes.
- **No separate `error_handler.py`**: error routing handled in `graph.py` via `route_after_error`.
- **Projects deferred**: project scoping moves to Phase 7. Current app is a flat conversation list.
- **Default prompt path**: `moira/resources/prompts.md`, overrideable via `MOIRA_PROMPT_FILE`.

---

## Architecture Alignment

The implementation plan reflects the consolidated architecture from `architecture.md`, plus feedback from `implementation-feedback.md` (responses in `implementation-response.md`):

- **Split Tool Discovery / Selection** into separate nodes [built]
- **VerificationReport TypedDict** with structured fields including outcome-based fields [built]
- **Finding TypedDict** for typed findings [built]
- **Full cycle cost (17)** for retry routing [built, updated from original 18]
- **ReportGeneration budget-exempt** [built]
- **unverified_claims in ResearchState** [built]
- **verification_attempts computed** not stored [built]
- **Conversation/turn semantics** — prior report context [built], `conversation_history` system tool [Phase 4]
- **Tool catalog provisioning** via implementation classes [built, replaced YAML config]
- **Embedding model** with swappable interface in `moira/embeddings/` [built]
- **Single-user auth** with server-side `DEFAULT_USER_ID` constant [built]
- **Node-level failure handling** via error routing in graph [built]
- **Theme packaging** as ZIP archives [Phase 6]
- **Routing terminology** matches architecture: Pass / Fail + Budget Sufficient / Fail + Budget Insufficient [built]
- **SSE event schema** defined as architecture-level contract [built]
- **LangGraph async** throughout [built]
- **AsyncSqliteSaver** for async checkpointing [built]
- **System tools vs catalog tools** — `conversation_history` is privileged, bypasses discovery [Phase 4]
- **Versioned schema migrations** with numbered SQL patches [built, current version = 6]
- **Full config schema** validated via Pydantic at startup [built]
- **Model preferences** stored per-user in SQLite [built]
- **Phase 7** is a menu of independent capabilities, not a sequence

---

## Related Design Documents

| Document | Status | Description |
|---|---|---|
| `tool-secrets-and-spec.md` | Planned (Phase 3) | Encrypted secrets + spec-from-implementation |
| `two-pass-discovery.md` | Planned (Phase 3) | Two-pass tool discovery restructuring |
| `tiptap-input.md` | Planned (Phase 3) | TipTap rich-text editor replacement |
| `run-persistence-reconnect.md` | **Built** | Run persistence & SSE reconnection (now in `run_manager.py`) |
| `phase3-ui-prep.md` | **Built** | Multi-view navigation framework + tool catalog UI |
| `standard-tools.md` | Partial (Phase 3) | Built-in tool definitions; `user_question` not yet built |
| `verification-outcomes.md` | **Built** | 11 verification outcome cases implemented |
| `conversation-state.md` | Partial | Data model defined; projects not yet built |
| `agent-craft.md` | Active | Prompt tuning and model behavior notes |
