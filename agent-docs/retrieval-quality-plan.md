# Retrieval Quality: Implementation Plan

> **Status:** Plan — phases not started. Implements the sequencing in
> [retrieval-quality.md](retrieval-quality.md), which stays the
> design-direction document (evidence, failure modes, rationale). This
> document is the build plan: phases, code touchpoints, tests, gates.
> `summarize_source` itself is **not** in scope (see
> [summarize-source.md](summarize-source.md)); only the shared
> source-content foundation (Phase 2) is.

## Phase status

| Phase | Scope | Testable behavior (gate) | Status |
|-------|-------|--------------------------|--------|
| 1 | Retrieval-isolation harness | CLI emits per-fact recall@k + queries-per-fact for a fixed question; repeats quantify variance | Not started |
| 2a | Depth rendering + serialization | `depth` survives snapshots; retry table + UI badges show snippet/page | Not started |
| 2b | Source-content store + material classes | Fetched bodies stored beyond the serving cap; class upgrades; 4-class badges in UI | Not started |
| 3 | Delegated query-writer pass | Harness A/B: query-writer vs freeform on same decomposition | Not started |
| 4 | Per-fact fan-out + fact-type templates | Fan-out variant measured by harness; budget watch | Not started |
| 5 | Passage-level retrieval | web_search returns ranked passages; recall@k lift vs Phase-1 baseline | Not started |
| 6 | Within-run feedback memory | Zero-yield queries force register change; PRF reformulation; harness validates | Not started |

Phases 1 and 2 are independent and can proceed in parallel. Phase 3
depends on 1 (baseline). Phase 4 depends on 3. Phase 5 depends on 2b
(store hydration). Phase 6 depends on 3 (query-writer is the consumer).

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
  `source_contents(url_hash TEXT PRIMARY KEY, url TEXT, run_id TEXT,
  citation_id TEXT, material_class TEXT, content TEXT, content_type TEXT,
  byte_size INTEGER, fetched_at TEXT)`. Per-run scoped now (`run_id`
  column; cross-run reuse is a later policy flip). In moira.db, not a
  sidecar file — it joins against runs for forensics.
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
  re-hydration composes later (Phase 5 / the deferred tool).
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
  fan-out list (Phase 4 consumes them). Record both original and written
  query in `request_attempts` (extend the ledger entry with
  `written_query`, `register`) — forensics + A/B attribution.
- **Tests:** unit tests with a mocked client asserting register diversity,
  length discipline, and that `queries_tried` suppresses repeats; research
  node test for the hook (on/off); harness variant `query-writer` for the
  A/B.

**Gate:** harness A/B on ≥ 2 questions shows recall delta freeform vs
query-writer with variance bounds.

## Phase 4 — Per-fact fan-out + fact-type templates

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
- Templates: the fact-type → registers table is data the query-writer
  prompt consumes (Phase 3 already encodes it); Phase 4's addition is
  *execution*, not more prompt surface.
- **Tests:** hook issues N variants once each; cross-variant exemption +
  prior-round dedup; merge behavior; budget accounting (N calls charged).
  Harness variant `fanout`.

**Gate:** harness variant `fanout` vs `query-writer` on the question set:
recall lift per additional search spent.

## Phase 5 — Passage-level retrieval

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
- **Tests:** BM25 unit tests (known corpus, expected ranking); chunking
  edge cases; degradation paths; integration test with mocked fetches.
  Harness variant `passage`.

**Gate:** recall@k(large) lift over Phase-1 baseline on ≥ 2 questions;
cost per resolved fact reported.

## Phase 6 — Within-run feedback memory

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

**Gate:** harness repeats: unresolved-fact variance shrinks vs Phase 3/4
artifact.

## Open questions (defaults chosen above)

| Question | Default | Revisit when |
|----------|---------|--------------|
| Query-writer model: small model vs same model | Same model, separate cheap prompt (Phase 3) | Harness latency/cost numbers |
| Fan-out vs 10-call ceiling | No limit change until harness says depth wins (Phase 4) | Phase 4 results |
| BM25 vs embeddings for passages | Inline BM25 (Phase 5) | Recall shortfall vs manual inspection |
| Store body cap | 100K chars, `full` only when body fit (Phase 2b) | summarize-source.md scheduling |
| Playbook persistence | Deferred (per-run only) | retrieval-quality.md parked list |
| Engine determinism | Assume stable SearXNG config; harness records engines per result | Engine mix drift in harness artifacts |

## Deferred / parked

- Cross-run query playbook, engine fusion — see retrieval-quality.md
  §Deferred.
- `summarize_source` tool — summarize-source.md, unscheduled; consumes the
  Phase 2b store when built.

## Verification (every phase)

- Backend: `uv run pytest tests/ -q -x --ignore=tests/test_url_content.py`
  (+ `moira_eval_tests` where touched), `.venv/bin/ruff check`,
  `.venv/bin/ruff format --check` — from `backend/`.
- Frontend (2a, 2b, 6): `npm run lint`, `npm run test:unit`,
  `npm run build` — from `frontend/`.
- Harness numbers recorded under `backend/moira_eval/results/harness/`
  and summarized in the phase table above when a phase completes.
