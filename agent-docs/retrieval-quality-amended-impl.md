# Retrieval-quality (amended) — implementation plan

> **Scope:** implements the steps in
> [retrieval-quality-amended.md](retrieval-quality-amended.md). That doc
> decides *what* gets built and in what order (and why); this doc is the
> task-level breakdown — files, tests, gates. Mechanism reference for the
> older phase designs remains
> [retrieval-quality-plan.md](retrieval-quality-plan.md).

| Step | Work | Gate | Status |
|---|---|---|---|
| 1 | Research-agency observability (Fact.origin, stop-reason, unattributed/coverage_any in harness) | Freeform 7-question sweep ×3 reports new fields; becomes "before" numbers for research-agency Phase 1 | Not started |
| 2 | Source-store retention/eviction | `source_contents` bounded under policy; eviction tests | Not started |
| 3 | Fetch unblocking (failure classes, robots decision, blocked-host memory) | Failure rate vs 2026-09-14 baseline, broken down by class | Not started |
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
2026-09-14 (22%), broken down by class; still-blocked hosts listed as
API-tool candidates under parked/additional-default-tools.md (FRED, SEC
EDGAR, OpenAlex).

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
