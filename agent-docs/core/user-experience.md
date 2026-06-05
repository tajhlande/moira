# MOiRA User Experience

This document describes the intended user experience for MOiRA. It is a companion to the architecture document — where the architecture doc specifies *how* the system works, this doc specifies *what the user sees and does*.

The primary audience is developers building or extending the UI and agent workflows. It should remain the source of truth for UX decisions and can be referenced when evaluating feature requests or design changes.

---

## Core Research Cycle

A user types a research question into the chat interface and presses Enter (or clicks Send). The system then:

1. **Creates a conversation** — a new titled thread appears in the sidebar
2. **Runs the research workflow** — the user sees a live status indicator showing the current stage (Planning, Discovering Tools, Selecting Tools, Researching, Summarizing, Drafting, Verifying, Generating Report). The budget remaining is visible throughout.
3. **Produces a research report** — the report appears in the chat area, containing:
   - The answer to the question
   - Citations and sources
   - Supporting evidence
   - Critiques and limitations
   - Any unverified claims (if the budget was exhausted before verification completed)
   - Total budget consumed

The user can expand tool call details to see which tools were used, what they returned, and how long they took. If verification failed and retried, the retry count is visible.

The conversation title can be renamed at any time by clicking the pencil icon next to it in the sidebar.

---

## Continued Conversation

After receiving a research report, the user can ask follow-up questions within the same conversation. This is the primary multi-turn interaction pattern.

When the user sends a follow-up message:

1. **Prior questions and reports are carried forward** — the planning node receives the answer from the previous research report as context. This allows the agent to build on its prior work rather than starting from scratch.
2. **A new research cycle runs** — the full workflow executes again (planning through report generation) with the new question informed by the prior report.
3. **A new report is produced** — the follow-up report appears in the conversation, building on the prior findings.

The conversation sidebar shows the full history of conversations that each contain messages and reports. Each report is a self-contained research result, but the agent has enough context from the prior report to do coherent follow-on work.

