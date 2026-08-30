# Grind Mode (parked)

A user-selectable "deeper" research mode: more model calls at deliberately
higher cost, in exchange for per-item scrutiny that a single combined call
cannot deliver. Designed 2026-08-29 in discussion; not started. Related
parked doc: `judgment-quality-levers.md` (this is the delivery vehicle for
its strongest items). Also relates to `../retrieval-quality.md` and
`../hierarchical-decomposition.md`.

## Motivations

1. **Prompt-length returns have collapsed for this model class.** 
   The Qwen 35B workflow model honors short, structural instructions and quietly
   ignores long prose ones — the session record shows candidate-tool order
   ignored until stated as binding, recall-only retry plans despite a soft
   rule, `request_id` dropped until schema-enforced. Every mechanical fix
   (routers, dedup fences, schema augmentation, coalescing) worked first
   try; prose rules needed repetition and enforcement. Adding more rules to
   prompts like `evaluation.system` will have diminishing returns.
2. **The persistent eval failures are attention-dilution failures.** 
   Both named judgment gaps happen when one evaluation call juggles ~4–8
   conclusions:
   - telescope-mount-cost: conflated conclusions passed evaluation
     (class properties attributed to the subject).
   - tyranitar-ou: conclusions naming partner entities with zero verified
     facts passed evaluation.
   One call per conclusion gives each the full context budget.
3. **Independent provenance:** the 2026-08-11 EVAL_LOG note (Q8 quant batch)
   already hypothesized "decomposing the verification tasks into distinct
   sub-tasks for each conclusion." That variant is also recorded as the
   "stronger variant" in `judgment-quality-levers.md` item 1. Grind mode is
   the generalization.

## Relationship to judgment-quality-levers.md

- **Lever 1 (distinctiveness test)** — grind mode is the architecture that
  makes it cheap: "would this conclusion survive a subject swap?" as the
  sole question of a call, with just that conclusion's facts and sources.
- **Lever 2 (entity-grounding)** — becomes input assembly rather than a
  prompt rule: attach "verified facts about entity X: <list or NONE>" to the
  per-conclusion call. The failure becomes glaring instead of forgotten.
- **Lever 3 (report status-tiering)** — orthogonal; still needed, but fed
  richer signal (per-conclusion verdicts with per-conclusion notes).
- **Does NOT fix acquisition variance** (jazz's core problem, vf≈2): that's
  retrieval sampling, addressed by `../retrieval-quality.md`. Two mechanisms,
  two bottlenecks; don't expect evaluation grind to move acquisition-limited
  questions.

## Design sketch

**Mode plumbing:** an effort/mode setting (e.g., `standard | deep`) in
`moira-config.yaml`, selectable per run (UI + eval harness), wired through
`ExecutionState`. Cost flows through the existing budget system — deep mode
is simply more paid calls, visible to the user. Eval batches default to
standard for comparability; deep mode scored as its own column.

**Stage 1 — evaluation grind (build first; smallest diff, directly targets
both named failures):** in the evaluation node, loop over conclusions; one
model call per conclusion, input assembled from: the conclusion, its cited
facts, relevant source excerpts, the verified-fact subjects for its entities
(the NONE case included), and the distinctiveness question. Merge verdicts
into the existing `structured_output` shape so downstream nodes and the
harness need no changes.

**Stage 2 — fact-discovery grind:** per-fact focused research rounds with
strategy-diverse query fan-out. Shares machinery with the
`retrieval-quality.md` per-fact fan-out plan — build them together, not
twice.

**Stage 3 — synthesis grind (hardest, last):** decompose by question aspect.
This is the merge-boundary problem already identified in
`../hierarchical-decomposition.md` (child conclusions vs parent facts,
provenance); unsolved there, unsolved here.

## Costs and caveats

- Answer latency ~1.5–3x. Do not promise parallelism: local gains depend on
  llama-swap batching behavior with a single loaded model.
- More calls = more exposure to single-call failure modes — but JSON repair,
  transient-5xx retries, and continue-on-failure now cover those.
- Subagent pathway: the fan-out/gather/merge-into-state plumbing built here
  is the primitive subagents need; merge semantics transfer to
  hierarchical-decomposition Level 2.

## Validation plan (when picked up)

Run the same questions in standard vs deep; targets are the two named
persistent failures (telescope critique gap, tyranitar partner grounding)
plus verified-fact counts and the knowledge-model scoring dimension
(`judgment-quality-levers.md` item 4) if built by then.

## Open questions

- Mode flag granularity: per-run only, or per-question effort in the UI?
- Whether review (`research_review`) gets the same treatment for facts, or
  evaluation-first results decide.
- Budget guardrail shape for deep mode (separate cap? same budget, warn on
  80%?).
