# Implementation Plan Feedback — Responses and Remedies

Response to `implementation-feedback.md`. Each item states the decision and where the implementation plan changes.

---

# Blocking Issues

## 1. Phase 1 is too big to be a "skeleton"

**Decision**: Agree. Trim Phase 1 to bootable backend + frontend + LLM only.

**Remedy**: Remove LanceDB, embeddings, and the entire `tools/` package from Phase 1 — they have no consumers until Phase 2. Phase 1 keeps: monorepo layout, `service_setup`, state TypedDicts, repository interfaces (only minimal SQLite implementation for sessions), inference client + model registry, minimal FastAPI routes, minimal Vue + NaiveUI shell. LanceDB, embeddings, `tools/`, semantic discovery, and tool execution all move to Phase 2 where they have consumers.

**Plan changes**: Phase 1 directory tree, backend deliverables, and exit criteria all updated. Phase 2 picks up the deferred subsystems.

## 2. LangGraph SqliteSaver vs. the repository abstraction

**Decision**: LangGraph's checkpoint saver is an allowed exception to the persistence boundary because it is a framework-internal concern.

**Remedy**: State this explicitly in Phase 2. Scope `CheckpointRepository` to *reading* checkpoints for observability and inspection — LangGraph owns writing them.

**Plan changes**: Phase 2 adds note. Phase 1 removes `CheckpointRepository` from repository interfaces (deferred to Phase 2 with observability).

## 3. SSE event schema is undefined

**Decision**: Define SSE event types and payload shapes as an architecture-level contract in Phase 2, before either layer codes against it.

**Remedy**: Add SSE event schema section to Phase 2 specifying event types and payloads:

| Event | Payload |
|---|---|
| `node_start` | `{ node: str, timestamp: str }` |
| `node_end` | `{ node: str, timestamp: str, budget_remaining: float }` |
| `tool_call` | `{ tool: str, args: dict }` |
| `tool_result` | `{ tool: str, output: str, duration_ms: int }` |
| `budget_update` | `{ budget_remaining: float, budget_consumed: float, node: str }` |
| `verification_report` | `{ report: VerificationReport, attempt: int }` |
| `run_complete` | `{ report: ResearchReport }` |
| `run_error` | `{ node: str, error: str, state_snapshot: dict }` |

**Plan changes**: New subsection in Phase 2 backend.

## 4. Conversation/turn semantics deferred to Phase 4 is too late

**Decision**: Move basic "prior ResearchReport carried forward as context" to Phase 2. Keep `conversation_history` tool in Phase 4.

**Remedy**: Phase 2 implements: each message starts a fresh graph run, prior ResearchReport (if any) injected as context. Phase 2 sessions are multi-turn — follow-up messages see the prior report. Phase 4 adds the `conversation_history` tool for on-demand transcript access and full session/project management.

State explicitly in Phase 2 that sessions carry forward the prior report from the first workflow run onward.

**Plan changes**: Phase 2 backend adds conversation context item. Phase 4 item updated to focus on `conversation_history` tool and project management.

---

# Coherence Problems

## 5. Theme format split between Phase 1 and Phase 6

**Decision**: Phase 1 default themes sit in a directory layout identical to what Phase 6 unzips. Phase 6 adds upload/extraction only — no format migration.

**Remedy**: State in Phase 1 that the theme directory structure (`manifest.json` + CSS + fonts + images) is the canonical format. ZIP packaging is a transport mechanism added in Phase 6, not a format change.

**Plan changes**: Phase 1 theme scaffolding item updated.

## 6. `PUT /api/models` has nowhere to store the assignment

**Decision**: Store as per-user preference row in SQLite. `model_preferences` table with columns `user_id`, `intelligence_model_id`, `task_model_id`. Single row for the default user.

**Remedy**: Add `model_preferences` table to Phase 1 SQLite schema. `PUT /api/models` writes to this table. `GET /api/models` reads from it plus queries configured endpoints for the available model list. Fits the Phase 4 multi-user story with no rework.

**Plan changes**: Phase 1 SQLite implementations item updated. Phase 4 auth section references this table.

## 7. Tool-catalog code in Phase 1 layout, but Phase 1 doesn't use it

**Decision**: Resolved by #1. Entire `tools/` directory moves to Phase 2.

**Remedy**: No separate change needed.

## 8. Cost weights in config but not enforced until Phase 2

**Decision**: Define full config schema in Phase 1, including all fields for later phases. Loader validates the complete schema but ignores unused sections.

**Remedy**: Phase 1 config task expanded: write the complete `moira-config-template.yaml` reflecting all known phases. Add Pydantic model for config validation that rejects typos and missing fields at startup. Sections not yet consumed (tool definitions, MCP server addresses, etc.) are validated structurally but ignored functionally.

**Plan changes**: Phase 1 project config item updated.

---

# Completeness Gaps

## 9. The `conversation_history` tool: special-case or normal tool?

**Decision**: Privileged system tool. Always available, bypasses semantic discovery, always injected into Research Execution by the graph regardless of what Tool Selection produces.

This establishes a two-category tool architecture:

- **Catalog tools** — registered in the tool catalog, embedded in LanceDB, subject to semantic discovery, model-selected per workflow step
- **System tools** — always present, framework-injected, bypass discovery entirely. `conversation_history` is the first; future examples might include `report_context` or similar

**Remedy**: Document this distinction in the implementation plan. Phase 2 tool infrastructure supports both categories. Phase 4 implements `conversation_history` as a system tool.

**Plan changes**: Phase 2 adds system tool concept. Phase 4 item updated to note `conversation_history` is system-scoped.

## 10. LangGraph async vs. sync

**Decision**: All nodes are async functions. Graph uses LangGraph's async APIs throughout. Required for SSE streaming.

**Remedy**: State in Phase 2 node implementation guidelines.

**Plan changes**: Phase 2 node implementations item updated.

## 11. Default user / auth header

**Decision**: Server-side constant `DEFAULT_USER_ID`, attached to all writes by the API layer. No client header or cookie for single-user MVP.

**Remedy**: Add to Phase 1. API routes resolve user ID server-side. All session, project, and preference writes use this constant. Phase 4 multi-user replaces the constant with request-scoped user resolution.

**Plan changes**: Phase 1 adds default user item.

## 12. Schema migrations across phases

**Decision**: Simple versioned migrations in Phase 1. `schema_version` meta table, numbered SQL patch files applied in order by `moira/persistence/sqlite/schema.py`. No Alembic for SQLite MVP.

**Remedy**: Phase 1 defines the migration runner and initial schema as patch `001_initial.sql`. Later phases add patches (`002_traces.sql`, etc.).

**Plan changes**: Phase 1 SQLite implementations item updated.

## 13. Error handler with partial state

**Decision**: Report Generation must be robust to any missing prefix of state fields.

**Remedy**: State this explicitly in Phase 2 error handler section. Add tests: one per node boundary in the workflow, each simulating a failure at that point and verifying Report Generation produces a valid report with partial state.

**Plan changes**: Phase 2 error handler and test items updated.

## 14. Real-endpoint requirement in Phase 1 exit criteria

**Decision**: Split into two criteria:

- **CI gate**: unit and integration tests pass with mock endpoint
- **Manual verification**: smoke test against a configured real endpoint (developer checklist, not CI gate)

**Remedy**: Update Phase 1 exit criteria.

**Plan changes**: Phase 1 exit criteria updated.

## 15. `MOIRA_CONFIG_FILE` first-boot UX

**Decision**: `make dev` checks for `./config/moira-config.yaml`. If missing, prints a clear message and exits: "Copy `config/moira-config-template.yaml` to `config/moira-config.yaml`, edit it, and re-run." Application startup produces the same message if the env var is unset or the file is missing.

**Remedy**: Add first-boot handling to Phase 1.

**Plan changes**: Phase 1 adds first-boot UX item.

---

# Smaller Things

## Session overlap Phase 1 / Phase 4

Phase 1 has minimal session storage: insert a session, insert messages, fetch transcript for display. No projects, no multi-session navigation. Phase 4 adds project scoping, multi-session management, and trace integration.

**Plan changes**: Phase 1 API routes and session description clarified. Phase 4 entry notes the expansion.

## Archive `architecture-adjustments.md`

Note in the Architecture Alignment section that `architecture-adjustments.md` is historical and can be archived.

## Quantitative exit criteria

Add fixture-based discovery tests for Phase 2 and 3: a set of question → expected-tool pairs with a top-1 match rate threshold. Defines "relevant" concretely.

**Plan changes**: Phase 2 and 3 test sections updated.

## Phase 7 grab-bag

Phase 7 is explicitly a menu, not a sequence. Each capability (parallel branches, advanced planning, long-running tasks, human checkpoints, knowledge graph, multi-agent) is independent. Pick whichever direction matters most when you get there.

**Plan changes**: Phase 7 description updated.

## Frontend streaming tests

Phase 2 frontend gets an explicit test strategy: mocked SSE source feeding the streaming UI components, verifying stage transitions, budget updates, and report rendering against known event sequences.

**Plan changes**: Phase 2 frontend tests updated.

## Config schema validation

Phase 1 adds Pydantic model for config validation. Rejects typos, missing required fields, and invalid value types at startup. See #8 above.

**Plan changes**: Phase 1 config item updated (combined with #8).
