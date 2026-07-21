# Agent Documentation Index

## Core (in `core/`)

Foundational architecture and planning documents. 
These are evergreen and should be updated as the project evolves.

| Document | Description |
|---|---|
| **core/architecture.md** | System goals, component structure, persistence strategy, and implementation guidelines for the MOiRA platform. |
| **core/implementation-plan.md** | Phased build plan — Phases 1–2 complete, Phase 3 in progress. Updated to reflect current implementation state. |
| **core/user-experience.md** | What the user sees and does — the intended UX for the research cycle, continued conversation, and sidebar. |
| **core/standard-tools.md** | Descriptive list of built-in tools that should always be available to the agent. |
| **core/research-loop-data-flow.md** | Field-by-field data flow through the 7 research loop nodes, fact/conclusion status lifecycles, budget costs, routing conditions, and known gaps. **Living reference — must be updated when research loop nodes, state fields, or routing change.** |
| **core/structured-output-renderer.md** | Design for the StructuredOutputRenderer component — field renderer registry + type-dispatching fallback to replace JSON dumps in step detail. **Living reference — must be updated when structured output fields or render types change.** |
| **core/conversation-state.md** | Conceptual data model of a conversation (Section 0) and complete inventory of every data element across all implementation layers. |
| **core/agent-craft.md** | Running notes on prompt tuning, model behavior observations, and the non-structural factors that make the agent trustworthy. |

## Core — Feedback Iteration (in `core/feedback-iteration/`)

Architecture review cycle: critique, adjustments, implementation feedback, and responses.
These documents represent moment-in-time reviews of core documents, typically by alternative LLMs.

| Document | Description |
|---|---|
| **core/feedback-iteration/architecture-critique.md** | External review of the architecture document, identifying gaps and risks. |
| **core/feedback-iteration/architecture-adjustments.md** | Planned changes to the architecture in response to the critique. |
| **core/feedback-iteration/implementation-feedback.md** | Review of the implementation plan against the (adjusted) architecture and critique. |
| **core/feedback-iteration/implementation-response.md** | Decisions and remedies responding to the implementation feedback. |

## Active Plans

Plans for active development that has not been fully completed yet.

| Document | Description |
|---|---|
| **goal-alignment-and-research-effectiveness.md** | Plan for fixing goal-alignment failures (5 of 7 questions scoring 2/5). Phased: Phase 1 = search strategy diversity (prompt-only, Change 3) — **done**; Phase 2 = derivation core (Foundational + Changes 1+2, code+prompts) — deferred; Phase 3 = report hedging (Change 4, optional) — deferred. Tracks the agent's inability to answer questions when research is incomplete — catalogs gaps instead of synthesizing best-available answers. |
| **fact-extraction-and-claim-quality.md** | Plan for improving report quality across five areas: claim correction in research_review — **done**; URL fetch dedup — **done**; process-metadata claim detection via reviewer judgment — **done**; citation curation for report generation — deferred; extraction discipline prompts — **done**. |
| **source-quality-and-verification.md** | Brainstorm / pre-plan for source classification and verification standards. Case study: tyranitar-ou run where a Reddit question post backed a false superlative claim marked "verified." Covers source-type taxonomy, classification approaches (tool reliability propagation, curated registry, heuristics), policy options (prompt-level vs code-level enforcement), and the cognitive-load principle (mechanical rules on structured data beat nuanced judgment for mid-grade models). |

## Notes & Commentary

Short-form documents capturing observations and design rationale.

| Document | Description |
|---|---|
| **comment-on-pokemon-as-test.md** | Short note on why competitive Pokemon questions are effective stress tests for the agent — large factual space, named entities, hard categorical rules, and authoritative sources. |

## Parked (in `parked/`)

Plans that have been deferred or superseded. Kept for reference; not actively being worked on.

| Document | Description |
|---|---|
| **parked/two-pass-discovery.md** | Plan for restructuring tool discovery to run before and after planning so plans are tool-aware. Round 1 achieved via `tool_identification` node (different architecture); Round 2 (plan-driven discovery) not built. Largely superseded. |
| **parked/tiptap-input.md** | Plan for replacing the plain-text input with TipTap rich-text editor. Not started. |
| **parked/kagi-web-search-plan.md** | Plan for the Kagi web search tool — first tool to use the credential store, using the Kagi Search API with Bearer auth. Not started. |
| **parked/context-management.md** | Plan for adaptive context window management — auto-detect limits, proactive tool output capping, evidence truncation, and progressive message trimming. Not started. |
| **parked/additional-default-tools.md** | Wishlist of additional default tools (Wikipedia, OpenAlex, FRED, SEC EDGAR, Europe PMC) to reclaim volume from low-quality web search results. |

## Feedback Iteration (in `feedback-iteration/`)

Standalone critiques and assessments of specific plans or the project as a whole.
These represent moment-in-time reviews, typically by alternative LLMs.

| Document | Description |
|---|---|
| **feedback-iteration/claude-assessment.md** | External assessment of MOiRA's architecture, knowledge model, prompts, and overall design quality. |
| **feedback-iteration/tool-secrets-critique.md** | External review of the tool secrets and spec plan, identifying design improvements. |

## Completed (in `completed/`)

These documents describe designs or work that has been completed
and are now archived. Design and implementation docs for specific
features and functionality should be moved into this directory as those
projects are completed.

| Document | Description |
|---|---|
| **completed/research-loop-overhaul.md** | Problem statement and design proposal for the overhauled research workflow — separation into fact discovery, synthesis, verification, and report generation. |
| **completed/research-loop-critique.md** | External review of the research loop overhaul design, identifying strengths and areas for improvement. |
| **completed/research-loop-critique-response.md** | Response to the research loop critique — accept/adopt, accept with modifications, and push back decisions. |
| **completed/research-loop-overhaul-plan.md** | Implementation plan for the research loop overhaul — knowledge model schema, graph structure, persistence, message schemas, and code components. |
| **completed/system-settings.md** | Persistent, layered settings system — DB-backed system-level configuration replacing config-file-only budget settings, with settings UI. Phase 1 complete. |
| **completed/draft-retry-routing.md** | Routing verification failures directly to draft synthesis (bypassing research) for synthesis-specific problems (cases 7-8). |
| **completed/run-persistence-reconnect.md** | Decoupling graph runs from HTTP requests, incremental persistence, and SSE stream reconnection. |
| **completed/phase3-ui-prep.md** | Phase 3 preparation: multi-view navigation framework, tool catalog UI, and standard tool definitions. |
| **completed/verification-outcomes.md** | The 11 verification outcome cases the model can return, grouped into accept/retry/error with retry guidance. |
| **completed/stop-and-resume-plan.md** | Stop/resume feature — dual mechanism (task cancel + interrupt), stopped step persistence, UI controls. |
| **completed/inference-metrics-plan.md** | Workflow steps normalization and inference token tracking — per-node token counts and timing persisted in `workflow_steps`. |
| **completed/tool-metrics-plan.md** | Tool call metrics — rolling counters with hourly buckets, call_type, latency min/max/sum. |
| **completed/state-management-cleanup.md** | Migration plan to unify frontend/backend run state around a canonical snapshot model and remove live-vs-persisted UI duplication. |
| **completed/evaluation-harness.md** | Repeatable evaluation harness — capture completed runs, compute mechanical metrics from the knowledge snapshot, score via a frontier-model judge against a general + Pokemon rubric, and diff results across commits. Implements the harness described in `claude-assessment.md`. |
| **completed/tool-secrets-and-spec.md** | Encrypted secrets storage on tools and spec-from-implementation (config/secret schemas from tool classes, not DB). |
| **completed/latex-rendering.md** | LaTeX formula rendering in the markdown pipeline (KaTeX integration with disambiguation heuristics). |
| **completed/dynamic-tool-discovery.md** | Dynamic tool discovery — ingest external APIs via OpenAPI/Swagger specs, register as tools with credential binding, guided wizard UX. |
| **completed/native-tool-calling.md** | Migrating the research node's tool-calling from text-based JSON parsing to native tool calling via the inference server's API — multi-provider adapter pattern (OpenAI, Anthropic, Responses), per-provider feature flag, hybrid fact extraction, tradeoffs and model considerations. |
| **completed/docker-deployment.md** | Single-container Docker deployment — multi-stage build, FastAPI static file serving, volume mounts, deploy script. |
| **completed/verification-split.md** | Replacing the single verification node with two focused, tool-free steps: research_review (evaluate evidence coverage) and judgment (evaluate conclusion logic). Driven by small-model format mixing problems in the combined verification node. |
| **completed/claim-validation.md** | Adversarial claim validation — enrich citations with source content, flow evidence into evaluation/review prompts, detect overclaims via LLM cross-examination rather than mechanical pattern matching. Includes structural sanity check for supporting fact IDs at research_review. |
| **completed/inference-settings-migration.md** | Moving inference provider configuration and model selection from YAML config into database-backed system settings, with runtime model discovery, encrypted API key storage, and UI management. |
| **completed/conversation-model-selection.md** | Per-conversation intelligence model selection via a header pill + popover, with overrides layered on top of global defaults stored in a dedicated `conversation_models` table. |
| **completed/recall-source-tool.md** | Built-in `recall_source` tool that lets the research model re-read the full stored content of previously-fetched citations during retry cycles. Follows the url_content dedup interception pattern — calls are intercepted in the research loop and synthesized from in-scope citations (no executor call, no budget charge). Always available as a default tool. |
| **completed/application-deployment-readiness.md** | Point-in-time assessment of deployment readiness for family testing. Most issues raised have since been addressed by other completed work. |
