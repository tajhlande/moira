# Research Agency: Knowledge Model as Workspace, Not Controller

Pre-plan. Not scheduled. Tests whether MOiRA's fixed control flow limits
research quality, and how to give the agent more freedom to act without
losing the structured knowledge model or verification.

| Phase | Description                                                               | Status      |
|-------|---------------------------------------------------------------------------|-------------|
| 0     | External baselines (plain ReAct agent, GPT Researcher) on the eval set    | Not started |
| 1     | Longer inner research loop (spike)                                        | Not started |
| 2     | Discovery tools: agent writes fact needs and leads to the knowledge model | Not started |
| 3     | Per-conclusion evaluation (grind mode Stage 1)                            | Not started |
| 4     | Director step choosing from a fixed menu, acting through scoped sub-loops | Not started |

## Motivation

MOiRA began as a self-hosted alternative to Perplexity-style deep research.
It has become a **fact-checking pipeline with a research step inside it**:
decomposition writes the blanks, research fills them, review and evaluation
check the results, and retries repeat the same steps. Agents that seem to
move with more agency work differently: they look at a result, decide, and
act again after every page. MOiRA does that only inside `research`, capped
at `DEFAULT_MAX_ROUNDS = 3` (`research.py:65`).

Observed effects:

- **The question's shape is fixed before anything is read.** In 7 of 10 runs
  (2026-08-24 batch), research added zero facts beyond decomposition. The
  system finds only what it already knew to look for.
- **Most effort goes to checking, little to searching.** Research takes about
  28% of node time. Each outer pass costs about 5 LLM calls of planning and
  judging per round of search.
- **Decomposition produces more blanks than research can fill.** Only 4–6 of
  roughly 21–29 decomposition facts get any query in a repeat (from the
  retrieval-quality harness), and nothing ranks which facts matter most for
  the answer.
- **The checks reward caution, not curiosity.** Review asks whether facts are
  supported, never what was learned that should change the search. This
  matches the goal-alignment failure mode (listing gaps instead of answering).
- **No node sees the whole investigation.** Leads, hunches and surprises have
  no field in the knowledge model, so they are lost between nodes.

## Assessment

**Keep (this is what makes MOiRA distinctive):**
- The knowledge model: facts, statuses, citations and conclusions as typed,
  inspectable state.
- Verification as a separate gate that the researching agent cannot skip
  or pass on its own.
- Visibility: every action is a typed step shown in the UI.
- Bounded budgets. A 27–35B local model running unconstrained loops tends to
  wander, repeat, or stop early. Frontier agents feel agentic partly because
  frontier models can manage their own process. Removing the scaffolding
  outright would likely produce a worse GPT Researcher.

**Limits quality:**
- Fixed node order with decomposition first. The fact list comes from the
  model's priors and caps what research can find.
- An outer loop too coarse to adapt. Retries replay the same plan with
  feedback about fact support, not about direction.
- A stopping rule tied to filling the given blanks rather than to the
  user's goal.

**Reframe.** The architecture's "deterministic orchestration" currently
covers two jobs. Keep the record strict and deterministic (what was found,
by which action, what status it has, what passed verification). Make the
choice of action flexible: a lab notebook, not a lab protocol.

## Relationship to grind mode

[parked/grind-mode.md](parked/grind-mode.md) found that the 35B workflow
model follows short, structural, schema-enforced instructions and ignores
long prose rules, and that evaluation fails when one call has to weigh 4–8
conclusions at once. That shapes how agency should be delivered here:
**the agent chooses what to do, and each choice runs as a short, scoped
call**, not as one long transcript under one long prompt. The model makes
small decisions often; code builds each call's narrow context.

How the grind stages map onto this plan:
- **Stage 1, per-conclusion evaluation → Phase 3.** Verification must scale
  with what agency produces. More discovered facts mean more conclusions,
  and a single combined evaluation call already loses track at 4–8 of
  them. Evaluation stays **exhaustive and decided by code**: every
  conclusion gets its own call. The agent never chooses what gets verified.
- **Stage 2, per-fact discovery fan-out → Phase 4.** Becomes the director's
  "focus on fact F" action. One difference from grind: the **agent**
  chooses which facts get a focused sub-loop, ranked by how much they
  matter to the goal, instead of code fanning out over every fact.
  Exhaustive fan-out stays as the deep-mode default or fallback. The
  sub-loop shares machinery with retrieval-quality's per-fact fan-out:
  build it once.
- **Stage 3, synthesis by question aspect.** Same unsolved merge problem as
  the director's "split sub-question" action and
  [hierarchical-decomposition.md](hierarchical-decomposition.md). Out of
  scope here.
- **Mode plumbing (`standard | deep`).** The natural home for agency
  budgets. Deep mode means more director steps and sub-loops, paid through
  the existing budget system.

## Phases

### Phase 0 — Baselines
Run a minimal ReAct loop and GPT Researcher, using the same model and
SearXNG, on the eval questions, and score both with the existing judge (see
[comparable-projects.md](comparable-projects.md)). This shows whether, and
on which questions, the constraints cost quality. Gate for Phases 2–3.

### Phase 1 — Longer inner loop (spike)
Inside one research invocation, without changing the outer graph:
- Raise the turn cap (about 3 → 10–15) and limit tool calls per research
  step (about 20–30). Existing budget accounting still applies.
- Stop based on the goal ("you can answer the user goal, or more search
  won't change the answer"), with a code-enforced minimum and maximum.
- Compact older turns: swap raw tool output for the extracted facts plus
  citation ids. `recall_source` covers re-reading.

Measure: turns actually used, share of verified facts not present in
decomposition, verified facts per run, judge score, repeated queries, and
time and tokens per run. If the model still stops at 2–3 turns, the problem
is the stopping rule and prompt framing, not the cap. The grind-mode
finding predicts that quality drops in later turns as the transcript and
rule load grow. If so, that's evidence for Phase 4's scoped sub-loops, not
a reason to drop agency. Phase 1 is a cheap probe, not the end state.

### Phase 2 — Discovery tools
The prompt already asks the agent to "Identify new wanted facts", but it
rarely does: this is a minor field in the output JSON. Native-tool models
act through tools, so add `add_fact_need(subject, fact_needed, why)` and
`note_lead(...)`. Both write to the knowledge model and render as steps.
Let `add_fact_need` create a request id, so calls that chase new leads are
attributed and scored like planned ones. Add a new `lead` / `open_question`
entity to the knowledge model.

### Phase 3 — Per-conclusion evaluation
Build grind mode Stage 1 as designed: one evaluation call per conclusion,
with input assembled from the conclusion, its cited facts, source excerpts,
and the verified facts for each named entity (including NONE), plus the
distinctiveness question. Verdicts merge back into the existing
`structured_output` shape. This comes before Phase 4 because the
verification gate has to keep pace with a larger knowledge model. Its
value doesn't depend on Phase 0's result.

### Phase 4 — Director step with scoped sub-loops
Replace some fixed edges with a director step that picks from a fixed menu
of actions: focus on fact F (a short per-fact research sub-loop, grind
Stage 2), read source S in depth (see
[summarize-source.md](summarize-source.md)), split sub-question Q (see
[hierarchical-decomposition.md](hierarchical-decomposition.md)), synthesize,
request verification, stop. The director call sees a compact view of the
knowledge model (fact statuses, open leads, budget), not raw tool output.
Each action runs with its own narrow context and writes back to the
knowledge model. Choices are schema-enforced and logged. Report generation
stays gated on verification.

## Open questions
- Does Phase 0 show a quality gap large enough to justify Phases 2 and 4?
- Does `research_review` also get per-fact calls, or do Phase 3's results
  decide that (open in grind mode too)?
- How large a context can the target local models sustain over 15 turns
  after compaction?
- Does the director step replace planning, or sit above it?
- How does this change the architecture's "Avoid Pure Autonomous Agent
  Behavior" and "Prefer Deterministic Scaffolding" sections? Proposed
  answer: those sections should govern record-keeping and verification
  gates, not the order of actions.
