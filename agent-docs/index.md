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

## Core — Feedback Iteration (in `core/feedback-iteration/`)

Architecture review cycle: critique, adjustments, implementation feedback, and responses.
These documents represent moment-in-time reviews of core documents, typically by alternative LLMs.

| Document | Description |
|---|---|
| **core/feedback-iteration/architecture-critique.md** | External review of the architecture document, identifying gaps and risks. |
| **core/feedback-iteration/architecture-adjustments.md** | Planned changes to the architecture in response to the critique. |
| **core/feedback-iteration/implementation-feedback.md** | Review of the implementation plan against the (adjusted) architecture and critique. |
| **core/feedback-iteration/implementation-response.md** | Decisions and remedies responding to the implementation feedback. |

## Feedback Iteration (in `feedback-iteration/`)

Standalone critiques of specific plans.
These represent moment-in-time critiques of specific design and implementation plans, 
typically by alternative LLMs.

| Document | Description |
|---|---|
| **feedback-iteration/tool-secrets-critique.md** | External review of the tool secrets and spec plan, identifying design improvements. |

## Active Plans

These are plans for active or future development that have not been started or
have not been fully completed yet. 

| Document | Description |
|---|---|
| **conversation-state.md** | Conceptual data model of a conversation (Section 0) and complete inventory of every data element across all implementation layers. |
| **agent-craft.md** | Running notes on prompt tuning, model behavior observations, and the non-structural factors that make the agent trustworthy. |
| **two-pass-discovery.md** | Plan for restructuring tool discovery to run before and after planning so plans are tool-aware. |
| **tool-secrets-and-spec.md** | Plan for encrypted secrets storage on tools and spec-from-implementation (config/secret schemas from tool classes, not DB). |
| **tiptap-input.md** | Plan for replacing the plain-text input with TipTap rich-text editor. |
| **kagi-web-search-plan.md** | Plan for the Kagi web search tool — first tool to use the credential store, using the Kagi Search API with Bearer auth. |
| **latex-rendering.md** | Plan for adding LaTeX formula rendering to the markdown pipeline (KaTeX integration with disambiguation heuristics). |
| **context-management.md** | Plan for adaptive context window management — auto-detect limits, proactive tool output capping, evidence truncation, and progressive message trimming. |
| **dynamic-tool-discovery.md** | Design for dynamic tool discovery — ingest external APIs via OpenAPI/Swagger specs, register as tools with credential binding, guided wizard UX. |
| **native-function-calling.md** | Design for native function calling — structured tool parameters to LLM APIs, per-model text-based vs. native selection, tradeoffs and model considerations. |
| **docker-deployment.md** | Plan for single-container Docker deployment — multi-stage build, FastAPI static file serving, volume mounts, deploy script. |
| **application-deployment-readiness.md** | Assessment of what is needed to deploy this app to family members for testing. |

## Completed (in `completed/`)

These documents describe designs or work that has been completed
and they are now archived. Design and implementation docs for specific
features and functionality should be moved into this directory as those 
projects are completed.

| Document | Description |
|---|---|
| **draft-retry-routing.md** | Routing verification failures directly to draft synthesis (bypassing research) for synthesis-specific problems (cases 7-8). |
| **run-persistence-reconnect.md** | Decoupling graph runs from HTTP requests, incremental persistence, and SSE stream reconnection. |
| **phase3-ui-prep.md** | Phase 3 preparation: multi-view navigation framework, tool catalog UI, and standard tool definitions. |
| **verification-outcomes.md** | The 11 verification outcome cases the model can return, grouped into accept/retry/error with retry guidance. |
| **stop-and-resume-plan.md** | Stop/resume feature — dual mechanism (task cancel + interrupt), stopped step persistence, UI controls. |
| **inference-metrics-plan.md** | Workflow steps normalization and inference token tracking — per-node token counts and timing persisted in `workflow_steps`. |
| **tool-metrics-plan.md** | Tool call metrics — rolling counters with hourly buckets, call_type, latency min/max/sum. |
| **state-management-cleanup.md** | Migration plan to unify frontend/backend run state around a canonical snapshot model and remove live-vs-persisted UI duplication. |
