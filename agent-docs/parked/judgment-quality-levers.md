# Judgment Quality Levers

Designed 2026-08-27 from judge-note analysis of the three persistent eval
failures. Deliberately held: the planning-freedom Phase 6 decision gate
requires frozen prompt behavior across its batches, so none of these are
implemented. Each lever names its failure, its target location, and its
mechanism. See `progress.md` (2026-08) for the failure-mode analysis that
produced them, and `EVAL_LOG.md` for the batches.

Related but separate: `../retrieval-quality.md` (pre-plan, also unimplemented)
addresses the acquisition-variance half of the jazz failure.

## 1. Distinctiveness test — telescope-mount-cost critique gap

**Failure:** evaluation passes conflated claims — class properties attributed
to the subject (user-side calibration confused with manufacturing overhead;
non-unique features treated as distinctive). Judge note: "conflation of
user-side calibration with manufacturing overhead... verification loop failed
to catch."

**Target:** `evaluation.system` prompt section in
`backend/moira/resources/prompts.md`.

**Mechanism:** add a per-conclusion test: "would this conclusion still hold if
the subject were swapped for another member of its class?" Conclusions that
survive a subject swap don't distinguish the subject and cannot support a
comparative answer. The evaluation prompts already receive the question, goal,
facts, conclusions, and source content, so no payload change is needed — the
rubric just never asks this question.

**Stronger variant (also deferred):** per-conclusion evaluation passes — one
evaluation call per conclusion instead of one per run. Hypothesized in the
2026-08-11 EVAL_LOG note; costlier, unmeasured.  See [grind-mode.md](grind-mode.md).

## 2. Entity-grounding rule — tyranitar-ou partner verification gap

**Failure:** the agent verified the subject's own profile rigorously
(PokeAPI, specialized tools) but recommended partner entities with no
verified facts behind them; synergy claims rested on unverified ground.
Judge scores: tool choice 1, search discipline 1, ability correctness 2,
synthesis discipline 1, verification quality 1.

**Target:** `evaluation.system` prompt (rule form) or the evaluation node
(structural gate form).

**Mechanism (rule form):** "a conclusion naming an entity with no verified
facts cannot be marked verified." **Structural variant:** an entity-grounding
gate in the evaluation node — collect the subjects of verified facts, and
fail/flag conclusions whose supporting entities have zero verified facts.

**Additional note:**  Track additional entities in the knowledge model.
The research step should emit these along with emitted facts. 

## 3. Report status-tiered presentation — jazz-trumpeters recalibration gap

**Failure:** the reviewer correctly downgraded weak conclusions, but the
final report still presented those same conclusions in the main answer with
hedging language. Judge note: "verification loop was the strongest part...
but the final answer still presented those same weak conclusions with hedging
language rather than recalibrating."

**Root cause:** report rules (see the hedging rules in
`report_generation.system`) mandate hedged presentation of `inferred`
conclusions but tier on `derivation` only and ignore conclusion STATUS.

**Target:** `report_generation.system` prompt section.

**Mechanism:** unverified conclusions must not appear in the main answer —
they belong in an insufficient-evidence section or are omitted. Hedging
("likely", "suggests") applies only to verified-but-inferred conclusions.
Prerequisite: jazz also needs acquisition help (verified-fact counts of 2 in
recent batches), so pair with retrieval-quality work.

## 4. Knowledge-model scoring dimension — bench rebalance (deferred by user)

The general rubric grades the report; the knowledge model's quality (fact
accuracy, status honesty, citation fidelity, decomposition coverage) is only
graded for tyranitar's pokemon rubric. The judge already receives the full
knowledge snapshot plus review/evaluation attempts and fact metrics, so an
**additive second rubric for the same judge payload** would make knowledge
quality visible without re-weighting (and thus re-baselining) the general
rubric. Also promote the existing deterministic checks (unsupported count,
dup interceptions, attribution rate) into a small scored dashboard.
Deferred 2026-08-27: "let's not worry about this now, I will pick it up
later."
