# Hierarchical Problem Decomposition

Pre-plan / brainstorm for recursively breaking hard questions into smaller
atoms, researching those, and assembling the resulting facts and conclusions
into an organized whole. This is a **fundamental architectural change** to the
current single-agent, one-graph design — and orthogonal to the
[`planning-freedom`](planning-freedom.md) branch's intent (evidence requests
within one research loop). Nothing here is scheduled; this document exists to
think it through before deciding whether and how to pursue it.

Related roadmap line item: "Sub-agents and parallel research branches"
(roadmap.md, Major Capabilities).

## Motivation

- **Flat decomposition is a ceiling.** Run data (2026-08-24 batch, 10 runs):
  median decomposition produces ~8 facts; in 7 of 10 runs research added
  *zero* new facts — the decomposition output is the final fact set. Rich
  answers are capped by what one flat enumeration produces.
- **Richness targets.** A more robust fact base — 2-3 dozen organized facts
  rather than a handful — cannot come from asking a mid-grade model to manage
  one giant flat list. It has to come from structure.
- **Hard questions have structure the flat model can't express.** E.g., a
  trade-policy question decomposes into genuinely different causal strands
  (input costs / consumer prices / employment / retaliation) that each carry
  their own evidence needs. A flat fact list flattens that structure and
  loses it.
- **Minimal-cover bias is systemic.** Every stage (decomposition "enumerate
  the specific data points needed," budget-constrained planning
  prioritization, "one well-chosen search per fact," review's
  sufficient-to-answer routing) optimizes for the smallest answering set.
  Hierarchy is a way to want more facts without fighting each stage.

## Core idea

Hard questions are recursively broken into sub-questions ("atoms"), each
small enough to research with the existing loop. Each atom produces verified
facts and labeled conclusions. The parent assembles child outputs into a
larger, somewhat more organized set of facts and conclusions that together
try to answer the original question.

```
Question
  └─ sub-question A                (one research loop each)
  │    ├─ facts, conclusions
  │    └─ sub-sub-question A1      (recursion depth TBD)
  └─ sub-question B
       ├─ facts, conclusions
       └─ merge → parent synthesis → parent evaluation → report
```

## The merge boundary is the hard problem

Splitting is the easy half. The unsolved design question is how child
outputs compose into the parent knowledge model. Options identified so far:

**Option 1 — child conclusions become parent facts.**
- Pro: parent synthesis stays small (reasons over child summary-facts, not
  raw child facts).
- Con: status laundering — a child's `inferred` conclusion rolled up as a
  parent fact carries what epistemic status? If "unverified," it looks weak
  despite being evaluated in the child; if "verified," it overstates.
  Derivation metadata must survive the rollup.
- Con: citation chains lengthen (parent conclusion → child conclusion →
  child fact → citation) and the UI/report must render that legibly.

**Option 2 — children emit verified facts only; parent re-synthesizes.**
- Pro: one epistemic standard at every level; parent conclusions cite child
  facts directly; contradiction handling stays in one flat fact space per
  level.
- Con: child synthesis work is discarded (the budget-exhaustion problem in
  QUESTIONS.md, but by design); parent synthesis becomes large and expensive
  as fact count grows.

**Option 3 — hybrid: verified facts roll up flat; child conclusions roll up
as first-class conclusions with provenance ("from branch A's evaluation").**
- Pro: preserves the reasoning chain; parent evaluation can adjudicate
  branch conclusions against each other (this is where cross-branch
  contradictions get caught).
- Con: the knowledge model needs a provenance concept it doesn't have today.

None decided. **Do not implement past Level 0 before choosing.**

## What hierarchy does NOT fix

Honest scoping, from the 08-24 forensics: decomposition was *good* — queries
and retrieval were the failures (duplicates, vague bundles, cap exhaustion).
Hierarchy improves organization and richness, not retrieval. Its payoff is on
questions structurally harder than the current eval set. If adopted, it is a
complement to (not a substitute for) the planning-freedom phases.

## Staging (cheapest first)

### Level 0 — prompt-only hierarchical decomposition
- Decomposition prompt asks the model to organize facts into **clusters**
  with per-cluster sub-goals (e.g., "input costs strand," "employment
  strand"), each cluster listing its facts.
- Research loop stays flat and single; clusters are cosmetic organization
  (displayed to planning, rendered in UI).
- **Tests:** does the model decompose deeper into structured clusters? Does
  fact count rise toward 2-3 dozen on hard questions? Does review/evaluation
  reasoning improve with organized input?
- Cost: prompt-only. Reversible.

### Level 1 — one graph, map-reduce
- Each cluster runs its own research loop (parallel research, per-cluster
  retry) inside the one graph.
- Single shared synthesis/evaluation over the merged fact space — flat merge
  (Option 2 semantics) as the default merge boundary for the first cut.
- **Tests:** does per-cluster research improve resolution rates vs one flat
  loop at equal budget? Does parallelism help wall-clock without quality
  loss? How bad is cross-cluster duplication, and does URL/query dedup
  across branches suffice?
- Cost: significant graph rework; budget model must split per cluster.

### Level 2 — true recursion / subagents
- Sub-questions spawn sub-graphs with their own budgets; the parent consumes
  verified facts + labeled conclusions (merge per whichever option is chosen
  above).
- Depth limits, cycle detection, and global budget governance required.
- This is the roadmap's "Sub-agents and parallel research branches."

## Design questions to resolve before Level 1+

1. **Merge semantics** — choose Option 1/2/3 (or a refinement). This is the
   gating decision; see QUESTIONS.md entries.
2. **Budget allocation** — per-branch budgets vs a shared pool; what happens
   when one branch exhausts early; does evaluation-level retry re-enter
   finished branches?
3. **Cross-branch duplication** — two branches researching overlapping facts
   is the multi-branch version of the duplicate-query problem. Shared
   fetched-URL registry? Shared query cache? Semantic fact dedup at merge?
4. **Contradiction across branches** — where is it detected (merge-time
   evaluation pass? per-branch?) and what does downgrade look like across
   provenance chains?
5. **Depth and breadth limits** — max recursion depth; max branches; when is
   a question "atomic enough"?
6. **UI/persistence** — workflow_steps and the structured output renderer are
   flat lists today; branch-aware runs need a tree presentation. run
   persistence/reconnect assumptions may need revisiting.
7. **Eval strategy** — hierarchy changes cost per run substantially; the
   knowledge-efficiency metrics (verified facts per search, per tool call)
   become the fair comparison basis against flat runs.

## Prior art and differentiation

Map-reduce subagent research is the crowded space catalogued in
[`comparable-projects.md`](comparable-projects.md) (GPT Researcher, STORM,
open deep-research reproductions). The differentiating version for MOiRA is
**hierarchical verified knowledge assembly**: branches produce verified fact
clusters that roll up through the same evaluation discipline, preserving
traceability from every parent conclusion down to original citations. That —
not "spawn subagents" — is the angle to protect in any design.

## Relationship to current work

- Orthogonal to `planning-freedom` (which keeps one loop and improves what
  happens inside it). Level 0 could even ride on the planning-freedom
  architecture unchanged.
- The candidate-expansion mechanical template idea (auto-generate per-
  candidate fact sets when research discovers candidates — discussed in the
  08-24 fact-count analysis) is a flat-pipeline enrichment that Level 0
  could subsume; don't build both blindly.
- The budget-exhausted-conclusions question in QUESTIONS.md (unverified
  conclusions invisible to report_generation) overlaps with Option 1's
  status-laundering problem and should be settled with it.
