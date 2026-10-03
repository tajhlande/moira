# Retrieval-quality (amended) — implementation plan

> **Scope:** implements the steps in
> [retrieval-quality-amended.md](retrieval-quality-amended.md). That doc
> decides *what* gets built and in what order (and why); this doc is the
> task-level breakdown — files, tests, gates. Mechanism reference for the
> older phase designs remains
> [retrieval-quality-plan.md](retrieval-quality-plan.md).

| Step | Work | Gate | Status |
|---|---|---|---|
| 1 | Research-agency observability (Fact.origin, stop-reason, unattributed/coverage_any in harness) | Freeform 7-question sweep ×3 reports new fields; becomes "before" numbers for research-agency Phase 1 | **Done** — implemented + gate run 2026-10-02 (989 backend / 277 eval tests, ruff clean); baseline table below |
| 2 | Source-store retention/eviction | `source_contents` bounded under policy; eviction tests | **Done** — implemented 2026-10-02 (defaults: 30-day age, 500M-char cap; 1277 tests, ruff clean); live DB currently under both limits, first sweep is a no-op |
| 3 | Fetch unblocking (failure classes, robots decision, blocked-host memory) | Failure rate vs 2026-09-14 baseline, broken down by class | **Code done + gate runs** 2026-10-03 (two trade-policy runs; classes/memory/error-persistence verified in the wild; failure-rate reduction not demonstrated — host-mix dominated, see Step 3 section) |
| 4 | Park original Phases 4/5/7 (doc work) | parked/ entries + index.md updated | Not started |
| 5 | Passage-level retrieval | — | **Deferred** (research-agency Phase 1 results first) |

Steps run on the `retrieval-quality` branch and merge together. Step 1
first: its gate run produces the baseline the research-agency plan
needs, so nothing downstream should start before it lands.

---

## Step 1 — Research-agency observability (harness)

Purpose: the 2026-09-14 baseline can't distinguish "agent stopped early"
from "agent couldn't retrieve." Every change below is measurement only —
no behavior change to the research loop.

### 1.1 `Fact.origin`

- `backend/moira/models/knowledge.py:69` — add
  `origin: NotRequired[str]` with comment: `"decomposition" | "overflow" | "discovered"`.
  TypedDict, no DB table (facts live in state snapshots) → old snapshots
  simply lack the field; consumers must treat missing as unknown.
- Set at the three creation sites:
  - decomposition node (where initial facts are built) → `"decomposition"`
  - `backend/moira/workflow/nodes/research.py:1632` (model-added facts:
    `fact_id` null/missing with `fact_needed`) → `"discovered"`
  - `research.py:1700` overflow-split (`next_id("f", ...)`) → `"overflow"`
- User decision (2026-10-01): carried on the fact itself, not in step
  detail, so later measurement/eval work can group by origin directly.
- Tests: each creation site sets origin; facts from old snapshots
  (no field) still load.

### 1.2 Loop outcome in state (stop-reason)

- `research.py:2898-2914` detail already has `rounds`, `stalled`,
  `new_facts` — but `exhausted_rounds` (computed at `research.py:2811`)
  is **not** in detail, and none of it lands in returned state (only in
  the `node_end` event the harness doesn't capture).
- Add `exhausted_rounds` to detail, and return a compact
  `execution_state["research_loop"] = {"rounds", "exhausted_rounds", "stalled"}`
  from the research node so `build_repeat_artifact` can read it from
  `final_state`.
- **Detail-schema note:** adding a key to research step detail requires
  the step-detail-schema skill's workflow (schema + tests) — load it
  when implementing.
- Budget unspent is already derivable: artifact has
  `budget_consumed`/`budget_limit` (`retrieval_harness.py:379-380`).
  Compute `budget_unspent_share` in metrics, not in the node.

### 1.3 Harness populations: spawned vs discovered

- `backend/moira_eval/retrieval_harness.py:395` `fact_queries` and
  `metrics.py:305-447` currently lump all non-original facts as
  "spawned" (`spawned_fact_count`).
- Split using `Fact.origin` (1.1): facts with `origin == "discovered"`
  become their own population; `origin == "overflow"` keeps the
  spawned name; missing origin + outside `original_fact_ids` → count as
  spawned (back-compat with existing artifacts).
- New summary fields (update the "definitive list" docstring at
  `metrics.py:357`): `discovered_fact_count`,
  `recall_at_k_discovered` (or `*_model_discovered` if cleaner),
  `discovered_resolution` (share of discovered facts reaching
  verified/unverified with claims). Keep `spawned_fact_count` semantics
  unchanged (overflow only) and add the split to per-repeat artifacts.
- Key agency metric (amended doc): **share of resolved facts not in the
  decomposition** — derivable as
  discovered_resolved / (discovered_resolved + original_resolved).

### 1.4 Unattributed calls + `coverage_any`

- Per repeat: `unattributed_web_search_calls` (web_search calls whose
  query text doesn't map to any request's attempts via the
  `fact_queries` chain) and its share of total searches. Source:
  `recorder.calls` in `build_repeat_artifact` vs
  `es["issued_queries"]`/request_attempts.
- `coverage_any`: for each fact, candidate material = snippets from its
  attributed queries **plus snippets from all unattributed queries**;
  judge "present" on the union. Implemented in
  `retrieval_harness.py:648` `_score_fact` / `:708` no-request-id path —
  extend the candidate set, cap at N (≈8) candidate snippets per fact to
  bound judge cost.
- Report `coverage_any_at_k` next to attributed `coverage`. The gap
  between them = recall lost to attribution, not retrieval.

### 1.5 CSV + tests

- `metrics.py` CSV gains the new columns; old artifact JSONs re-score
  with blanks (fields absent → `None`, never crash — existing pattern).
- Tests in `backend/moira_eval_tests/`: canned repeat artifacts covering
  all three origins, missing origin, exhausted vs stalled vs
  clean-stop, unattributed calls, coverage_any with mocked judge;
  stop-reason classification unit test on `research_loop` state.

**Gate:** live freeform 7-question sweep ×3 (needs model + SearXNG; user
runs). Deliverable: the amended doc's Step-1 field set, populated. This
run is the "before" column for research-agency Phase 1.

**Baseline interpretation notes (recorded 2026-10-02):**

- Historical harness rows (2026-09-13/14 freeform, 2026-09-18
  query-writer) all ran research model **Qwen3.8-27B-vllm-single**
  (Qwen3.6-27B base + additional post-training, DFlash2-W4A16 quant).
  A 2026-10-02 probe ran **Qwen3.6-35B-A3B-Q5** (same Qwen3.6
  architecture, no extra post-training). The two regimes are NOT
  directly comparable: the 35B-A3B probe produced 11.7 facts/run vs
  22–27 historically — recall/coverage improvements are at least partly
  a smaller-fact-set effect, not retrieval quality. Read
  `recall_at_5_queried` alongside `facts_per_run`.
- The 2026-10-02 probe (water-blood-pressure, query-writer variant, ×3)
  hit the round cap in 3/3 repeats (rounds=3, exhausted) with ~2/3 of
  budget unspent — first live confirmation of the control-flow asymmetry
  the amended plan was written around.
- Step 1 gate sweep must run the **freeform** variant (the probe above
  was query-writer).

**Gate run complete (2026-10-02, freeform ×3 ×7 questions, model
Qwen3.6-35B-A3B-Q5, judge z-ai/glm-5.3-flash, ~76 min).** Cross-question
"before" column for research-agency Phase 1 (full per-question rows in
`backend/moira_eval/results/harness/summary.csv`, 2026-10-02 rows):

| field | mean |
|---|---|
| recall_at_5 / _original / _queried | 0.310 / 0.412 / 0.586 |
| coverage (attributed) | 0.739 |
| coverage_any (union material) | 0.512 |
| discovered_fact_count | **0.0** (all 21 runs) |
| overflow_fact_count | 3.90 |
| resolved_share_beyond_decomposition | 0.124 |
| research_rounds | 2.90 (cap = 3) |
| research_exhausted_rate | **0.952** |
| research_stalled_rate | 0.0 |
| budget_unspent_share | **0.696** |
| unattributed_web_search_share | 0.348 |
| facts_per_run | 12.52 |
| web_search / url_content calls | 9.05 / 3.00 |
| url_content_failures | 0.43 |
| recall_with_pages | 0.044 |

Readings: (1) the round cap, not budget and not stalling, ends nearly
every run with ~70% of budget unspent — the control-flow bottleneck is
confirmed at scale and is research-agency Phase 1's target; (2) the model
introduces **zero discovered facts** — all non-original facts are
overflow splits, so current "agency" is decomposition-fact resolution
only; (3) ~35% of searches are unattributed, but union scoring
(coverage_any 0.512) stays below attributed coverage (0.739) — the
binding constraint is material presence (retrieval content), not
attribution; (4) page-rescue is negligible (0.044) and fetch failures
are rare at this volume.

---

## Step 2 — Source-store retention/eviction

Design is already written: retrieval-quality-plan.md §Phase 8 (max-age
+ size-cap sweep, repo primitives, config knobs).

- `backend/moira/config.py:64` `SourceStoreConfig`: add
  `max_age_days: int = 0` (0 = off) and `max_total_chars: int = 0` (0 = off).
- Repo (`backend/moira/persistence/sqlite/repos/source_contents.py`):
  add `evict_older_than(cutoff_iso) -> int` and `total_content_chars() -> int`
  (plain SQL, same connection conventions as upsert/get).
- Eviction runs at store-write time in research hydration (after
  upsert): if over size cap, delete oldest `fetched_at` rows (whole runs,
  newest-run-safe: never evict the current run's rows) until under cap;
  then age sweep. Config-off = zero behavior change.
- Tests: age eviction, cap eviction ordering, current-run immunity,
  both-off no-op, live-DB smoke on a copy.

**Gate:** a seeded store over cap shrinks to policy; nothing in a normal
run path regresses (full backend suite).

---

## Step 3 — Fetch unblocking

Context: ~22% of url_content calls fail (17/79 in the 2026-09-14 sweep;
256/1129 all-time), clustered on bls.gov, aeaweb.org, usitc.gov,
sciencedirect, fred.stlouisfed.org. Note `url_content.py:140-163` already
sends realistic browser UA/Accept/Accept-Language — the low-hanging
header fruit is done; measure before adding more header realism.

### 3.1 Failure classification

- `backend/moira/tools/builtin/url_content.py`: classify failures in
  the returned error string with a stable prefix —
  `blocked:` (403/401/429), `timeout:`, `not_found:` (404),
  `too_large:`, `unsupported:` (content-type), `network:` (other). Raise
  vs return path stays as-is; only the message gains the class.
- Harness: `build_repeat_artifact` counts url_content failures by class
  → new CSV columns (`url_content_failures_blocked`, `_timeout`, ...).

### 3.2 Blocked-host memory (within run)

- On a `blocked:` failure, record host in a run-scoped set (execution_state).
- Subsequent url_content calls to that host in the same run are
  intercepted before execution, returning a synthetic result marked
  "host blocked this run" — following the existing dedup interception
  pattern (`research.py:1506` `_execute_with_url_dedup`; synthetic
  results don't consume call limits, per the flag at `research.py:1368`).
- Prompt-visible consequence: the model sees fewer wasted fetch
  failures; tool-loop guidance already steers away from repeated hosts.

### 3.3 Robots / policy decision

- Decide + document (one paragraph in this doc's result or
  url_content docstring): we self-host SearXNG for research use and
  honor per-host rate/practicality (blocked-host memory), but do not
  fetch robots.txt per URL — document why (research-agent parity with
  browser UA; robots is a crawler-politeness contract, our volume is
  trivial). If this decision is uncomfortable, the honest alternative
  is honoring robots for GET fetches — **flag for user sign-off before
  implementing 3.2** rather than deciding unilaterally.

**Gate:** repeat of a fetch-heavy question ×3; failure rate vs
2026-09-14 (22%), broken down by class. Still-blocked hosts are recorded
below in the gate-run notes; a host is promoted to a
parked/additional-default-tools.md candidate only when a specialized API
endpoint could replace the need for that site (e.g. FRED for
bls.gov/federalreserve.gov material).

**Result (2026-10-02, code done — gate run pending):**

- 3.1: `url_content._fetch` raises `_FetchError(cls, detail)` →
  `ToolResult.error = "{cls}: {detail}"` for every failure path.
  Classes: `blocked` (401/403/429), `timeout`, `not_found` (404),
  `too_large`, `unsupported` (content-type — new guard: rejects
  PDFs/images before the body is read; text/*, xhtml, xml, json, rss,
  atom allowed), `network` (transport + other 4xx/5xx), `parse`
  (extraction), `invalid` (missing url). Non-2xx statuses still fail
  as before (raise_for_status semantics preserved by an explicit
  `status >= 400` catch-all).
- Harness: `build_repeat_artifact` records
  `counts["url_content_failure_classes"]` (parsed via
  `moira_eval.metrics.failure_class`; unprefixed legacy errors →
  `"other"`), and `harness_recall_summary` emits
  `url_content_failures_{class}` {mean, sd} columns for all 8 classes.
  Synthetic blocked-host interceptions never reach the executor, so
  these counts reflect real fetch failures only.
- 3.2: run-scoped `blocked_hosts: list[str]` in `ExecutionState`
  (models/knowledge.py), seeded from state at research() entry,
  appended by `_update_fetched_urls` when a REAL fetch fails with the
  `blocked:` prefix (dedup'd; synthetic results skipped), consulted by
  `_partition_url_content_calls` before execution — sibling URLs on a
  refused host get a synthetic `host_blocked` ToolResult (no budget
  charge, `metadata["host_blocked"]`, error `blocked: host ...` so the
  harness classifies it the same way). Exact-URL dedup takes precedence
  over host blocking. Cross-pass: persists in execution_state like
  issued_queries.
- 3.3: robots decision made with user sign-off — **skip robots.txt**.
  Rationale documented in the url_content module docstring
  (self-hosted research agent, trivial volume, browser-like headers;
  politeness enforced behaviorally via blocked-host memory).
- Tests: `tests/test_url_content_failure_classes.py` (new — the
  existing tests/test_url_content.py is excluded from the standard
  suite because of its network integration tail; these run MockTransport
  through the real `_fetch`), `TestBlockedHostMemory` in
  tests/test_research_internals.py (partition/synthetic/update +
  non-blocked classes ignored + synthetic skipped), harness
  failure-class counting + summary aggregation + legacy-repeat zeros.
  Verification: 1020 backend + 279 eval tests, ruff check + format
  clean.
- Follow-up (found examining live run 1881ee32, whose two failed
  url_content fetches persisted with NO reason): the failure reason
  was dropped before every consumer except the harness's
  RecordingExecutor. Fixed — research's tool_result event payload and
  tool_results_log entries now carry `error` (null on success);
  active_run persists it into `workflow_steps.detail.tool_results`
  (schema: optional `error` on tool_result, eval capture passes it
  through); and the model-facing zero-result/unstructured feedback
  lines append the reason ("Status: FAILED (blocked: HTTP 403 from
  ...)") so the model can tell a host refusal from a timeout.
  Verification after: 1021 backend + 279 eval tests, ruff clean;
  `validate_step_details --all` shows only the known historical-noise
  violations (optional property reclassifies nothing).

**Gate runs (2026-10-03, trade-policy-manufacturing ×3, freeform, 35B):**

Two runs, same question, judge `glm-5.3` (03:18 via Neuralwatt CDN,
06:17 via z-ai direct — same underlying model, so judge is comparable):

- Fetch failures: 03:18 run 2/7 (29%); 06:17 run 7/10 (70%) —
  `blocked` 3 + `unsupported` 4. Combined 9/17 (53%) vs the 2026-09-14
  baseline 12/22 (55%) for this question. NOT a clear improvement: the
  rate is host-mix dominated and each run samples a different mix.
- Recurring refusals across runs and baseline: `bls.gov` (blocked),
  `federalreserve.gov` (unsupported PDF — the content-type guard
  working, but the Fed publishes as PDF). One-off refusals this gate:
  `journals.uchicago.edu`, `oreilly.com`, `cepr.org`, `web.pdx.edu`,
  `live.icai.org`, `justinrpierce.com`. The long tail rotates; only the
  data/agencies recur — which strengthens the parked API-tools lane
  (FRED covers bls.gov/federalreserve.gov material).
- Blocked-host memory had nothing to intercept in either run (the model
  does not re-attempt a refused host within a run) — working as designed.
- Retrieval numbers moved modestly between the two runs (recall@5 0.45 →
  0.50, recall@3 0.39 → 0.50, coverage 0.83 → 0.85); both runs sit well
  above the 2026-09-14 same-question baseline (recall@5 0.16) with the
  smaller fact sets of the 35B model.
- `recall_with_pages` collapsed to 0.00 (0.05 prior run) because ~70% of
  fetch attempts failed — page-backed evidence is structurally capped by
  fetch success, not by retrieval or scoring. Fixing this belongs to the
  API-tools lane (parked/additional-default-tools.md), not to more
  fetch-side retries.

Gate verdict: classes + blocked-host memory + error persistence all
verified in the wild; failure-rate reduction NOT demonstrated on this
question (host-mix variance, small n). Still-blocked hosts are recorded
above (this section); none justify a parked/additional-default-tools.md
candidate yet — bls.gov/federalreserve.gov material is the one lane with
a replacing API (FRED, already on that list).

---

## Step 4 — Park original Phases 4/5/7 (doc work)

No code changes. `research.query_writer_enabled` stays default-false.

- Move the designs (mechanism intact, per parking guidelines) from
  retrieval-quality-plan.md to `agent-docs/parked/`:
  - Phase 4 (register-enforced query generation) + Phase 7
    (within-run feedback memory) → one parked entry or two, framed as
    candidate internals of research-agency Phase 4's scoped sub-loop.
  - Phase 5 (per-fact fan-out) → parked entry pointing at
    research-agency Phase 4 / grind-mode Stage 2 (agent chooses which
    facts get the sub-loop).
- retrieval-quality-plan.md: mark those phases "parked — see parked/",
  fix cross-references; index.md entries for the new parked docs.

**Gate:** parked/ entries + index.md updated; plan doc consistent.

---

## Verification (every step)

From `backend/`:

```
uv run pytest tests/ -q -x --ignore=tests/test_url_content.py
uv run pytest ../backend/moira_eval_tests/ -q -x   # where touched
.venv/bin/ruff check
.venv/bin/ruff format --check
```

Frontend untouched by Steps 1–4. Live gate runs (Step 1, Step 3) need
model + SearXNG — user executes.

## Deferred

- Step 5 (passage-level retrieval, old Phase 6) — wait for
  research-agency Phase 1 results per the amended doc.
