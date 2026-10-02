# Retrieval Quality: Amended Plan

Replaces the remaining sequencing of
[retrieval-quality-plan.md](retrieval-quality-plan.md). That document stays
the mechanism reference (code grounding, phase designs, gate results);
this one sets what gets built, and in what order, now that the project is
moving toward giving the research agent more freedom to choose its
actions.

| Step | Scope | Origin | Status |
|------|-------|--------|--------|
| 1 | Harness changes to measure research agency | New | Not started |
| 2 | Source-store retention and eviction | Original Phase 8 | Not started |
| 3 | Fetch unblocking | Split out of original Phase 6 | Not started |
| 4 | Park original Phases 4, 5, 7 | Doc work | Not started |
| 5 | Passage-level retrieval | Original Phase 6 | Deferred until research-agency Phase 1 results |

Steps 1–4 finish on the `retrieval-quality` branch. Then merge to main and
start research-agency Phases 0–1. Step 5 resumes afterwards.

## Why the change

The Phase 1 milestone baseline (2026-09-14) split recall into two factors:
recall@5 on original facts 0.108 ≈ **coverage 0.21 × queried recall 0.52**,
holding per question.

- Original Phases 4, 5 and 7 improve queried recall. Even a perfect 1.0
  only lifts recall to about 0.21.
- Coverage is the larger lever. Its recorded causes are control-flow
  problems, not query phrasing:
  - About 27 decomposition facts per run against about 8 searches.
  - Early stops: rep1 used 2 of 3 rounds and stopped with 122 of 150
    budget unspent.
  - About half of web_search calls are unattributed, so they bypass
    the query-writer hook and the scoring.
- Phase 3 (query-writer) was null: all 102 rewrites came back in one
  register.
- Phases 4, 5 and 7 assume code decides what gets searched: planned,
  request-attributed facts, with fan-out over every fact. The
  research-agency direction moves that decision to the agent, so building
  them now means rework later.

**Kept:** work that helps whatever controls the search. That's the
harness, the source store, fetch reliability, and passage ranking.

## Step 1 — Harness changes to measure research agency

The harness (`moira_eval/retrieval_harness.py`) runs one research
invocation over the real nodes. That's the right test bed for
research-agency Phase 1, which changes only that invocation. It needs to
measure what agency changes:

- **Discovered-fact population.** Today facts outside `original_fact_ids`
  are counted as `spawned_fact_count` (overflow-split) and excluded from
  the headline. Split them into *spawned* (overflow-split) and
  *discovered* (new fact needs the model introduced), and report recall
  and resolution for discovered facts as their own population. Agency
  Phase 1's key metric is the share of resolved facts that weren't in
  decomposition.
- **Turns and stop reason per repeat.** Record research `rounds` (already
  in the research step detail, `research.py` near `:2903`), whether the
  round cap was hit (`exhausted_rounds`), the `stalled` flag, and budget
  left unspent at stop. This separates "model thought it was done" from
  "cap reached" from "budget out."
- **Unattributed-call accounting.** Count web_search calls without a
  `request_id` per repeat and report their share. Score whether their
  results resolve any fact (match against all facts, not only attributed
  ones), so agent-chosen searches aren't invisible to the metric.
- **Coverage definition.** Keep `coverage` (attributed) for continuity,
  and add `coverage_any`: a fact counts as covered if any executed query
  (attributed or not) returned material the judge marks present.
- Summary CSV gains the new fields; old rows get blanks (existing
  behavior).

**Tests** (`moira_eval_tests/`): metrics functions on canned artifacts for
each new population and field; the stop-reason classification; smoke test
with the mocked executor.

**Gate:** a freeform re-run of the 7-question sweep (×3) reports the new
fields. Its numbers become the "before" for research-agency Phase 1.

## Step 2 — Source-store retention and eviction

Original Phase 8, unchanged. Nothing calls `evict_run`, and
`source_contents` grows without bound. Max-age plus size-cap sweep, repo
primitives in plain SQL, config knobs on `SourceStoreConfig`. Full design
and gate in [retrieval-quality-plan.md](retrieval-quality-plan.md)
§Phase 8.

## Step 3 — Fetch unblocking

Split out of original Phase 6's "known obstacle." About 22% of url_content
fetches fail (17 of 79 in the 2026-09-14 sweep; 256 of 1129 all-time), and
the failures cluster on the authoritative hosts research most needs
(bls.gov, aeaweb.org, usitc.gov, sciencedirect, fred.stlouisfed.org).
This is a tool fix that helps any control structure, and passage
retrieval depends on it.

- **Headers:** a realistic browser-like User-Agent and Accept headers
  (also on the roadmap's "Bugs and cleanup" list). Decide and document the
  robots.txt policy.
- **Failure-aware fetching within a run:** remember hosts that failed with
  a block-class error (403/429/challenge pages) and return a synthetic
  "host blocked this run" result instead of re-fetching. This follows the
  existing url_content dedup interception pattern and doesn't consume
  per-run call limits.
- **Classify failures** in the error string (blocked / timeout / not found
  / unsupported content), so the harness's `url_content_failures` can be
  broken down by class.

**Tests:** header construction; blocked-host memory and synthetic result;
failure classification; no limit charge on synthetic results.

**Gate:** harness sweep shows the url_content failure rate down against
the 2026-09-14 numbers, with failures broken down by class. Hosts that
still block are listed as candidates for API tools (see
`parked/additional-default-tools.md`: FRED, SEC EDGAR, OpenAlex).

## Step 4 — Park original Phases 4, 5, 7

Move their designs to `agent-docs/parked/` with mechanism detail intact,
add entries in `agent-docs/index.md`, and mark them in the plan's status
table.

- **Phase 5 (per-fact fan-out):** points to research-agency Phase 4 /
  grind-mode Stage 2. Build the per-fact sub-loop once, there, with the
  agent choosing which facts get it.
- **Phases 4 (enforced registers) and 7 (feedback memory):** recorded as
  candidate internals of that per-fact sub-loop. Once a fact is chosen,
  enforced query variety and zero-yield reformulation are what the
  sub-loop should do.

No code changes. The query-writer stays behind
`research.query_writer_enabled` (default false).

## Step 5 — Passage-level retrieval (deferred)

Original Phase 6, minus fetch unblocking (Step 3). Resume after
research-agency Phase 1 reports. If agency raises coverage, queried recall
becomes the binding factor, and passage ranking is the strongest lever on
it. Design and gate unchanged: [retrieval-quality-plan.md](retrieval-quality-plan.md)
§Phase 6.

## Caveat

The harness measures one research invocation with no review or retry
routing, so its coverage understates the full pipeline. It's well suited
to comparing research-step variants, but it doesn't predict end-to-end
judge scores. Research-agency Phase 0's external baselines cover that.

## Verification (every step)

- Backend, from `backend/`: `uv run pytest tests/ -q -x
  --ignore=tests/test_url_content.py` (plus `moira_eval_tests` where
  touched), `.venv/bin/ruff check`, `.venv/bin/ruff format --check`.
- Frontend: not touched by Steps 1–4.
