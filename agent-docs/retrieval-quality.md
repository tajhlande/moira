# Retrieval Quality: Consistency and Power of Information Search

> **Status:** Brainstorm / pre-plan. Captures findings and design directions.
> Not yet phased for implementation. Sibling to
> [source-quality-and-verification.md](source-quality-and-verification.md)
> (verification-side) and [planning-freedom.md](planning-freedom.md)
> (planning/orchestration-side); this document covers the retrieval side.
> Not to be tackled on the planning-freedom branch — separate stream.
> The web_source content store and material-class foundation are defined
> and sequenced *here* (moved from summarize-source.md, which now covers
> only the deferred `summarize_source` deep-read tool).

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
- Fetched bodies hydrate the source-content store (see "Source-content
  store" below) as `full`-class material instead of being discarded after
  ranking — the store is a prerequisite for this step, not optional
  plumbing.
- Converts page-level luck into passage-level recall — probably the single
  biggest lever for "returned irrelevant results."
- Structural consequence for the sibling plan: if web_search returns ranked
  passages, the "encourage url_content before extracting factual claims"
  prompt nudge in source-quality-and-verification.md becomes mostly
  obsolete — richer evidence reaches the evaluator structurally. Its
  "investigate the url_content ratio first" note should redirect here.

### Source-content store (web_source) with material classes

Added 2026-09-11; relocated from summarize-source.md so this plan is
self-contained (that doc now covers only the deferred `summarize_source`
deep-read tool). Full page bodies need somewhere to live, and the existing
knowledge tables must not hold them: stuffing 50–100K blobs into
`workflow_runs.knowledge_snapshot`, the citations structure, or step
details would bloat every snapshot, every resume, and every eval capture.

- **Shape:** a separate table (e.g. `source_contents`, keyed by URL hash /
  citation id, with `fetched_at`, `content`, `content_type`, byte size) or a
  content-addressed file store under `data/` — decision open, but either
  way invisible to `knowledge_summary()` and the eval capture layer.
- **Material-class flag (the point of the store):** every stored body
  carries an explicit enum of *what kind of material it is*:

  | class | meaning | today's analog |
  |-------|---------|----------------|
  | `snippet` | search-result excerpt; the page was never fetched | `Citation.depth == "snippet"` (via `_apply_sources`) |
  | `clipped` | fetched, stored as a window (first N chars of the body) | `Citation.depth == "page"` at `CITATION_CONTENT_LIMIT` |
  | `full` | fetched, complete body stored | none — bytes beyond the cap are discarded today |
  | `summary` | model-generated condensation of a parent source, with provenance to it | none — `summarize_source` output lands here, if that tool is built |

  The flag makes "what does the agent actually have" a queryable fact
  instead of something inferred from character counts. The motivating
  evidence is the jazz-run forensics below (`b962d05e`).
- **Serving caps unchanged.** `Citation.content` remains the serving window
  and `recall_source` is unchanged; the storage cap rises to the 50–100K
  order. Full bodies are consumed by passage-level retrieval (chunk + rank)
  and, if it is ever built, the `summarize_source` tool.
- **Hydration on fetch:** any fetch — `url_content` today, passage-level
  retrieval later — deposits the body into the store and upgrades the
  record's class. Acquisition stays a single caller-visible call; the model
  is never asked to chain fetch tools.
- **Scope:** per-run to start. Cross-run reuse (same URL fetched in an
  earlier run) is technically free once the store exists but amounts to
  cross-invocation memory — deferred, like the query playbook.

The class is consumed structurally in every agent-facing view — the next
section.

### Structural source-material flags (know what you're reading)

Added 2026-08-29, motivated by jazz-run forensics (`b962d05e`): 27 of 30
citations were search snippets — pages the agent never fetched — yet the
agent mined and recalled that store as if it had read the material. The
agent cannot tell "I have this page" from "I have a 300-char excerpt of a
search result" unless something tells it, structurally, every time.

- **Consume the material-class flag everywhere a source is shown.** Every
  agent-facing view of a source — research feedback lines, the retry-context
  citation table (extend the existing depth column), recall results, planner
  store views — renders the class from the store above. Never require the
  model to infer material quality from character counts.
- **Build order:** rendering and snapshot serialization work against the
  existing `Citation.depth` field (`snippet` / `page`) before the store
  exists; the four-class enum upgrades it once the store lands. The first
  step ships independently.
- **Flesh-out affordances:** a `snippet`-class source should surface its
  upgrade path inline: `url_content` to acquire the page (clipped/full), and
  `summarize_source` for a directed deep read once that tool exists. "This
  is an excerpt; here is how to get the real thing" beats prompt folklore
  about fetching pages.
- **Known gap to fix when this lands:** `knowledge_summary()` currently
  drops even the `depth` field, so run snapshots can't answer "what did the
  agent actually have" (all 30 jazz citations serialized with no depth).
  Snapshot serialization must carry the class flag.
- Pairs with the recall refusal (snippet-depth citations already refuse
  recall and point at `url_content`) — this generalizes that pattern from
  one tool to the whole agent-facing surface.

### UI parity: render what the schema learns

Added 2026-09-11. Rule for every step in this plan: wherever the work
produces meaningful changes to the knowledge schema or stored
information, those changes are also reflected in the user interface.
Metadata only the model sees is half a feature — inspectability is a
stated platform priority, and the UI is where it lands. The same rule
applies in [summarize-source.md](summarize-source.md) for the metadata
that tool would add.

- **Material class on sources and citations.** The knowledge panel
  (frontend/src/components/KnowledgePanel.vue — Sources list, fact
  citation refs) and the report citation views
  (frontend/src/components/ReportPanel.vue, CitationMarkdown.vue) render
  the class — e.g. a badge distinguishing "the agent cited a search
  excerpt" from "the agent read the page." Users currently cannot make
  the distinction the jazz forensics demanded. The knowledge-panel side
  rides the same fix as the `knowledge_summary()` gap above.
- **Fact-level metadata.** When later steps attach structure to facts —
  query provenance from feedback memory, extraction provenance if
  `summarize_source` is ever built — it renders on the fact rows in the
  knowledge panel, not only in agent-facing context.
- **Retrieval outcomes.** Per-fact recall and query outcomes measured by
  the harness / produced by fan-out become forensics-visible in
  run-detail views once stored.

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
| summarize-source.md | The `summarize_source` *deep-read tool* only (sub-context extraction) — its storage foundation moved here | We build the web_source store + material classes as our source-content foundation step; the deferred tool consumes the store and caches its output back as `summary`-class material |
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
2. **Source-content foundation** — the store + material classes defined
   above, flag rendering in every agent-facing and user-facing view (see
   "UI parity"), and the snapshot serialization fix. Ships in two steps:
   render/serialize the existing `depth` field first, then add the store
   and the four-class enum. Independent of the harness — can proceed in
   parallel with step 1.
3. **Delegated query-writer pass** — cheap, isolated, unit-testable; A/B
   against baseline with the harness.
4. **Per-fact fan-out + templates** — depth-over-breadth trade measured by
   the harness; watch the 10-search ceiling.
5. **Passage-level retrieval** — web_search overhaul; biggest expected
   lever, largest change surface. Requires the step-2 store: fetched pages
   hydrate it as `full`-class material rather than being discarded.
6. **Feedback memory** — within-run outcome memory first; cross-run playbook
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
- Source-content store policy: own table vs content-addressed file store;
  eviction/total-size caps; per-run scope for now (cross-run reuse is
  cross-invocation memory — deferred like the query playbook).

## Deferred

- **Engine fusion / multi-engine fan-out** — Brave is paid and
  deterministic; engine variance is not currently a problem. Revisit only
  if Brave coverage gaps show up in harness recall numbers.
- **Cross-run query playbook** — see sequencing; after the harness.
