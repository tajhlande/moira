# Assessment of MOiRA

I've read the knowledge model, the graph & node implementations, the full prompts.md, the architecture doc, and the
roadmap/critiques. Here's my read.

## What is well-designed

**The knowledge model is the strongest primitive in the project.** Facts (`unknown → unverified → verified/contradicted`) and
Conclusions (with `unsupported` as an additional terminal state) with explicit `supporting_fact_ids` and `citation_ids` create a
queryable graph — not just a report with a citation list. That is genuinely rarer than it looks. Perplexity, OpenAI DR,
Claude Research, Gemini DR all produce narrative-with-footnotes; they don't expose a structured fact graph that survives the
run. `Citation.content` being carried alongside the excerpt so review/evaluation can cross-reference is also unusual — most
agents lose the source text after research finishes.

**The research cycle has real architectural discipline.** The two-tier retry (review → planning for gaps; evaluation →
tool_identification for a full reset) with reserved-budget accounting so retries can't starve terminal nodes is thought
through. The structural sanity check that flags conclusions referencing hallucinated fact IDs before spending evaluation
tokens is a nicely-placed cheap check.

**The prompts are unusually strict for the right reasons.** The repeated "a fact is a single observable property, no
conjunctions, no judgment, no superlatives" definition, the ID-discipline instructions ("use EXACT ID, no _partial
suffixes"), and the anti-prior-knowledge language in decomposition ("don't assume facts true because familiar") are all
guarding against real failure modes. The adversarial evaluator prompt with its four-way status rubric (verified / unsupported
 / contradicted / unverified) plus explicit "unsupported ≠ contradicted" distinction is one of the sharpest pieces of prompt
engineering here.

## Where the unique value is thinner than it looks

1. **Tool selection is still probabilistic.** LanceDB retrieval + LLM planning is a more structured version of the same failure
mode the critique document (`research-loop-critique.md`) already flagged: the model can still route to `web_search` when it
shouldn't. The recommendation to make fact-type → tool routing deterministic hasn't been implemented.
2. **Research verifies its own homework.** `research_review` re-reads the citations that `research` already wrote against. Genuine
independent verification would use different tools / different sources — otherwise it's the same model reading the same text
and being asked whether it agrees with itself.
3. **The fragility budget is high.** The many defensive paths — JSON repair, parse retries, phantom-fact cleanup
(`_cleanup_empty_claims`), auto-promoted-then-reverted statuses, `status`-as-fallback-for-`result` — say the primitives depend on
the model's discipline being consistently high. A local 7–14B model won't clear that bar; frontier models don't need the
scaffolding this heavily.
4. **No evaluation harness.** The critique already flagged this and it's still true. Without a fixed test set with metrics
(fact-coverage rate, `web_search` overuse rate, unsupported-conclusion rate, hallucinated-citation rate) vs. a baseline (plain
tool-using agent, OpenAI DR, Claude Research), you cannot know whether the architecture is producing better answers or just
more elaborate ones.
5. **The knowledge doesn't persist.** Every conversation starts from zero facts. On the roadmap under "Memory" but not built.
This is the single biggest missed moat — a general research agent can't accumulate a verified fact base scoped to a user or
project; MOiRA's structure makes this natural.

##  Is this worth pursuing?

**Yes — but the differentiation is currently latent, not realized.** The knowledge model plus adversarial multi-node verification
plus local-first plus OpenAPI-ingested capabilities plus full transparency into every step is a real combination that no
shipping product has. The problem is that today the report the user sees still looks like a report with citations. The unique
 value lives in the graph behind it, and the product doesn't yet make that graph the reason to use MOiRA.

## Highest-leverage next steps

1. **Build the evaluation harness first.** 20–50 fixed research questions, ground-truth fact lists, run against a baseline plain
agent and a frontier DR agent. Measure the 4–5 metrics that map to your reliability claims. Everything else improves faster
once you can see the score change.
2. **Persist facts and citations across conversations, scoped to project.** This is on the roadmap; it's also the single feature
that would meaningfully differentiate from every hosted research agent — accumulated verified knowledge that a new question
can build on without re-verifying.
3. **Deterministic fact-type → tool routing** for the common cases (biological taxonomy, chemistry, geography, market data,
current events). Keep semantic search as a fallback for the long tail. This directly attacks the tool-selection failure mode
the critique flagged.
4. **Cross-fact contradiction detection at the knowledge-model layer, not at review time.** The graph already has everything
needed — before synthesis, flag facts on the same subject that disagree on `value` or `relation`. This is cheap, deterministic,
and exactly the kind of check that hosted agents can't do because they don't have the graph.
5. **Fact classification (verifiable vs. consensus/opinion)** — also on the roadmap. Without it, "unsupported" is a failure
state; with it, opinion questions become a first-class supported mode where success is "range of views represented with
attribution."
6. **Make the fact graph the primary UI artifact, not the report.** The report is the fallback for people who want a paragraph.
Users who trust the answer more than a competing agent will do so because they can see the 12 sub-questions and the evidence
chain. This is a product decision, not a code decision.
7. **Independent verification in `research_review`.**Have it run its own tool calls against different sources, not just re-read
the ones `research` already cited. Otherwise it's the same model checking its own work.

Steps 1 (harness) and 2 (fact persistence) are the ones I'd sequence first. The harness makes every other decision
measurable; the persistence turns a well-architected agent into a product with a moat.

## How to make the harness for evaluation

A tractable shape:

Freeze what you already do. Write down the rubric the frontier model was implicitly applying when it judged the rewrite
better. Four or five criteria, each scorable 1–5 or pass/fail: "conclusions actually follow from cited facts," "facts are
atomic and not judgment-laden," "citations support the claims they're attached to," "critiques identify real weaknesses,"
"the answer addresses the user's actual goal." Whatever you were noticing. This is the rubric — it doesn't need to be
perfect, just consistent between runs.

Three to five questions, stable. Pick ones that have exercised different failure modes for you. The Pokemon strategy
question, one where the current agent used to over-search, one where synthesis used to overreach. You don't need ground-truth
answers — the model is grading against the rubric, not against a key.

A script that runs the questions, dumps the snapshot, hands report + facts + conclusions + citations + critiques to a
frontier model with the rubric, and prints scores. Add the mechanical metrics you get for free from the trace (web_search
calls, unknown-fact count, unsupported-conclusion count, hallucinated-ID hits). Save results per commit so you can diff.

That's it. Building this is a few hours, not a project. It replaces "did that feel better?" with "did the score move?" —
which is what you need to know whether tuning synthesis or second-pass research is paying off.

One caveat worth naming: frontier-model judging is calibrated to itself, not to truth. It will catch regressions in reasoning
quality and grounding discipline reliably. It will miss subtle factual errors where the model doesn't know the domain any
better than your agent does. For the domains you care about most (Pokemon competitive play, wherever else), one hand-graded
pass a month keeps the LLM judge honest.

The harness doesn't have to be good to be useful. It just has to be repeatable.