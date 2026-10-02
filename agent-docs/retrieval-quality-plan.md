# Retrieval Quality: Implementation Plan

> **Status:** In progress — Phase 3 gate run complete (2026-09-18: null
> macro result; register enforcement extracted as Phase 4). Phases 1,
> 2a, 2b complete (milestone baseline:
> full sweep, 2026-09-14). Implements the sequencing in
> [retrieval-quality.md](retrieval-quality.md), which stays the
> design-direction document (evidence, failure modes, rationale). This
> document is the build plan: phases, code touchpoints, tests, gates.
> `summarize_source` itself is **not** in scope (see
> [summarize-source.md](summarize-source.md)); only the shared
> source-content foundation (Phase 2) is.

> **Superseded sequencing (2026-10-01):** this plan will not be completed
> as written. Phases 1–3 stand as done (the query-writer stays behind its
> default-off flag). Phases 4, 5 and 7 are paused: they improve queried
> recall for request-attributed calls, but the Phase 1 baseline shows
> coverage is the binding factor, and coverage is being addressed by
> giving the research agent more freedom to choose its actions (a
> research-agency plan on main). Phases 6 and 8 carry forward. Revised
> order and new harness work:
> [retrieval-quality-amended.md](retrieval-quality-amended.md). Phase
> detail below remains the mechanism reference.

## Phase status

| Phase | Scope | Testable behavior (gate) | Status |
|-------|-------|--------------------------|--------|
| 1 | Retrieval-isolation harness | CLI emits per-fact recall@k + queries-per-fact for a fixed question; repeats quantify variance | **Complete** (milestone baseline: full sweep, 2026-09-14) |
| 2a | Depth rendering + serialization | `depth` survives snapshots; retry table + UI badges show snippet/page | **Complete** (2026-09-17) |
| 2b | Source-content store + material classes | Fetched bodies stored beyond the serving cap; class upgrades; 4-class badges in UI | **Complete** (2026-09-17) |
| 3 | Delegated query-writer pass | Harness A/B: query-writer vs freeform on same decomposition | **Gate run complete** (2026-09-18: null macro result — see implementation notes; register enforcement extracted as Phase 4) |
| 4 | Register-enforced query generation | ≥2 distinct registers per fact set enforced in code; register distribution reported alongside recall | Not started |
| 5 | Per-fact fan-out + fact-type templates | Fan-out variant measured by harness; budget watch | Not started |
| 6 | Passage-level retrieval | web_search returns ranked passages; recall@k lift vs Phase-1 baseline | Not started |
| 7 | Within-run feedback memory | Zero-yield queries force register change; PRF reformulation; harness validates | Not started |
| 8 | Source-store lifecycle (retention/eviction) | `source_contents` size stays bounded under a configurable policy; fresh runs unaffected | Not started |

Phases 1 and 2 are independent and can proceed in parallel. Phase 3
depends on 1 (baseline). Phase 4 (register enforcement) depends on 3.
Phase 5 (fan-out) depends on 4 — fanning out a single-register variant
set would just buy more of the same miss. Phase 6 depends on 2b (store
hydration). Phase 7 depends on 3/4 (the writer is the consumer). Phase
8 is store hygiene: it depends only on 2b and can land at any
point — it should land before long-running use makes unbounded growth a
real problem.

## Grounding: what the code actually does

Facts the phases build on (file:line as of 2026-09-11; re-verify before
editing):

- **Research loop** — `backend/moira/workflow/nodes/research.py`.
  Entry `research()` at `research.py:2307`; tool loops
  `_run_native_tool_loop` (`:1895`) / `_run_text_tool_loop` (`:2061`);
  execution pipeline `_execute_tools` (`:769`) → `_process_execution_results`
  (`:1148`). Citation content is stored capped:
  `content=(sr.content or sr.snippet)[:_CITATION_CONTENT_LIMIT]` at
  `research.py:1229-1243`.
- **Query dedup already exists** — `_partition_web_search_dupes`
  (`research.py:1003`), `_QUERY_DUPE_THRESHOLD = 0.65` (`:91`), IDF-weighted
  `_query_similarity` (`:966`). Fan-out must merge *results across
  variants* (URL dedup via `_find_or_merge_citation`, `research.py:1688`)
  while allowing the variant queries themselves past the dupe guard.
- **"Queries already tried" already exists** —
  `ExecutionState.issued_queries` (`models/knowledge.py:190`) and the
  per-request ledger `ExecutionState.request_attempts` (`:184`), recorded
  by `_record_request_attempt` (`research.py:1111`), rendered into retry
  prompts by `_format_request_outcomes` (`research.py:226`). The
  query-writer consumes this ledger; nothing new to invent for input.
- **Citation / Fact models** — `models/knowledge.py:30-45` (Citation with
  `depth: "page" | "snippet"` at `:45`) and `:59-72` (Fact with `status`
  unknown/unverified/verified/contradicted, `citation_ids`; **no
  `derivation` field on Fact** — that lives on Conclusion only, `:93`).
  `CITATION_CONTENT_LIMIT = 5_000` (`models/knowledge.py:56`);
  `_TOOL_RESULT_FEEDBACK_LIMIT = 3_000` (`research.py:140`).
- **Snapshot gap confirmed** — `knowledge_summary()`
  (`models/knowledge.py:250-313`) serializes citations without `depth`
  (`:298-311`). Snapshot chain: `active_run.py:533-535` / `:653-654` →
  `_persist()` `:741-773` → `WorkflowRun.knowledge_snapshot`
  (`persistence/interfaces.py:54`). Eval capture reads it in
  `moira_eval/capture.py:292-299`.
- **Retry-table depth is derived, not read** — `_format_prior_citations`
  (`workflow/nodes/_helpers.py:559-609`) computes depth from `content`
  presence (`_depth(c)` at `:591-595`), so it can disagree with
  `Citation.depth` (e.g. `_apply_sources` sets `depth="snippet"` with
  excerpt content present, `research.py:1789-1815`). Phase 2a unifies on
  the field.
- **recall_source interception pattern** — stub tool
  (`tools/builtin/recall_source.py:48-60`) + in-node synthesis
  `_build_recall_source_result` (`research.py:631-712`), including the
  snippet-depth refusal (`:653-671`). This is the pattern a query-writer
  hook or summarize interception would follow; executed tools get no store
  handle (service-locator via `service_provider` is the real-tool
  alternative, per `web_search.py:270-275`).
- **web_search is SearXNG, not a Brave API** —
  `tools/builtin/web_search.py:192` (GET `{searxng_base_url}/search`,
  `format=json`); "brave" appears as a SearXNG *engine name*. The
  retrieval-quality.md claim "engine variance ruled out" rests on the
  SearXNG engine config being stable — the harness should record
  `metadata["results"][i]["source"]` engines per run to keep that honest.
  Exact-query cache: `SearchCache` (`web_search.py:57`), **separate DB**
  `./data/search_cache.db`, key = sha256 of query|categories|language|
  time_range (`:90-105`), TTL default 7 days, settings
  `web_search.cache_enabled` / `web_search.cache_ttl_seconds`
  (`services/settings/definitions.py:145-166`).
- **Budget/cost plumbing** — `ToolDefinition.invocation_cost /
  call_limit_per_run / call_limit_per_step` (`tools/base.py:27-29`);
  `STANDARD_TOOLS` registration (`tools/standard.py:18-65`; web_search
  5.0/10/5, url_content 3.0/15/8); run-start dicts
  `_build_tool_cost_and_limits` (`api/streaming.py:31-50`); enforcement
  `_validate_and_filter_calls` (`research.py:424`, cost at `:1328`) +
  `workflow/budget.py`. Fan-out pricing composes with this as-is.
- **DB migrations** — raw `sqlite3`, numbered SQL files in
  `persistence/sqlite/migrations/`, `CURRENT_VERSION = 24`
  (`persistence/sqlite/schema.py:10`). New table = `025_*.sql` + bump +
  repo class under `persistence/sqlite/repos/` + register in
  `service_setup.py init_services`. Precedent for stores outside moira.db:
  `search_cache.db`, LanceDB `vectors/` — but the source-content store
  should live **in moira.db** (run-scoped, joined by run/citation, useful
  for forensics), unlike the search cache.
- **Prompts** — `resources/prompts.md` sections `## key`, rendered by
  plain `{var}` `str.replace` (`moira/prompts.py:145-159`), validated
  against `REQUIRED_SECTIONS` at startup. New sections must be added
  there. Prompt examples must use non-eval subject matter (AGENTS.md).
- **Eval package** — `backend/moira_eval/`: `questions.py` (7 fixed
  questions), `capture.py` (offline artifact capture from SQLite,
  `tool_trace` with `output_preview[:500]`), `metrics.py` (deterministic;
  nearest analog `targeted_fact_resolution_rate` at `metrics.py:242` —
  post-verification, not retrieval recall), `judge.py` (LLM judge,
  `judge_config_from_env()` env config), `invoke.py` (needs live server),
  `result.py` (`results/<commit_sha>/<qid>.json` — sha-keying has caused
  overwrites; namespace harness output differently).
- **Test patterns** — `backend/tests/test_integration.py:198-295` is the
  no-network full-graph pattern (`_inject_services` + mocked
  `tool_executor.execute_batch` + `compile_graph` + `ainvoke`);
  `test_research_node.py` for node-level. Eval tooling tests live in
  `backend/moira_eval_tests/`.
- **No BM25 anywhere**; tokenization helpers exist (`research.py:916`,
  `:929`). Embedding stack (sentence-transformers + LanceDB) currently
  covers tool descriptions only.

## Phase 1 — Retrieval-isolation harness

**Goal:** make "retrieval is the noise" a reproducible number: per-fact
recall@k and queries-per-resolved-fact on the fixed question set, with
repeat-run variance. Everything later A/Bs against this.

**Design:**

- New module `backend/moira_eval/retrieval_harness.py` (+ CLI via
  `python -m`). Runs a **subgraph** of the real nodes:
  `decomposition → tool_identification → planning → research → END`
  (reuse the real node functions; no synthesis/review/evaluation/report).
  Built with `StateGraph` the same way `workflow/graph.py:191` composes
  the full graph; no app server needed (in-process `ainvoke`, the
  `test_integration.py` pattern promoted to a CLI).
- **Determinism:** real web_search through SearXNG with the exact-query
  cache on (deterministic per query string); model sampling of queries
  stays live — that *is* the variable being measured. Run N repeats per
  question (default 3) and report mean ± sd per fact.
- **Recall scoring:** per fact, judge whether the needed information is
  present in what retrieval returned, at result depth k (k = 1..returned).
  Two scorers:
  - LLM scorer (default): one cheap call per (fact, retrieved-set), reusing
    the `judge_config_from_env()` pattern; input = `fact_needed` +
    concatenated retrieved snippets/content for that fact's attributed
    calls; output = `{present: bool, evidence_quote, found_at_k}`.
  - Manual annotation fallback: per-question gold-marker file (keywords /
    phrases that must appear) for deterministic re-scoring without the
    judge — also guards judge drift.
- **Metrics added to `moira_eval/metrics.py`:** `per_fact_recall_at_k`,
  `queries_per_resolved_fact`, `unresolved_fact_count`, plus existing
  `duplicate_queries_intercepted`. Pure functions over a harness artifact
  dict (same style as existing metrics).
- **Output layout:** `backend/moira_eval/results/harness/<question_id>/<
  variant>/<timestamp>/` (variant starts as `freeform`) — avoids the
  sha-overwrite pitfall recorded in EVAL_LOG.md.
- Captures the url_content:web_search ratio as a side effect (answers
  source-quality-and-verification.md's open question) — include both
  counts in the artifact.

**Tests** (`backend/moira_eval_tests/`): subgraph compiles and terminates
after research; recall scorer unit tests on fixture artifacts (judge
mocked); gold-marker scorer deterministic; metrics functions on canned
artifact dicts. Integration smoke: one repeat of one question against a
mocked executor (no network).

**Gate:** `uv run python -m moira_eval.retrieval_harness --question
water-blood-pressure --repeats 3` prints a per-fact table (recall@k,
k-found, queries) + summary stats. Record a baseline artifact in
`agent-docs/` notes; EVAL_LOG stays for full-pipeline scores only.

**Baseline recorded (2026-09-13, live run, `water-blood-pressure` ×3,
freeform variant, artifact
`backend/moira_eval/results/harness/water-blood-pressure/freeform/20260913T180953.json`).**
Headline numbers (original = decomposition facts; queried = facts with ≥1
attributed query; the run predates the original-fact snapshot, so
originals are approximated as planning-targeted — undercounts coverage
slightly):

- recall@5: **0.11** all-facts / **0.14** original / **0.49 ± 0.22** queried
- recall@1: 0.09 / 0.12 / 0.44 ± 0.29 — recall@1≈recall@5 means hits, when
  they happen, rank #1; misses don't improve with depth
- planning coverage: **0.32** of original facts ever got a query
- queries/resolved fact: **1.00** — every resolution came from a single
  query; no fact got a second register (the fan-out Phase 5 targets)
- page rescue: 0.02 (one fact); url_content 1.3/run vs web_search 9.7/run
- spawn: research's overflow-split path added ~4.7 facts/run that are never
  queried (excluded from the headline denominator; ~0.9 of the 10-search
  budget was consumed before spawn even exists)
- Variance is the story: queried-recall@5 per repeat was 0.33/0.40/0.75 —
  the lottery, now a number
- Superseded as the milestone baseline by the full-sweep record below
  (kept for the original single-question forensics)

**Milestone baseline (2026-09-14, full sweep, all 7 benchmark questions ×
3 repeats, freeform variant, judge `z-ai/glm-5.2` (milestone), Qwen
workflow model; rows in
`backend/moira_eval/results/harness/summary.csv`, artifacts under
`results/harness/<qid>/freeform/20260914T1*.json`).**

Macro numbers (means over questions):

- **recall@5_original = 0.108 ≈ coverage 0.21 × queried-recall 0.52 — the
  identity holds almost exactly per-question, not just in aggregate.**
  Retrieval quality decomposes cleanly: *coverage* (does a decomposition
  fact ever get an attributed query?) times *queried recall* (given a
  query, does the needed info appear in top-5?). Coverage is the
  bottleneck — 2.4× the leverage of query phrasing.
- Per question (means over 3 repeats):

  | question | queried@5 | coverage | original@5 | facts/run | searches | fetches |
  |---|---|---|---|---|---|---|
  | trade-policy | 0.83 | 0.26 | 0.209 | 28.7 | 9.7 | 7.3 |
  | jazz | 0.75 | 0.18 | 0.107 | 21.7 | 4.7 | 2.3 |
  | telescope | 0.68 | 0.28 | 0.190 | 22.3 | 7.3 | 4.3 |
  | cheetos | 0.44 | 0.29 | 0.116 | 14.7 | 8.7 | 4.0 |
  | future-nostalgia | 0.44 | 0.11 | 0.053 | **44.3** | 9.7 | 2.7 |
  | water | 0.33 | 0.22 | 0.060 | 27.0 | 9.0 | 2.0 |
  | tyranitar | **0.15** | **0.089** | **0.020** | 33.3 | 6.3 | 1.0 |

- **Fact explosion drives the coverage ceiling:** ~27 decomposition
  facts/run macro vs ~8 searches (~1 query per fact per round). At 44
  facts (future-nostalgia) coverage 0.11 is arithmetically inevitable.
  Decomposition consolidation is the cheapest lever — halving facts
  roughly doubles coverage at zero search cost.
- **tyranitar fails at both levels** (worst queried recall *and* worst
  coverage) — the one question where query/ranking quality is also
  broken, not just volume. Consistent with its history (needed fact in
  pages never fetched).
- **No reformulation sweep-wide:** queries-per-resolved-fact ≈ 1.0 on
  every question — single-shot queries; misses are never re-queried
  (Phase 7's target).
- **url_content doesn't rescue recall:** 1–7 fetches/run, yet
  `recall_with_pages` ≈ 0 everywhere — fetches don't target missed facts.
- **~22% of url_content fetches fail, and they fail on exactly the hosts
  we most need** (2026-09-14 sweep: 17 of 79 fetches failed; all-time DB
  tool-metrics: 256 of 1129 = 22.7%). Failures are concentrated on
  bot-protected/paywalled authoritative sources — bls.gov 3/3, aeaweb.org
  3/3, usitc.gov 2/2, plus sciencedirect, karger, tandfonline,
  fred.stlouisfed.org, bea.gov, medium.com, cepr.org — while
  federalreserve.gov was the only blocked-class host with any success
  (1/2). This is the editorial/reference taxonomy. Failed fetches still
  consume per-run call limits and the model sometimes retries the same
  blocked URL. Fetch unblocking (rational browser-like headers, robots
  policy decision, cache of partial successes) is a prerequisite for the
  page-rescue path (Phase 6) to have anything to rank — recorded as a
  known obstacle there. Harness now counts `url_content_failures` and
  keeps the bounded error string per failed call (see implementation
  notes).
- Judge cost verification: 92 scorer calls, $0.23 actual — matches
  glm-5.2 pricing (~$0.21 predicted); flash would have been ~$0.05.
  Milestone judging on every sweep is affordable (~$0.23/sweep).

**Implementation notes (2026-09-13):** landed as described, with these
concretizations:

- Attribution: fact → queries follows the evidence-request chain
  (`evidence_requests.target_fact_ids` → `request_attempts[request_id]`,
  `tool == web_search`). Duplicate query texts count once. Facts with no
  attributed queries score as `never_queried` (counted separately from
  unresolved).
- Rank scoring: each fact's entries are the top-k results of its
  attributed queries (rank-labeled) plus page excerpts from url_content
  fetches of those results' URLs. `found_at_k` = min rank among passages
  the judge marked; `recall@k` derives from it. `with_pages` marks
  page-only rescues (present in fetched body, not snippets).
- Gold scorer lives in `moira_eval/gold/<question_id>.json`
  (`{"entries": [{"fact_keywords": [...], "markers": [...]}]}`);
  facts matching no gold entry score `present: null` (unknown), not
  absent — judge-drift guard, not a second oracle.
- Research executes only **model-emitted** tool calls (text mode carries
  `request_id` per call for strict attribution) — planned calls from
  planning's output are advisory. Smoke test fixtures reflect this.
- Scoring runs after all repeats (a scorer failure never wastes
  retrieval passes); judge input capped at 30 entries/fact, pages at
  3000 chars.
- CLI records the resolved intelligence model + variant label in the
  artifact; results are timestamp-keyed, not sha-keyed.
- Failed url_content calls are recorded, not just counted: each recorded
  tool call keeps a bounded `error` string (300 chars — the only record
  of *why* a fetch failed; output is empty on failure), repeat counts
  include `url_content_failures`, and the summary/CSV carry
  `url_content_failures` mean/sd (added 2026-09-14 after the blocked-host
  finding).
- Original-vs-spawn separation (added after the first live run): the
  harness captures decomposition's fact ids via a `values`-mode stream and
  stores `original_fact_ids` per repeat; metrics report three populations
  (all / original / queried) plus `coverage` and `spawned_fact_count`,
  because overflow-split spawn was deflating the all-facts denominator.
- Every run appends a row to the accumulating summary CSV at
  `backend/moira_eval/results/harness/summary.csv` (question_id, variant,
  model, timestamp, repeats, then mean/sd per summary field; the header
  grows to accommodate new fields, old rows gain blanks). Field
  definitions live in the `harness_recall_summary` docstring in
  `backend/moira_eval/metrics.py`.
- Runner plumbing: `./run.sh eval:retrieval ...` (sources `.env` for
  MOIRA_SECRETS_KEY, passes MOIRA_CONFIG_FILE/MOIRA_DATA_DIR like the dev
  commands — the harness loads services in-process, unlike other eval
  sub-actions).
- Judge models are purpose-scoped env slots (shared endpoint/key):
  `MOIRA_EVAL_JUDGE_MODEL_BATCH` (full-pipeline eval), and for this
  harness `MOIRA_EVAL_JUDGE_MODEL_ITERATION` (default; cheap judge for
  A/B iteration) vs `MOIRA_EVAL_JUDGE_MODEL_MILESTONE` (`--milestone`
  flag; precise judge for baselines). Chosen after a head-to-head
  rescore (2026-09-14): judges disagree on ~1/4 of borderline verdicts,
  flash skews loose (false positives on generic snippets), so iteration
  and milestone scoring are never mixed. Artifacts and the summary CSV
  record `judge_model` alongside the workflow model.
- Batch-2 forensics (2026-09-14, second water run): coverage is the
  ceiling — only 4–6 of ~21–29 decomposition facts get any attributed
  query per repeat; ~half of web_search calls are unattributed
  (model-issued, no request linkage) and thus invisible to promotion
  and to scoring. Both judges confirm repeats 1–2 were genuine misses
  (collapse is real variance, not the judge swap). Phase 3/5 ordering
  should weigh facts-touched-per-run (attribution discipline,
  decomposition consolidation) above per-query phrasing quality.

Remaining for the gate: done — baseline above. A gold file for
`water-blood-pressure` would enable judge-free re-scoring later.

## Phase 2a — Depth rendering + serialization (ships independently)

**Goal:** "what did the agent actually have" becomes a serialized,
rendered fact — using the existing `Citation.depth` field, before any
store exists.

- `knowledge_summary()` citation serialization includes `depth`
  (`models/knowledge.py:298-311`). Legacy rows without `depth` serialize
  as-is; consumers treat missing as unknown.
- `_format_prior_citations` / `_depth(c)` (`_helpers.py:591-595`) reads
  the `Citation.depth` **field** instead of inferring from content
  presence (keeps the retry table consistent with `recall_source`'s
  refusal logic, which already reads the field, `research.py:653-671`).
- Frontend: `KnowledgePanel.vue` Sources list + fact citation refs and
  `ReportPanel.vue` citation rows render a small snippet/page badge from
  the serialized flag (UI parity rule, retrieval-quality.md).
- Backend tests: snapshot round-trip retains `depth`; helper renders
  field-derived depth. Frontend: component test for the badge +
  `npm run lint` / `npm run test:unit` / `npm run build`.

**Gate:** a run snapshot's citations carry depth; UI shows the badge.

## Phase 2b — Source-content store + material classes

**Goal:** the web_source store from retrieval-quality.md §"Source-content
store": full bodies beyond the serving cap, material-class enum, hydration
on fetch.

- **Migration** `025_source_contents.sql`: table
  `source_contents(url_hash TEXT, url TEXT, run_id TEXT,
  citation_id TEXT, material_class TEXT, content TEXT, content_type TEXT,
  byte_size INTEGER, truncated INTEGER, fetched_at TEXT,
  PRIMARY KEY (run_id, url_hash))`. Per-run scoped now (`run_id` column;
  cross-run reuse is a later policy flip). In moira.db, not a sidecar
  file — it joins against runs for forensics. The key includes `run_id`
  so storage scope matches `get`/`evict_run` scope: two runs fetching
  the same URL keep independent rows.
- **Class enum:** carry it in the existing `Citation.depth` field, widened
  to `snippet | clipped | full | summary` (legacy `"page"` renders as
  `clipped`). One field, one vocabulary, all consumers — avoids a
  parallel metadata channel. `full` requires store backing; `clipped` is
  today's capped page; `summary` reserved for the deferred tool.
- **Repo** `SourceContentRepository` under `persistence/sqlite/repos/`
  (interface in `persistence/interfaces.py`), registered in
  `service_setup.py`. Operations: `upsert(run_id, citation_id, url, body,
  class)`, `get(run_id, url_hash)`, `evict_run(run_id)`; body cap
  100,000 chars default (config `source_store.max_body_chars`) — the
  PokeAPI-sizing question (summarize-source.md) starts as "first 100K,
  class says `full` only when the body fit whole, else `clipped` with a
  truncated flag in metadata).
- **Hydration:** in `_process_execution_results`, when a url_content
  result arrives, write the pre-cap body (`_MAX_OUTPUT_LENGTH = 100_000`
  already bounds it, `url_content.py:26`) into the store and set
  `depth = "full"` (or `clipped` if truncated) on the citation; `Citation.content`
  stays the 5,000-char serving window, unchanged. Fetch-on-miss
  re-hydration composes later (Phase 6 / the deferred tool).
- `knowledge_summary()` serializes the class flag (from 2a) but never the
  body — snapshots stay lean.
- UI: badges upgrade to the 4-class vocabulary (`KnowledgePanel.vue`,
  `ReportPanel.vue`); a `full`-class source in the Sources list can show
  byte size on hover (cheap, from byte_size if we serialize it — include
  `byte_size` in the summary serialization).
- **Tests:** migration up/down on a temp DB; repo round-trip; research
  node test asserting store write + class upgrade on a canned url_content
  result; snapshot test asserting body absent / class present; frontend
  badge test update.

**Gate:** after a run that fetched a long page, the store row exists with
the full body; the citation serializes `depth: "full"`; UI shows it.

**Implementation notes (2026-09-17):** landed as described, with these
refinements:

- Full body travels from `url_content` to the store via a reserved
  metadata key (`full_body`) — a side channel the research node strips
  from every agent-facing and stream view (`_strip_reserved_metadata`),
  so neither the writer events nor `tool_results_log` ever carry 100K
  bodies. The 5K `Citation.content` window is unchanged by design.
- Store writes go through the async write queue (fire-and-forget, same
  pattern as executor metrics). When store services are absent the
  citation is marked truthfully `clipped` (window), not `full`.
- `Citation.byte_size` records the true fetched size; it feeds
  `knowledge_summary` and the UI hover ("N chars stored") alongside the
  depth badge.
- **Key fix (same day):** the table initially keyed `url_hash` alone, so
  run B's fetch of a URL run A had fetched stole the row — the storage
  scope contradicted the `get`/`evict_run` scope. Re-keyed to composite
  `PRIMARY KEY (run_id, url_hash)` (migration 025 rewritten in place, live
  table dropped and re-created, no data preserved); the redundant
  `run_id` index was dropped because the composite PK's leftmost column
  covers run-scoped queries. Regression tests pin cross-run independence
  and eviction asymmetry.

## Phase 3 — Delegated query-writer pass

**Goal:** query generation leaves the overloaded research prompt; a
narrow, unit-testable pass emits 2–3 register-diverse queries per
targeted fact.

- **New module** `backend/moira/workflow/nodes/query_writer.py`:
  `async def write_queries(fact_needed, subject, evidence_needed,
  queries_tried, snippets_seen, client) -> list[dict]` returning
  `[{query, register}]` with registers from the fact-type template table
  (retrieval-quality.md §"Fact-type query templates"; source-type
  taxonomy vocabulary shared with source-quality-and-verification.md).
- **Prompt** `## query_writer.system` in `resources/prompts.md` (+ entry
  in `REQUIRED_SECTIONS`, `moira/prompts.py:25-66`). Output JSON, low
  temperature. Same workflow model first (open question resolved as:
  separate cheap prompt, same model — measure a smaller model later via
  config `research.query_writer_model`).
- **Integration:** a pre-execution hook in `_execute_tools`
  (`research.py:769`): when the resolved call is a `web_search`
  attributed to a request with targeted facts and the config flag
  `research.query_writer_enabled` is on, the model's query is replaced by
  the writer's first query, and remaining variants are held as the
  fan-out list (Phase 5 consumes them). Record both original and written
  query in `request_attempts` (extend the ledger entry with
  `written_query`, `register`) — forensics + A/B attribution.
- **Tests:** unit tests with a mocked client asserting register diversity,
  length discipline, and that `queries_tried` suppresses repeats; research
  node test for the hook (on/off); harness variant `query-writer` for the
  A/B.

**Gate:** harness A/B on ≥ 2 questions shows recall delta freeform vs
query-writer with variance bounds.

**Implementation notes (2026-09-17):** landed as described, with these
refinements:

- Writer module is `backend/moira/workflow/nodes/query_writer.py`
  (`write_queries(...)`), prompted by `query_writer.system` in
  `resources/prompts.md`. Output normalization enforces the contract
  mechanically: ≤ `MAX_QUERIES` (3), ≤ 60 chars each, dedup within the
  set, suppression of already-tried queries (case/whitespace-blind),
  and *any* model/parse failure degrades to `[]` — the writer can only
  add suggestions, never block a search.
- The hook lives in `_rewrite_calls_with_writer` (research.py), called
  from both tool loops *after* call validation but *before*
  `_execute_tools`, so rewritten queries still pass through near-dupe
  interception and the run-scoped query ledger like any other query.
  Only `web_search` calls attributed (via `request_id`) to an evidence
  request with ≥ 1 targeted fact are rewritten; model free-issues pass
  through untouched. Writer output is cached per request id within a
  batch — several calls serving one request share one sub-call.
- Ledger (`request_attempts`) entries gain `original_query`,
  `written_query`, and `register` when a rewrite happened; `query` is
  what actually executed. Both phrasings are therefore forensically
  visible, and the harness/spreadsheet can attribute hits to the
  writer vs the model.
- Remaining writer variants ride in the rewrite record as
  `queued_variants` — Phase 5's fan-out consumes them; they are not
  executed in Phase 3 (depth stays 1 query per call).
- Config: `MoiraConfig.research` (`ResearchSettings`):
  `query_writer_enabled` (default false) + `query_writer_model`
  (empty = same workflow model; the plan's resolved open question).
  Harness wiring: `_apply_variant(config, variant)` in
  `moira_eval/retrieval_harness.py` maps the `--variant query-writer`
  label to the flag, so the A/B is
  `./run.sh eval:retrieval --question <qid> --variant query-writer`
  vs `freeform` on the same decomposition, scored identically.
- Tests: `tests/test_query_writer.py` (7 — normalization contract,
  tried-suppression, decline-to-empty, prompt carries context),
  `TestQueryWriterHook` in `tests/test_research_internals.py` (5 —
  rewrite/pass-through/decline/ledger), `TestApplyVariant` in
  `moira_eval_tests/test_retrieval_harness.py` (3). Full suite green
  (1254 backend+eval), ruff clean.

**A/B interim (2026-09-18, water-blood-pressure ×3, both arms judged by
glm-5.2):** query-writer 6 facts hit (4/1/1 per repeat, ranks 1–4) vs
freeform 2 (2/0/0, all rank 1); recall@5 queried 0.58 vs 0.22,
original 0.13 vs 0.05. Direction positive, but n=3 on one question is
variance-dominated — the full-sweep gate run decides. The judge-model
confound (flash judged the arm before the 5.2 rescore) was checked: both
judges produced identical verdicts on this artifact. Writer forensics:
it fired on every attributed call (4/2/4 rewrites vs 8/4/10 searches);
the remaining searches were unattributed model free-issues that bypass
it. All rewrites landed in the `technical` register.

**Full-sweep gate (2026-09-18, 7 questions × 3 repeats, both arms
glm-5.2):** macro recall@5-original 0.108 (freeform) vs 0.109
(query-writer) — **null result**. recall@5-queried 0.52 vs 0.53;
coverage 0.207 vs 0.206; queries/resolved 1.00 vs 0.93. Per-question
r@5-original deltas: cheetos +0.08, tyranitar +0.05, jazz +0.02,
future-nostalgia +0.01, trade −0.02, water −0.03, telescope −0.11.
Compounding notes: the query-writer arm decomposed into MORE facts
(31.0 vs 27.4/run — worse denominators), used slightly fewer searches
(7.2 vs 7.9) and fetches (2.6 vs 3.4); its url_content blocked/run
(0.24) is real while the baseline column is missing data (the failure
counter postdates the 09-14 sweep — blank, not zero). Hook forensics
across the sweep: 102 rewrites, ~85% of attributed calls — but **all
102 in the `technical` register**. The designed treatment (deliberate
register diversity) was not delivered: the same 27B model rephrases in
one academic register regardless of the prompt's three-register
contract. Verdict: Phase 3 alone is null, but the treatment was
diluted — the writer needs mechanical register enforcement (per the
inherited principle: structured-data rules over prompt-hope). Extracted
as Phase 4; only then can Phase 5 fan-out test deliberate diversity.

**rep1 forensics (why one repeat covered 2/20 requests):** not budget
(28/150 consumed), not dedup (0), not blocked fetches (0) — the model
emitted calls in only 2 of the 3 internal rounds
(`DEFAULT_MAX_ROUNDS`, research.py:66), then declared research done
with 122 budget unspent ("model believes it's done" termination). Two
compounding observations: (a) 8 of the 20 evidence requests were
extraction-detail requests ("Methods detail from the key relevant
source…") that presuppose stage-one identification of that source —
premature specificity the research pass skipped past; (b) the harness
subgraph runs a single research invocation with no review/retry
routing, so harness coverage measures intra-pass discipline and
under-claims what the full graph (which routes back through planning on
review failure) would retrieve. Full-sweep A/B gate run pending.
- The Phase-3 gate: **run complete** (2026-09-18, results above — live
  model + SearXNG, same repeat count as the freeform baseline).

## Phase 4 — Register-enforced query generation

**Goal:** variant sets are register-diverse by construction — enforced in
code, not prompt-hope. Extracted from the Phase-3 gate (2026-09-18): the
writer hook fired on ~85% of attributed calls across the sweep, but
every rewrite came back in the `technical` register (102/102) — the
prompt's three-register contract did not survive contact with the model.

- **Slot assignment is code's job:** the hook assigns 2–3 distinct
  registers per fact from the fact-type template table
  (retrieval-quality.md §"Fact-type query templates": numeric/spec →
  spec-sheet + product-review registers, causal/medical → scholarly,
  cost/comparative → forum/"vs" colloquial, event/historical → news).
  The writer does not choose registers; it fills them.
- **Fill-and-check:** the writer prompt passes the assigned slots;
  the response validator (`_normalize_response` in
  `backend/moira/workflow/nodes/query_writer.py`) requires one query
  per assigned register. A missing or off-register slot falls back to
  a deterministic templated skeleton built from
  `fact_needed`/`subject`; duplicate registers collapse. Compliance is
  a structural property of the output, never an assumption.
- **No execution change:** one query per fact still executes (first
  variant); `queued_variants` still queue for Phase 5. The rewrite
  ledger already records `register` per variant — it becomes a gate
  metric (register distribution), reported alongside recall by the
  harness.
- **Tests:** assignment is table-driven by fact type; skeleton fallback
  when the writer omits or echoes slots; off-register
  relabel-or-drop; distribution shows ≥ 2 distinct registers per fact
  set. Harness variant `query-writer-enforced` (the label exists for
  the A/B; fold into `query-writer` if it wins).
- **Design note:** deciding whether to classify fact type in code
  (keyword heuristics on `fact_needed`) or to ask the writer for it
  with the assignment — code keeps it deterministic; the decomposition
  node already emits `subject` that can anchor it.

**Gate:** enforced vs current query-writer on the same question set,
same judge (glm-5.2): register distribution must show ≥ 2 registers in
real sweeps, recall@5 compared against the 2026-09-18 query-writer
numbers. Reads two ways: enforcement alone lifts recall → Phase 5
fan-out's extra-search trade may be unnecessary; diversity moves
nothing → the miss is ranking/snippets, and Phases 5/6 carry the
burden.

## Phase 5 — Per-fact fan-out + fact-type templates

**Goal:** issue all register variants per fact; a fact sinks only if every
register misses.

- In the Phase-3 hook: when `research.fanout_enabled`, execute all
  variants (each a real web_search call — costs and the 10-per-run limit
  apply honestly). The dupe guard must compare *across variants*: variants
  are exempt from `_partition_web_search_dupes` against each other by
  design (they are deliberately different phrasings), but still deduped
  against `issued_queries` from prior rounds.
- Result merge: existing URL merge in `_find_or_merge_citation`
  (`research.py:1688`) already unifies duplicates; per-fact attribution
  extends `_record_request_attempt` with per-variant outcomes.
- Budget interplay: fan-out trades breadth for depth under the same
  `budget.default_limit` (150) and web_search `call_limit_per_run` (10).
  The harness measures whether to raise the limit or keep breadth — no
  default change until numbers say so.
- Templates: the fact-type → registers table now feeds code-enforced
  slot assignment (Phase 4); Phase 5's addition is *execution* of the
  filled variant set, not more prompt surface.
- **Tests:** hook issues N variants once each; cross-variant exemption +
  prior-round dedup; merge behavior; budget accounting (N calls charged).
  Harness variant `fanout`.

**Gate:** harness variant `fanout` vs `query-writer` on the question set:
recall lift per additional search spent.

## Phase 6 — Passage-level retrieval

**Goal:** convert page-level luck into passage-level recall — the web_search
overhaul. **Requires Phase 2b** (fetched bodies must land in the store).

- Spike first (no plumbing): `backend/moira/tools/builtin/passage_rank.py`
  or a helper module — chunk text (paragraph windows, ~800 chars, 50%
  overlap), score chunks against `fact_needed` with **BM25 implemented
  inline** (no new dependency; tokenization reuse from
  `research.py:916`-style helpers, lifted into a shared util). Embedding
  re-ranking is a later option (stack exists: sentence-transformers).
- Integration shape: a new mode of web_search (config flag
  `web_search.passage_mode`) — after the search returns top-N URLs, fetch
  pages (reuse url_content fetch+extract path as a library call), hydrate
  the store (Phase 2b — class `full`/`clipped`), chunk, rank against the
  targeted `fact_needed`, and return the top passages with source IDs in
  `output` (and `metadata["passages"]`). Fetches are charged honestly
  (url_content-class cost) or folded into a re-priced web_search — decide
  with harness numbers before enabling by default; default off.
- Fallbacks: fetch failures degrade to snippet-only results (today's
  behavior); N fetched per search capped (default 3) — budget discipline.
- **Known obstacle — blocked fetches (2026-09-14 sweep data):** ~22% of
  url_content fetches fail outright, concentrated on the authoritative
  hosts passage ranking most needs (bls.gov, aeaweb.org, sciencedirect,
  fred.stlouisfed.org, …). Snippet-only degradation on those hosts
  reproduces today's page-level luck rather than fixing it. Before or
  alongside this phase: fetch unblocking work (headers/robots policy,
  failure-aware URL selection that stops retrying known-blocked hosts
  within a run). The harness's `url_content_failures` count is the
  regression signal.
- **Tests:** BM25 unit tests (known corpus, expected ranking); chunking
  edge cases; degradation paths; integration test with mocked fetches.
  Harness variant `passage`.

**Gate:** recall@k(large) lift over Phase-1 baseline on ≥ 2 questions;
cost per resolved fact reported.

## Phase 7 — Within-run feedback memory

**Goal:** mechanical feedback instead of re-rolling guesses.

- **Query outcome memory:** `_record_request_attempt` data already
  exists; feed `queries_tried` + per-query yield into the query-writer
  input (Phase 3 hook reads the request's ledger). Mechanical rule in the
  writer: a zero-yield register is not re-sampled for the same fact —
  pick a different register (code-enforced choice from the template
  table, not prompt-hope).
- **Pseudo-relevance feedback:** on retry for a request with irrelevant
  results, extract salient terms from whatever snippets returned
  (TF-based, over the returned snippets only) and pass them as
  `corpus_terms` to the writer — reformulate with the corpus's vocabulary.
- **Fact-level provenance (UI parity):** facts resolved by a
  query-writer/fan-out call carry the winning query + register in
  `request_attempts`; serialize a compact `retrieved_via` hint on the
  fact in `knowledge_summary()` and render it on fact rows in
  `KnowledgePanel.vue`.
- Cross-run query playbook stays deferred (retrieval-quality.md
  §Deferred → parked list there).
- **Tests:** writer input assembly from ledger; register-forcing rule;
  PRF term extraction; provenance serialization + UI badge. Harness
  variant `feedback` (repeats should show tighter variance, not just
  higher mean).

**Gate:** harness repeats: unresolved-fact variance shrinks vs Phase 3/5
artifacts.

## Phase 8 — Source-store lifecycle (retention and eviction)

**Goal:** `source_contents` growth stays bounded. Today nothing calls
`evict_run`; the table accumulates ~100K-char bodies per fetched page
indefinitely. Standing policy (2026-09-17): the store is per-run and a
fresh workflow run fetches fresh content — cross-run reuse is a separate
future debate. This phase only bounds the table; it does not change
visibility semantics.

- **Policy (configurable combination, defaults chosen at
  implementation):**
  - **Max age** — delete rows older than
    `source_store.max_age_days` (default proposal: 30) based on
    `fetched_at`.
  - **Size cap** — when total `byte_size` exceeds
    `source_store.max_total_bytes` (default proposal: 500 MB), evict
    oldest-`fetched_at` rows (LRU-by-age as the proxy; there is no
    access-time tracking and adding one is not worth it for this
    store) until under the cap. Age wins first, then cap.
  - **Per-run ceiling guardrail** — a pathological run can fetch far
    more than most; `evict_run` remains available for explicit
    cleanup (e.g. wired into run deletion later, if runs become
    deletable).
- **Mechanism:** extend the existing periodic-cleanup pattern
  (service_setup already runs a search-cache cleanup thread) with a
  `source_contents` sweeper — same thread or a sibling. Lazy alternative
  (run the sweep opportunistically on hydration, every N-th write) is
  acceptable if the thread plumbing fights back; state the choice in
  implementation notes.
- **Repo primitives:** beyond `evict_run`, the repository needs
  `delete_older_than(cutoff) -> int` and
  `evict_to_size(max_total_bytes) -> int` (oldest-first). Keep them
  plain SQL so the sweeper is cheap; avoid loading bodies into Python
  (`SUM(byte_size)`, delete by `fetched_at` ordering, count-only
  reads).
- **Config:** add the two knobs to `SourceStoreConfig` next to
  `max_body_chars`; document that `0`/`None` disables a dimension.
- **Tests:** repo-level — age cutoff deletes old rows only; cap
  eviction removes oldest first and stops at the cap; disabled
  dimensions are no-ops. Sweep-level — the sweeper calls the repo with
  configured values; a failure in the sweep is logged, never raises
  into the run path.
- **Non-goals:** no cross-run body sharing, no compression, no
  access-frequency (true LRU) tracking — this store's workload (write
  once, read rarely, forensics later) doesn't justify the bookkeeping.

**Gate:** with the policy active, inserting more content than the cap
allows reduces the table to the cap (oldest evicted); recent runs'
`get()` results are unaffected; sweep failures never break a run.

## Open questions (defaults chosen above)

| Question | Default | Revisit when |
|----------|---------|--------------|
| Query-writer model: small model vs same model | Same model, separate cheap prompt (Phase 3) | Harness latency/cost numbers |
| Fan-out vs 10-call ceiling | No limit change until harness says depth wins (Phase 5) | Phase 5 results |
| BM25 vs embeddings for passages | Inline BM25 (Phase 6) | Recall shortfall vs manual inspection |
| Store body cap | 100K chars, `full` only when body fit (Phase 2b) | summarize-source.md scheduling |
| Store retention policy | Max age + size cap sweep (Phase 8) | Observed growth rate; cross-run reuse debate |
| Playbook persistence | Deferred (per-run only) | retrieval-quality.md parked list |
| Engine determinism | Assume stable SearXNG config; harness records engines per result | Engine mix drift in harness artifacts |

## Deferred / parked

- Cross-run query playbook, engine fusion — see retrieval-quality.md
  §Deferred.
- `summarize_source` tool — summarize-source.md, unscheduled; consumes the
  Phase 2b store (built 2026-09-17).

## Verification (every phase)

- Backend: `uv run pytest tests/ -q -x --ignore=tests/test_url_content.py`
  (+ `moira_eval_tests` where touched), `.venv/bin/ruff check`,
  `.venv/bin/ruff format --check` — from `backend/`.
- Frontend (2a, 2b, 7): `npm run lint`, `npm run test:unit`,
  `npm run build` — from `frontend/`.
- Harness numbers recorded under `backend/moira_eval/results/harness/`
  and summarized in the phase table above when a phase completes.
