# Planning Freedom: Evidence Requests over Prescribed Tool Calls

Experiment plan for the `planning-freedom` branch. The implementation already
exists as a spike; this document frames it as a measurable experiment, records
the evidence so far, and defines what must be fixed and measured before an
adopt / iterate / rollback decision.

## Phase Tracking

| Phase | Scope                                                                    | Status                          | Decision gate                               |
|-------|--------------------------------------------------------------------------|---------------------------------|---------------------------------------------|
| 0     | Spike: `evidence_requests` replace `ToolCallPlan`                        | Complete (`771b8d5`)            | —                                           |
| 1     | Baseline evals + run forensics                                           | Complete (07-23, 08-24 batches) | —                                           |
| 2 | Defect fixes: extraction-fallback corruption, request-bundling guard | Code complete — pending eval confirmation | Junk facts eliminated; unit tests pass |
| 3     | Traceability: `request_id` echo, strict request matching, retry feedback | Not started                     | Every tool call attributable to one request |
| 4     | Query discipline: mechanical dedup, coverage-driven rounds               | Not started                     | Duplicate queries intercepted mechanically  |
| 5     | Measurement: planner/researcher dimension metrics in eval harness        | Not started                     | Eval batch emits both dimensions            |
| 6     | Decision: multi-batch eval vs main 08-18 baseline                        | Not started                     | Adopt / iterate / rollback (criteria below) |

## Motivation

The prior planner collapsed "what evidence do I need?" into "how do I retrieve
it?". For each wanted fact it prescribed a particular tool call with a
particular query. This caused:

- **Recomposition** — the planner bundled multiple facts into broad queries,
  producing evidence less directly useful for individual facts.
- **Precommitment** — research was locked to particular retrieval choices
  (especially web_search) even when a discovered tool was the better source.
- **No adaptive room** — research couldn't respond intelligently to tool
  failure, poor results, or source-specific suitability.
- **Unexplainable deviation** — when research did something different from the
  plan, sensible adaptation was indistinguishable from plan failure.

## Hypothesis

> MOiRA will research more effectively if planning specifies evidence
> requirements and source preferences rather than prescribing individual tool
> calls.

Precise form of the expected outcome:

> Planning can specify the epistemic requirements of research independently of
> its retrieval implementation, allowing the research phase to dynamically
> select and sequence tools while preserving explicit traceability from
> evidence back to the facts it is intended to establish.

## Design

### Architecture shift

```
Old:
  Decomposition → wanted facts → Planning → prescribed tool calls + queries
  → Research executes the plan

New:
  Decomposition → wanted facts → Planning → evidence requirements +
  preferred evidence sources → Research (actual tool selection / queries /
  fallback / adaptation) → evidence → verification / synthesis
```

The planner owns *what* evidence is needed and which sources are preferred.
The researcher owns *how* to retrieve it.

### Schema

`EvidenceRequest` (`backend/moira/models/knowledge.py:80`):

```python
class EvidenceRequest(TypedDict):
    target_fact_ids: list[str]   # facts this evidence should establish
    evidence_needed: str         # description of the evidence itself
    candidate_tools: list[str]   # best-first; web_search typically last
    fallback: bool               # planning-level cascade permission
```

Example:

```json
{
  "target_fact_ids": ["f001", "f003"],
  "evidence_needed": "Tyranitar's type chart (weaknesses & resistances)",
  "candidate_tools": ["pikalytics", "smogon", "web_search"],
  "fallback": true
}
```

### Two different "fallbacks" — do not conflate

1. **`fallback` field (planning)** — a planning decision about whether the
   researcher is expected to cascade beyond the preferred source.
   `false` = canonical source exists, don't gratuitously search elsewhere.
   `true` = no single canonical source; broader retrieval is expected if the
   first candidate is insufficient. Currently **advisory only** (prompt-level;
   nothing enforces cascade behavior mechanically).
2. **Claim fallback (extraction)** — the null-fact-id path added to
   `_apply_discovered_facts` (`backend/moira/workflow/nodes/research.py:864-887`).
   This is the path that produced the junk facts in the 08-24 eval and is a
   Phase 2 defect, not part of the design intent.

### Implemented on the branch (Phase 0 spike)

- `backend/moira/models/knowledge.py:80-97` — `EvidenceRequest` TypedDict
- `backend/moira/workflow/nodes/planning.py:266-299` — parses
  `evidence_requests` from planner JSON
- `backend/moira/workflow/nodes/research.py` — `_format_evidence_requests`;
  request matching at `research.py:760` is currently a loose
  `name in candidate_tools` check; `_apply_discovered_facts` new-fact and
  claim-fallback paths (~`research.py:780-887`)
- `backend/moira/resources/prompts.md` — planning prompt rewritten for
  evidence requests; research prompts say "you decide HOW to query" and add
  "do not repeat exact search queries" (prompt-only, demonstrably insufficient)
- `frontend/src/components/StructuredOutputRenderer.vue` — renders requests

## Evidence so far

### Baseline (main, 2026-08-18, commit `32155047`, Q5_K_XL)

| Question | Score | Status |
|----------|-------|--------|
| flaming-hot-cheetos | 19/25 | PASS |
| future-nostalgia | 18/25 | PASS |
| jazz-trumpeters | 13/25 | FAIL |
| telescope-mount-cost | 20/25 | PASS |
| trade-policy-manufacturing | 17/25 | PASS |
| tyranitar-ou | 12/16 | FAIL |
| water-blood-pressure | 20/25 | PASS |

**5 PASS / 2 FAIL.** Note: the branch's `EVAL_LOG.md` diverged from main's and
lacks this batch — merge main's log before the Phase 6 gate so the comparison
is visible in one place.

### Branch evals

2026-07-23 (`771b8d59`): 4 PASS / 3 FAIL (nostalgia 15 P, tyranitar 13/16 F
with only 4 web_search).
2026-08-24 (`cdc7a27b`, Q5): 4 PASS / 3 FAIL — cheetos 18 P, nostalgia 15 F,
jazz **17 P (+4)**, telescope 21 P, trade 16 F, tyranitar **9/16 F (−3)**,
water 18 P.

Net: **freedom raised variance, not the floor.**

### Noise floor

Same-commit re-runs (main 08-11 vs 08-12, commit `7d024788`) swing up to ±4
rubric points per question (e.g., future-nostalgia 12→16, tyranitar 14→10).
**A single n=7 batch cannot support an adopt/rollback decision.** Phase 6
requires repeated batches.

### Run forensics (data/moira.db, 08-24 batch)

- **tyranitar-ou (`d114bec1`)** — 13 tool calls across 3 rounds including 3
  near-duplicate f004 queries ("Generation 9 OU tier list Pokemon viable" /
  "Smogon gen 9 OU tier list Pokemon rankings 2024" / "Gen 9 OU tier list
  current rankings Smogon"). Hit the 10-search cap without covering all
  requests. All 8 facts and 4 conclusions verified — but the report was 806
  chars (4176 on the pre-branch run) and named no partners despite every
  conclusion naming them. (Report truncation is tracked separately; see
  Deferred.)
- **trade-policy-manufacturing (`4cf60468`)** — the real unknowns (f003, f004,
  f005, f007, f008 — all causal-impact facts) stayed unknown. Meanwhile the
  new extraction paths created f012–f014: `fact_needed` values are fact-ID
  references ("f003|f004"), zero citations, junk claims ("Wasserstein Index
  Generation", "SVAR and DiD methods") that leaked into the report as hedged
  methodology filler. One bundled request targeted [f001, f008] with one vague
  evidence description — too diffuse to form a good query.
- **telescope-mount-cost (`6487e6ad`)** — clean: 8 verified, no junk facts.

### State of the attempt, so far

The evals above are **not a judgment of the hypothesis** — they are a
diagnostic checkpoint on an unfinished attempt. The spike landed the schema
and prompt changes; the supporting machinery (extraction guards, request
attribution, mechanical query discipline, measurement) doesn't exist yet. The
failures observed so far are defects in that missing machinery, not
refutations of the separation-of-concerns idea. Improving fact discovery
remains the goal; the forensics tell us where the remaining work is.

| Expected outcome | Current state | Where it gets addressed |
|---|---|---|
| Better decomposition of evidence needs | Partial — bundling degrades targeting | Phase 2 bundling guard |
| Better tool selection during research | Not yet working — web_search satisfies everything | Phase 3 strict matching; Phase 5 measures it |
| Working fallback cascade | Unmeasured — advisory only, no instrumentation | Phase 5 measurement |
| Fewer pathological/broad queries | Not yet — duplicates returned, bundled requests vague | Phase 2 + Phase 4 |
| Better quality without excess cost | Neutral-to-worse so far (cap hit on duplicates) | Phases 2–4, judged at Phase 6 |
| Easier-to-debug trajectories | Not yet — attribution weaker than per-call `target_fact_ids` | Phase 3 request_id echo |

Phases 2–5 are the actual attempt; Phase 6 is the first fair test.

## Evaluation dimensions

Planner and researcher are scored separately so one can't contaminate the
other's verdict.

**Planner dimension (did planning produce good evidence requests?):**
- *Coverage* — every unknown fact targeted by ≥1 request
- *Granularity* — facts per request (see Phase 2 bundling guard)
- *Preference quality* — is the first candidate tool actually the best source
  (judged, or heuristic: domain tool before web_search)

**Researcher dimension (did research execute the requests well?):**
- *Resolution rate* — fraction of targeted facts that reach verified
- *Duplication count* — near-duplicate queries issued per run
- *Cascade behavior* — when the preferred tool fails/inadequate, does another
  candidate get tried before web_search
- *Efficiency* — verified facts per web_search (see
  [`knowledge-efficiency.md`](knowledge-efficiency.md))

**System dimension:**
- Rubric PASS count vs baseline; unsupported claims = 0; tool-call count;
  token cost.

## Implementation Plan

### Phase 2: Defect fixes

**Status: code complete** — implemented as Fix A + Fix B + prompt alignment.
Pending confirmation on the next eval batch.

1. **Guard `_apply_discovered_facts`** (`research.py`):
   - `_is_fact_id_reference(text)` — true when `fact_needed` consists solely
     of fact IDs and connectors ("f003|f004", "f003 and f004"). Such values
     (the model mimicking the pipe-delimited facts display) are treated as
     missing; a cited claim can still be salvaged via the fallback path.
   - Citation gate: a new fact with a claim but zero `citation_ids` enters as
     `unknown` with no claim (re-extractable next round); immediate
     `unverified` requires ≥1 citation.
   - Claim-only fallback (null fact_id, no fact_needed) requires ≥1 citation,
     else the entry is dropped with a warning.
2. **Bundling guard at parse time** (`planning.py`): `_split_bundled_requests`
   splits multi-fact requests into one request per fact (`evidence_needed`
   becomes each fact's own `fact_needed`, falling back to the bundle
   description). Also drops target IDs referencing nonexistent facts.
   Decision: always split — no structured-source exception (see rationale
   below).
3. **Prompt alignment** (`prompts.md`):
   - `discovered_facts` spec (both text-JSON and native-tools variants):
     every claim must include ≥1 citation ID; `fact_needed` must be a
     plain-English description, never fact-ID references. Good/bad example
     pair added.
   - New-fact guidance in both Search-strategy sections rewritten to match
     the guard semantics.
   - `planning.system`: "Target ONE fact per evidence request" with the
     single-lookup exception framed as last-resort guidance; the parse-time
     split is the backstop.

**Why always split (no structured-source exception):** the failure asymmetry
favors splitting — a split type-chart request costs one redundant query
(url_content dedup catches the double-fetch), while an unsplit vague bundle
produced the trade-policy failure. Detecting "single structured source" from
`evidence_needed` text needs fragile heuristics, and the model retains the
prompt-level exception guidance for genuinely shared lookups.

**Verification (done):** `uv run pytest tests/ -q -x --ignore=tests/test_url_content.py`
(845 passed, incl. 6 new `_apply_discovered_facts`/`_is_fact_id_reference`
tests and 5 new `_split_bundled_requests` tests incl. node-level e2e);
`.venv/bin/ruff check`; `.venv/bin/ruff format --check` — all clean.
Remaining: confirm on the next eval batch that no ID-reference facts or
uncited `unverified` facts enter the snapshot (watch the RESEARCH/PLANNING
warning logs — they are now the prompt-adherence signal).

**Phase 2.1 amendment (post-eval forensics, added after batch d0dcd2f):**
the telescope run showed a third extraction defect — the model attached
multiple cited entries with distinct subjects to the same existing fact ID
in one response, and last-write-wins clobbering left an arbitrary survivor
(the real claims were verified as genuine — sourced and extracted — just
mis-mapped to the wrong fact IDs). Fixes:

1. **Overflow split** (`research.py`, `_apply_discovered_facts`): the first
   cited entry for a fact ID updates that fact; every subsequent cited
   entry for the same ID in the same response becomes a new fact keyed by
   its own subject (claim text doubles as `fact_needed`, mirroring the
   claim-only fallback). Uncited overflow entries are dropped.
2. **Prompt rule** (both research variants): "A claim must answer its fact's
   question — what the fact_needed asks for. If you found a related detail
   that does not answer an existing fact's question, do not force it onto
   that fact: record it as a new fact. One entry per fact per response."

Verification: 3 new unit tests (multi-entry split, uncited overflow dropped,
empty-claim does not lock the fact); full suite 848 passed, ruff clean.

### Phase 3: Traceability

1. **Echo `request_id` on each tool call.** Research tags every query with the
   evidence request it targets (prompt instruction + parse). This restores the
   fact↔result attribution the old per-call `target_fact_ids` provided.
2. **Strict matching** — a tool result satisfies the request it was issued
   against (`research.py:760`), not every request listing that tool.
3. **Retry feedback loop** — per-request outcome summary (queries tried, result
   counts, still-unknown facts) fed to the retry planning prompt, replacing
   bare "still unknown" with *why* it failed.

**Verification:** `workflow_steps.detail` shows request attribution for every
call; retry prompt contains the per-request failure summary.

### Phase 4: Query discipline

1. **Mechanical near-duplicate interception** — token-overlap (or embedding)
   similarity vs all queries already issued this run; refuse or force
   rephrase. Prompt-only "do not repeat" demonstrably doesn't hold for a 35B
   local model.
2. **Coverage-driven rounds** — before each extra round, present a checklist
   (request → attempted? → resolved?); next-round queries must target
   unattempted or unresolved requests. Turns `max_extra_rounds` freedom into
   directed freedom.

**Verification:** unit test intercepts a near-duplicate; retry prompt contains
the checklist.

### Phase 5: Measurement

Wire the planner and researcher dimensions into the eval harness batch output
(computed from `workflow_steps.detail` + knowledge snapshots): coverage,
granularity distribution, resolution rate, duplication count, cascade
attempts, verified facts per web_search.

**Verification:** an eval batch emits both dimensions per question.

### Phase 6: Decision gate

Run ≥2 Q5 batches after Phases 2–4 (same commit), compare against main's
08-18 Q5 baseline.

- **Adopt** if: PASS ≥ 5/7 in at least one batch with no batch below 4/7;
  resolution rate and facts-per-search ≥ baseline; unsupported = 0;
  duplication ≈ 0; request attribution present.
- **Iterate** if mixed (e.g., quality holds but traceability incomplete).
- **Rollback** if consistently below baseline after the fixes.

On adoption: update `core/research-loop-data-flow.md` (still documents
`tool_call_plan`), merge `EVAL_LOG.md` from main, move this doc to
`completed/`.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Freed researcher burns budget on duplicates | Phase 4 mechanical interception (not prompt-only) |
| Bundled requests produce vague queries | Phase 2 parse-time split |
| Extraction fallback corrupts the fact model | Phase 2 guards + unit tests |
| Attribution stays loose, debuggability claim fails | Phase 3 request_id echo is a hard requirement for adoption |
| Eval noise drives a wrong adopt/rollback call | ≥2 batches; ±4-point same-commit variance documented above |
| Cascade never actually happens despite `fallback` | Phase 5 measures cascade behavior; enforcement deferred (see QUESTIONS.md) |

## Deferred

- **Report completeness** — tyranitar's 806-char answer that omitted every
  conclusion is a report-generation failure, not a planning-freedom failure.
  Track separately (candidate fix: every verified conclusion ID must be
  represented in the answer).
- **Mechanical fallback enforcement** — cascades stay advisory until Phase 5
  shows they don't happen; see `QUESTIONS.md`.
- **Constrained decoding** for `evidence_requests` / `discovered_facts` JSON
  (already on `roadmap.md`) — prevents schema drift like
  `fact_needed: "f003|f004"` at the source.
- **Efficiency metrics beyond facts-per-search** — see
  [`knowledge-efficiency.md`](knowledge-efficiency.md).
