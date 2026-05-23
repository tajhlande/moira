# Implementation Plan Feedback

Review of `implementation-plan.md` against `architecture.md` (post-adjustments) and the issues raised in `architecture-critique.md` / `architecture-adjustments.md`.

---

# What the plan gets right

The phased structure is sound. Vertical spikes that touch every layer in every phase will give you a runnable artifact early and let architecture stay honest under contact with code. The phase dependency chain is linear and clear.

The "Architecture Alignment" section at the bottom is a good move — it explicitly traces each adjustment back to a phase, which makes it easy to verify nothing was dropped. Spot-checking: the full-cycle-cost-18 fix, budget-exempt Report Generation, `VerificationReport`/`Finding` TypedDicts, split Discovery/Selection, `unverified_claims` in `ResearchState`, separated embedding interface, single-user-with-user-id schema, and the three terminal conditions for Report Generation are all there.

Service DI in Phase 1 is the right place for it — putting `service_setup` before anything that depends on it means the abstraction doesn't get retro-fitted and tests stay clean from day one. The same applies to defining repository interfaces and state TypedDicts upfront in Phase 1, before any node code exists to depend on them.

---

# Blocking issues

## 1. Phase 1 is too big to be a "skeleton"

Phase 1's stated goal is "type a message and get a response from the LLM." But the work listed includes: full repository interfaces, SQLite *and* LanceDB implementations, the embedding client (with sentence-transformers model loading), the tool catalog module, tool discovery, an executor, the inference client, a model registry, plus full frontend scaffolding with NaiveUI and theme directory structure.

That's 6–8 of the architecture's major subsystems, not a skeleton. The first vertical spike should prove the end-to-end loop is *bootable* — backend talks to frontend talks to LLM — and almost nothing else. LanceDB, embeddings, and the tool catalog have no consumers in Phase 1 and add real first-boot friction (model download, vector store init, config schema commitment).

**Recommendation**: trim Phase 1 to: monorepo layout, `service_setup`, state TypedDicts, repository *interfaces* (no implementations beyond a minimal SQLite one for sessions), inference client, minimal FastAPI routes, minimal Vue+NaiveUI shell. Defer LanceDB, embeddings, and the entire `tools/` package to Phase 2 where they have a consumer (Tool Discovery node). The bigger Phase 1 you have today won't prove much more than a leaner one would, and the unused scaffolding can drift from the eventual real usage.

## 2. LangGraph's SqliteSaver vs. the repository abstraction

Architecture mandates that "no part of the application outside the persistence layer should import or depend on database-specific modules directly." LangGraph's `SqliteSaver` is a database-specific module imported by the workflow layer. Phase 2 calls for "SQLite checkpoint saver for workflow state" without acknowledging this tension.

This isn't necessarily wrong — checkpointing is a LangGraph internal concern and treating it as an exception is defensible. But the plan should say so explicitly, because otherwise the first contributor to wire up checkpointing will either (a) violate the boundary silently or (b) try to wrap `SqliteSaver` behind a `CheckpointRepository` interface, which is fighting the framework.

**Recommendation**: in Phase 2, state explicitly that LangGraph's checkpoint saver is an allowed exception to the persistence boundary because it's a framework-internal concern. The `CheckpointRepository` interface mentioned in Phase 1 should either be removed or scoped to *reading* checkpoints for observability/inspection, not writing them.

## 3. SSE event schema is undefined — and it's the backend/frontend contract

Phase 2 says the SSE endpoint emits "stage transitions, tool calls, intermediate outputs, budget updates." Phase 2 frontend says "display current workflow stage" and "budget remaining display." Nothing defines the event envelope, types, or sequencing.

This is the contract surface between two layers being built in parallel. Defining it after Phase 2 starts means rework on whichever side guessed wrong. Define it before either side codes against it.

**Recommendation**: add a small section to Phase 2 specifying the SSE event types (e.g., `node_start`, `node_end`, `tool_call`, `tool_result`, `budget_update`, `verification_report`, `run_complete`, `run_error`) and their payload shapes. Treat this as an architecture-level artifact, not implementation detail.

## 4. Conversation/turn semantics deferred to Phase 4 is too late

The critique called turn semantics "the biggest missing piece" of the original architecture. The architecture now has a section on it. But the plan defers implementing it to Phase 4 — *after* the full workflow runs in Phase 2 and tools work in Phase 3.

What does a multi-message session look like in Phases 2 and 3? The plan implies each message is a standalone graph run with no prior-report context — but it doesn't say so, and the frontend chat UI will already render multiple messages by Phase 1. Users (you, testing) will hit this immediately in Phase 2 and either confuse themselves or build an ad-hoc workaround that has to be unwound in Phase 4.

**Recommendation**: either (a) move the basic "prior ResearchReport carried forward as context" plumbing to Phase 2, since it's a small addition once the workflow exists, deferring only the `conversation_history` tool to Phase 4; or (b) explicitly state in Phase 2 that sessions are single-turn until Phase 4, and have the UI reflect that (no follow-up input until prior turn completes, or a "start new session for follow-up" prompt). Don't leave it implicit.

---

# Coherence problems

## 5. Theme format split between Phase 1 and Phase 6

Phase 1: "Theme = directory with `manifest.json` + CSS overrides (ZIP packaging comes later)."
Phase 6: ZIP archive with `manifest.json`, CSS, fonts, images.

Are these the same internal format with different packaging? If yes, say so — the Phase 1 default themes should sit in a directory layout identical to what gets unzipped in Phase 6, so Phase 6 is purely about adding upload/extraction. If they're different schemas, you'll have a migration on your hands.

## 6. `PUT /api/models` in Phase 1 has nowhere to store the assignment

Phase 1 includes the endpoint but doesn't define persistence for the model-purpose assignment. Is it per-user, per-session, or global? Stored in SQLite or in a config file? The architecture says "available models should be read from the API and assigned to purpose (intelligence or task) by the user" — but the storage location isn't defined.

Pick one and document it. The natural answer for single-user MVP is "per-user row in SQLite, default user has it" — which slots cleanly into the Phase 4 multi-user story without rework.

## 7. Tool-catalog code in Phase 1 layout, but Phase 1 doesn't use it

The Phase 1 layout includes `moira/tools/catalog.py`, `discovery.py`, and `executor.py`. Phase 1's exit criteria don't include tool usage. These modules either exist as empty placeholders (clutter) or get partially implemented and then redone in Phase 2 (waste).

Either drop the entire `tools/` subdirectory from Phase 1 (consistent with the Phase 1 trimming suggested in #1), or explicitly state that the Phase 1 versions are interface stubs only with no implementation.

## 8. Cost weights live in config in Phase 1 but aren't enforced until Phase 2

This is fine — config schema can be committed to before the consumer exists — but it means the Phase 1 config template needs to define the schema for cost weights, budget limits, *and* all the fields that won't be consumed until Phase 2 (tool definitions) or Phase 3 (MCP server addresses) or later. Otherwise the config template keeps mutating across phases and any user-edited config breaks at each phase boundary.

**Recommendation**: in Phase 1, define the full config schema even where most fields are unused. Validate it but ignore unimplemented sections. Add a Phase 1 task to write the complete `moira-config-template.yaml` reflecting all known phases.

---

# Completeness gaps

## 9. The `conversation_history` tool: special-case or normal tool?

Phase 4 mentions it briefly. The architecture says "the agent has access to a `conversation_history` tool that retrieves prior messages on demand." Is this tool registered in the catalog (and thus subject to semantic discovery) or is it always available regardless of the Tool Selection node's output?

If it's in the catalog: how does it get discovered when the user's question doesn't semantically resemble "I want to look at past messages"? The Planning node will have to know to surface it, which means it's not really discovered semantically — it's privileged.

If it's privileged: that's fine, but it's an architectural distinction (system tools vs. catalog tools) the architecture and plan don't currently make.

**Recommendation**: decide which it is and document it. The simpler answer is "always available, bypasses discovery, always passed to Research Execution as a known tool" — but say so.

## 10. LangGraph async vs sync, FastAPI async

Backend is FastAPI (async). LangGraph supports both sync and async graphs but the patterns are different. Streaming via SSE requires async. The plan doesn't specify which LangGraph API to use.

**Recommendation**: state in Phase 2 that nodes are async functions and the graph uses LangGraph's async APIs throughout.

## 11. Default user / auth header at all

Phase 4 says "default user created at startup." But the API needs to know which user is making a request before that. Is there a hardcoded header, a cookie set at first page load, a config-file user ID? With no authentication, the answer is probably "single hardcoded user ID resolved server-side" — but that's a real decision because it shapes how the user-id field on every table gets populated and how Phase 4-era multi-user is added.

Specify in Phase 1 how the user ID is resolved for the single-user case. The easiest answer: server-side constant `default_user_id`, attached to all writes by the API layer, not by the client.

## 12. Schema migrations across phases

Phase 4 adds trace persistence to SQLite. Phase 5 may add citation tables. Phase 7 adds knowledge-graph storage. Nothing in the plan addresses how schema evolves between phases.

For a single-developer project this is often "drop and recreate the dev DB," but if any phase exit criterion includes "data persists from prior phase," that breaks. At minimum, decide whether the SQLite schema is managed manually, with Alembic, or by some lightweight migration in-house — and put it in Phase 1 where the schema gets created.

## 13. Error handler with partial state

Phase 2's error handler routes any node failure to Report Generation. But Report Generation reads state — and if the failure happened at Tool Selection, `active_tools` is empty; if at Research Execution, `findings` is empty; if at Compression, `compressed_findings` is empty.

Report Generation has to be robust to *any* missing prefix of state. The plan should say so explicitly, and the node should be tested against partial-state inputs (one test per phase boundary in the workflow).

## 14. Real-endpoint requirement in Phase 1 exit criteria

"Inference client works against a real OpenAI-compatible endpoint" is a Phase 1 exit criterion. That means Phase 1 cannot exit without a running endpoint somewhere. For a self-hosted project this isn't unreasonable, but worth distinguishing between *unit tests against a mock* (which should pass standalone) and *manual verification against a real endpoint* (which is a smoke test, not a CI gate).

**Recommendation**: split the exit criterion into (a) unit/integration tests pass with mock endpoint (CI gate), and (b) manual smoke test against a configured real endpoint (developer checklist, not CI gate).

## 15. `MOIRA_CONFIG_FILE` env var with no default + `make dev`

"Fails fast if unset" is good fail-safe behavior but bad first-boot UX. `make dev` should either set `MOIRA_CONFIG_FILE=./config/moira-config.yaml` if a user-edited config exists, or print a clear message ("copy `moira-config-template.yaml` to `moira-config.yaml` and edit it, then re-run").

Small thing, but the alternative is the first contributor hitting an opaque startup failure.

---

# Smaller things worth a pass

- **Phase ordering of sessions/projects vs tools**: Phase 4 introduces session and project CRUD, but a primitive `SessionRepository` already exists in Phase 1 to support the chat endpoint. The plan should clarify that Phase 1 has just-enough session storage (insert message, fetch transcript) and Phase 4 fleshes it out with project scoping, multi-session navigation, and trace integration. Currently Phase 1 just says "create session" and Phase 4 says "full CRUD" with no overlap statement.

- **`architecture-adjustments.md` is now historical**: Phase 0 / "Architecture Alignment" notes this, but the file is still in the working directory. Either delete it or rename to `archive/architecture-adjustments.md` to make the historical status physical.

- **Exit criteria are qualitative**: "Semantic discovery returns relevant tools for domain-specific queries" — relevant how, measured how? For a single-developer project this might be fine ("I tried 5 queries and they looked right"), but consider whether at least the Phase 2 and 3 exit criteria should have at least one quantitative check (top-1 match rate on a fixture set of question→expected-tool pairs).

- **Phase 7 grab-bag**: parallel branches, advanced planning, long-running tasks, human checkpoints, knowledge graph, multi-agent — that's six largely independent capabilities bundled as one phase. Realistically each is a phase of its own. Consider explicitly saying "Phase 7 is a menu, not a sequence — pick whichever direction matters most when you get there."

- **Frontend testing is light**: Vitest component tests in Phase 1, then "tests" is unspecified for later frontend work. The streaming workflow UI is the most complex frontend surface — it deserves explicit testing strategy at Phase 2 (e.g., mocked SSE source).

- **No mention of OpenAPI/JSON-schema validation for the config file**: Config drives a lot of behavior. A JSON schema that the loader validates against would catch typos immediately. Worth adding to Phase 1.

---

# Summary

The plan is structurally sound and faithfully traces the post-adjustment architecture into phases. The most pressing issues are (1) Phase 1 is bloated and should be trimmed, (2) the SSE event schema needs to be defined before two layers code against it, (3) the LangGraph SqliteSaver / repository-boundary tension needs to be acknowledged, and (4) conversation/turn semantics should not be deferred past the first phase that has a real workflow.

Nothing here invalidates the phased structure. Most of the gaps are "say the quiet part out loud" — decisions you'll make anyway, but better made explicitly now than implicitly under pressure later.
