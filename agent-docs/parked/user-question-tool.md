# user_question Tool (Parked)

> **Status:** Parked — never built. Retained as a candidate for a future
> interactivity phase. Recorded 2026-09-13.

## What it was

A standard tool letting the workflow model ask the user a follow-up
question mid-run — to gain clarity about the research question or how to
formulate an answer. The question is posed in multiple-choice format
(A/B/C/D options the UI can render for easy response) with a free-text
option. Both the question and the user's answer are appended to the
prompt for the graph step, and the step re-run. Originally specified as
part of core Phase 3 (implementation-plan.md:172); full requirements live
in [core/standard-tools.md](../core/standard-tools.md) §"User question
tool".

## Why parked

Two unresolved problems (per project decision, 2026-09-13):

1. **No UX integration story.** The workflow runs headless and
   asynchronously (research loop, retries, streaming events); a tool that
   blocks on human input doesn't fit any current surface. It needs a
   pause/resume mechanism in the workflow engine plus a conversation-UI
   affordance (inline question card with options), neither designed.
2. **Interactivity breaks benchmarking.** Eval runs are unattended; a
   tool that requires a human answer stalls the run. Benchmarks would
   need a scripted-answer mode, which is extra machinery for a feature
   with no user demand yet.

## Current state (intentional, harmless)

- `moira/tools/standard.py` still defines the tool (`user_question`,
  implementation `moira.tools.builtin.user_question.UserQuestionTool`) so
  the definition isn't lost; the module `tools/builtin/user_question.py`
  does not exist.
- The DB row is disabled (`enabled = 0`), so it is never offered to the
  model.
- `init_services` logs "Cannot resolve tool implementation
  'moira.tools.builtin.user_question.UserQuestionTool'" on startup —
  known noise, judged acceptable.

## Revival notes

- Precondition: a pause/resume path in the workflow engine (compare how
  `summarize_source`'s sub-context calls would blur tool/node lines) and
  a UI affordance in the conversation view.
- Benchmark story needed: scripted answers for eval (e.g. a canned
  answer provider, or skip-and-log).
- The STANDARD_TOOLS entry and disabled DB row mean wiring it up later is
  mostly writing the module + enabling the row.
