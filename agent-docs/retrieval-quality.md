# Retrieval Quality: Consistency and Power of Information Search

> **Status:** Brainstorm / pre-plan. Captures findings and design directions.
> Not yet phased for implementation. Sibling to
> [source-quality-and-verification.md](source-quality-and-verification.md)
> (verification-side) and [planning-freedom.md](planning-freedom.md)
> (planning/orchestration-side); this document covers the retrieval side.
> Not to be tackled on the planning-freedom branch — separate stream.

## Problem

Batch-to-batch eval noise is dominated by variance in information retrieval,
not by the pipeline behaviors we have been tuning (planning, extraction,
verification). The information needed to answer eval questions is available
to be had — the searching doesn't find it, and *which* runs find it varies
run to run.

### Evidence (2026-08-24/25 batches, Qwen3.6-35B-A3B workflow model)

| Observation | Detail |
|-------------|--------|
| Same-question score swings with no pipeline change | future-nostalgia verified facts swung 12 → 1 between batches; water 20 → 0; jazz flipped 15 → 19 on retrieval luck |
| Model knows queries are missing | water-blood-pressure (08-24): 10 searches, 4 rounds, **0 of 15 facts extracted**; model's own mid-run words: *"The queries are returning irrelevant results."* |
| Query style regresses to keyword-stuffing | water run queries were 8–12-word academic phrases: "acute water ingestion vasopressin AVP concentration adults", "water consumption measurement food frequency questionnaire NHANES dietary assessment" |
| Near-zero cache hit rate | web_search cache hits ≈ 0 across runs — the model never converges on canonical query phrasings; it re-rolls different terms every run |
| Engine variance ruled out | Brave Search is now the paid, deterministic engine. Variance is entirely in the query itself, not the engine. |

The retrieval lottery obscures subtler signals (e.g., fact claims that don't
match the wanted fact): rubric movement can't be attributed to extraction or
planning changes while query quality re-rolls every run.

### Why queries are bad

Query writing is a low-status job inside an overloaded context: the same
35B model, in the same research prompt that also does extraction, source
recording, and next-round planning, freeform-generates query strings. Nothing
structures the query space. The result is sampling variance — sometimes a
colloquial phrasing that works, sometimes keyword-stuffed phrases that miss.

## Failure-mode decomposition

Three distinct problems hide under "bad search." They need different fixes:

1. **Vocabulary mismatch** — formal `fact_needed` language vs. how sources
   actually phrase things. Keyword-stuffed queries amplify it. Classic IR
   problem; the standard fix is pseudo-relevance feedback (reformulate using
   the retrieved corpus's own terminology).
2. **Query-quality sampling variance** — the model samples a phrasing per
   fact per run from an unstructured space; hit rate is a lottery.
3. **Page-level luck** — the needed fact may be in result #4's page but not
   its snippet, so it is never seen (url_content usage is sparse and
   optional).

## Strategy

Two complementary strategies, applied together:

1. **Replace accidental variance with deliberate diversity.** Structure the
   query space per fact: a small set of variants in *distinct registers*
   (technical term, natural-language question, site-scoped). Coverage by
   design instead of by luck.
2. **Make retrieval robust to any single mediocre query.** Per-fact
   multi-query fan-out: issue all variants, merge/dedup results. A fact
   sinks only if *every* register misses — this averages the lottery
   instead of hoping to win it. Costs more searches per fact; pairs with
   budget discipline (planning-freedom Phase 4) and existing URL dedup.

Plus a feedback loop: when round-1 results are irrelevant, reformulate using
vocabulary extracted from whatever snippets did come back rather than
re-rolling another guess.

## Proposed techniques

### Measurement first: retrieval-isolation harness

Before building, make "most of the noise" a number we can regress-test:

- Fixed question set; run decomposition → query generation → web_search only
  (Brave, deterministic); no synthesis/evaluation.
- Score **per-fact recall@k** (did retrieved content contain the needed
  fact?) and **queries-per-resolved-fact**.
- A/B current freeform queries vs. templated vs. fan-out on the same
  decomposition output.
- Side benefit: disambiguates "never retrieved the source" from "retrieved
  it and ignored it" (the tyranitar-ou failure in
  source-quality-and-verification.md) — downstream these look identical.
- Also emits the url_content : web_search ratio that
  source-quality-and-verification.md's open questions ask for.

### Query generation: delegated query-writer pass

A dedicated, narrow generation step — the "query writer" — separate from the
research prompt:

- Input: `fact_needed`, subject, queries already tried, snippets seen.
- Output: 2–3 short queries in deliberately different registers: technical
  term, natural-language question ("why are X expensive"), site-scoped
  (`site:` for expected source types).
- Narrow enough for a small, fast model — keeps the heavy research model out
  of string-crafting entirely and makes query generation independently
  testable (feed a fact, assert register diversity + length discipline).

### Fact-type query templates

Registers selected by fact type, reusing the source-type taxonomy that
source-quality-and-verification.md defines (`reference` / `editorial` /
`community` / `commercial`):

| Fact type | Registers to try | Expected source type |
|-----------|------------------|----------------------|
| Numeric / spec | spec sheet, product review | `reference`, `commercial` |
| Causal / medical | scholarly phrasing, review-article terms | `editorial` |
| Cost / comparative | forum/review, "vs", colloquial question | `community`, `commercial` |
| Event / historical | news register, timeline terms | `editorial` |

Retrieval *targets* the taxonomy; verification *weights* it. One vocabulary,
two consumers — define it once so the plans don't diverge.

### Per-fact multi-query fan-out

Issue all generated variants for a fact, merge and dedup results. Raises
per-fact hit probability sharply (fact sinks only if every register misses).
Watch search budget: 10 web_search calls per run is the current observed
ceiling — fan-out trades breadth (fewer facts per round) for depth
(higher hit rate per fact). The harness measures whether the trade pays.

### Passage-level retrieval (web_search overhaul)

The heaviest intervention, aimed at page-level luck:

- Fetch top-N results, chunk pages, rank chunks against `fact_needed`
  (local BM25 minimum; embeddings optional later), return best passages
  with source IDs.
- Converts page-level luck into passage-level recall — probably the single
  biggest lever for "returned irrelevant results."
- Structural consequence for the sibling plan: if web_search returns ranked
  passages, the "encourage url_content before extracting factual claims"
  prompt nudge in source-quality-and-verification.md becomes mostly
  obsolete — richer evidence reaches the evaluator structurally. Its
  "investigate the url_content ratio first" note should redirect here.

### Within-run feedback (mechanical, not prompt-hope)

- **Query outcome memory**: queries that produced cited facts anchor future
  phrasing within the run; zero-yield queries force a register change.
- **Pseudo-relevance feedback**: on retry, extract domain vocabulary from
  whatever snippets did return and reformulate with the corpus's own terms.
- **Cross-run query playbook** (later): persist successful query→fact
  mappings and suggest them in future runs — a learned playbook instead of
  a cache. The existing web_search cache stores exact-query results; the
  playbook stores *what kind of phrasing worked for what kind of fact*.

## Relationship to existing plans

| Plan | Boundary | Bridge |
|------|----------|--------|
| planning-freedom.md | Query *discipline* (dedup, coverage-driven rounds — its Phase 4) | Fan-out budget interplay; request-attribution tells the query writer which requests failed |
| source-quality-and-verification.md | Source *weighting* after retrieval | Shared source-type taxonomy (its §"Minimal useful taxonomy" feeds our fact-type templates); passage retrieval subsumes its url_content guidance; harness answers its url_content-ratio open question |
| goal-alignment-and-research-effectiveness.md | Synthesis/evaluation/report-side inference rules | None direct — upstream/downstream |

Inherited principle from source-quality-and-verification.md: **mechanical
rules based on structured data beat nuanced judgment on a mid-grade model.**
That is equally the argument for mechanical query discipline (fan-out,
templates, dedup, feedback memory) over prompt-hoping for better freeform
queries.

## Sequencing (proposal, not commitment)

1. **Retrieval-isolation harness** — measure per-fact recall and
   queries-per-fact on current freeform queries; establishes the baseline
   number the rest of the work regresses against.
2. **Delegated query-writer pass** — cheap, isolated, unit-testable; A/B
   against baseline with the harness.
3. **Per-fact fan-out + templates** — depth-over-breadth trade measured by
   the harness; watch the 10-search ceiling.
4. **Passage-level retrieval** — web_search overhaul; biggest expected
   lever, largest change surface.
5. **Feedback memory** — within-run outcome memory first; cross-run playbook
   later, after the harness exists to validate it.

## Open questions

- Fan-out vs. the 10 web_search calls-per-run ceiling: is depth-over-breadth
  budget-positive at eval time? (Harness answers.)
- Should the query-writer be a separate small model, or a separate cheap
  prompt on the same model? (Latency/cost vs. quality trade — measure both
  in the harness.)
- Does passage ranking need embeddings, or is BM25 against `fact_needed`
  sufficient? (Try BM25 first; it's local and deterministic.)
- Where does the query playbook persist (per-conversation, per-database)?
  Cross-invocation memory is also deferred in sibling plans.

## Deferred

- **Engine fusion / multi-engine fan-out** — Brave is paid and
  deterministic; engine variance is not currently a problem. Revisit only
  if Brave coverage gaps show up in harness recall numbers.
- **Cross-run query playbook** — see sequencing; after the harness.
