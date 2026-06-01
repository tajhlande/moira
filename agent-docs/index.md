# Agent Documentation Index

| Document                        | Description                                                                                                                        |
|---------------------------------|------------------------------------------------------------------------------------------------------------------------------------|
| **architecture.md**             | System goals, component structure, persistence strategy, and implementation guidelines for the MOiRA platform.                     |
| **architecture-critique.md**    | External review of the architecture document, identifying gaps and risks.                                                          |
| **architecture-adjustments.md** | Planned changes to the architecture in response to the critique.                                                                   |
| **implementation-plan.md**      | Phased build plan — Phases 1–2 complete, Phase 3 in progress. Updated to reflect current implementation state.                     |
| **implementation-feedback.md**  | Review of the implementation plan against the (adjusted) architecture and critique.                                                |
| **implementation-response.md**  | Decisions and remedies responding to the implementation feedback.                                                                  |
| **conversation-state.md**       | Conceptual data model of a conversation (Section 0) and complete inventory of every data element across all implementation layers. |
| **user-experience.md**          | What the user sees and does — the intended UX for the research cycle, continued conversation, and sidebar.                         |
| **verification-outcomes.md**    | The 11 verification outcome cases the model can return, grouped into accept/retry/error with retry guidance.                       |
| **agent-craft.md**              | Running notes on prompt tuning, model behavior observations, and the non-structural factors that make the agent trustworthy.       |
| **standard-tools.md**             | Descriptive list of built-in tools that should always be available to the agent.                                                   |
| **two-pass-discovery.md**         | Plan for restructuring tool discovery to run before and after planning so plans are tool-aware.                                    |
| **run-persistence-reconnect.md**  | Plan for decoupling graph runs from HTTP requests, incremental persistence, and SSE stream reconnection.                           |
| **tiptap-input.md**               | Plan for replacing the plain-text input with TipTap rich-text editor.                                                              |
| **phase3-ui-prep.md**             | Phase 3 preparation: multi-view navigation framework, tool catalog UI, and standard tool definitions.                              |
| **tool-secrets-and-spec.md**      | Plan for encrypted secrets storage on tools and spec-from-implementation (config/secret schemas from tool classes, not DB).        |
