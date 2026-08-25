# Open Questions

Design and architecture questions that need user input before or during implementation.

Questions are removed from this file once their answers are reflected in the
relevant plan documents and code.

---

## Planning freedom — open questions

**Discovered during:** writing
[`planning-freedom.md`](./planning-freedom.md) (retroactive plan for the
`planning-freedom` branch).

### Should fallback cascade behavior ever be mechanically enforced?

The `fallback` flag on `EvidenceRequest` is planning-level *cascade
permission* — advisory only. Nothing verifies the researcher actually tries
the next candidate tool before falling to web_search. Phase 5 will measure
cascade behavior; if cascades don't happen, the options are prompt
strengthening (cheap, unreliable) or code-level enforcement (e.g., budget
gating web_search until preferred candidates are tried or refused).

**Question:** If measurement shows cascades aren't happening, enforce
mechanically or accept advisory semantics?

---

## Hierarchical decomposition — open questions

**Discovered during:** brainstorming
[`hierarchical-decomposition.md`](./hierarchical-decomposition.md).

### Merge semantics: how do child conclusions compose into the parent?

The gating design decision for any hierarchical/multi-branch research
(Options 1/2/3 detailed in the plan doc): child conclusions as parent facts
(status laundering risk), verified-facts-only rollup (discards child
synthesis), or hybrid with provenance (needs a concept the knowledge model
doesn't have). Overlaps with the budget-exhausted-conclusions question below
— settle together.

**Question:** Which merge semantic, and does `Conclusion` need a provenance
field to support it?

### Budget allocation across branches

Per-branch budgets vs shared pool; early-exhaustion handling; whether
evaluation-level retry re-enters finished branches.

**Question:** Decide before Level 1 (map-reduce) work begins.

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