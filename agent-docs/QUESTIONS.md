# Open Questions

Design and architecture questions that need user input before or during implementation.

Questions are removed from this file once their answers are reflected in the
relevant plan documents and code.

---

## Budget-exhausted reports lose all synthesis work

**Discovered during:** Claim validation Phase 4 review (run
`42fe5db2-721c-4c78-b8be-1850e6cb6217`).

**Problem:** When the workflow runs out of budget before evaluation runs,
`report_generation` receives:

- `verified_facts`: whatever `research_review` verified — populated.
- `verified_conclusions`: **empty** — evaluation never ran to verify them.
- Conclusions with status `"unverified"` fall through every bucket in
  `report_generation.py:180-185`. They are completely invisible to the
  report prompt.

This means the synthesis node's work (conclusions, reasoning,
supporting_fact_ids) is silently discarded. The report-generation model
must re-derive an answer from raw verified facts, duplicating synthesis
effort and losing the structured reasoning chain.

In the examined run, the model still produced a 3061-char answer, but it
was essentially re-synthesizing during report generation rather than
drawing on synthesis's output.

**Scope:** This is pre-existing behavior, not caused by the claim
validation changes. Phase 7's `omitted_conclusions` side channel will
surface `"unsupported"` conclusions, but `"unverified"` conclusions remain
invisible to the report prompt in all cases (not just budget exhaustion).

**Question:** Should `"unverified"` conclusions be surfaced to the
report-generation prompt (e.g., in a separate "unverified conclusions"
section with caveats), or should this be handled differently (e.g.,
always run evaluation even with minimal budget, or pass conclusions to
report_generation regardless of verification status)? This is arguably a
separate issue from claim validation but intersects with Phase 7's
report-handling work.

---

## Evaluation harness — open questions

**Discovered during:** planning for
[`evaluation-harness.md`](./evaluation-harness.md).

### Stable question set beyond the Tyranitar canary

The harness ships 4 starter questions (see `evaluation-harness.md` Iteration 3).
`tyranitar-ou` is fixed (the existing canary). The other three need user
input — they should be questions that have actually exercised distinct
failure modes:

- `oversearch-bait` — a question where the agent historically burned
  `web_search`.
- `synthesis-trap` — a question where individually-true facts tempt an
  unsupported conclusion.
- `multi-entity` — a question requiring many named entities with
  interacting facts.

**Question:** Which real questions from past MOiRA usage should fill these
slots? Placeholder text will land with Iteration 3; user picks the final set.