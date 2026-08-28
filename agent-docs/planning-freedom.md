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
| 2 | Defect fixes: extraction-fallback corruption, request-bundling guard | Complete — gates held on batches `d0dcd2f`/`d0dcd2fb` and 08-25/26 smoke runs (zero junk, uncited-unverified, or unsupported facts) | Junk facts eliminated; unit tests pass |
| 3     | Traceability: `request_id` echo, strict request matching, retry feedback | Complete — verified on 08-25 runs (attribution + carry-over confirmed; recall collapse fixed via 3.1) | Every tool call attributable to one request |
| 3.1   | Store visibility + recall discipline: citation depth/linked-facts table, within-batch recall dedup | Code complete — runtime verification pending next retry-prone run | Planner sees store depth; no same-batch duplicate recalls |
| 3.3   | Context budget management: per-result feedback cap, citation limit retune, URL-field pruning | Complete (`cb87020`, `4bd76a0`; recall step-limit 4/pass verified live on run `3322e4c7`) ; 3.3.3 rolling compression deferred | No research-loop context overflow on wide fan-out or recall-heavy rounds |
| 4     | Query discipline: IDF-weighted duplicate interception (run-scoped, hard-reject), coverage-driven rounds, fact-append dedup | 4a implemented + calibrated + **runtime confirmed** (run `9d8d6dc2`); 4b shipped as structural progress gate (revised design); 4c fact-dedup calibrated on snapshots | Duplicate queries intercepted mechanically; threshold measured from historical pairs; stalled research forced to evaluation; duplicate shell facts blocked at append |
| 5     | Measurement: planner/researcher dimension metrics in eval harness        | Code complete (`capture.py` + `metrics.py`, 28 unit tests; live values on run `2276d7c9`) | Eval batch emits both dimensions            |
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
  request matching (spike: loose `name in candidate_tools`; now strict
  per-request promotion via `request_id`, Phase 3);
  `_apply_discovered_facts` new-fact and claim-fallback paths
  (~`research.py:780-887`; guarded in Phase 2)
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
| Better tool selection during research | Plumbing fixed (Phase 3 strict matching); behavior unmeasured | Phase 5 measurement |
| Working fallback cascade | Unmeasured — advisory only, no instrumentation | Phase 5 measurement |
| Fewer pathological/broad queries | Yes — duplicates intercepted live (2 on run `9d8d6dc2`), zero false rejections | Confirmed; residue (synonym re-rolls) accepted |
| Better quality without excess cost | Neutral-to-worse so far (cap hit on duplicates) | Phases 2–4, judged at Phase 6 |
| Easier-to-debug trajectories | Working — request_id echo verified on 08-25/26 runs (unattributed calls are model-initiated supplementary searches) | Complete (Phase 3) |

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

**Implementation (complete):**
- `EvidenceRequest.id` (`models/knowledge.py`) — mechanically assigned
  `req0001...` in planning (`_assign_request_ids`); planner never emits IDs.
- `ToolCall.request_id` (`tools/base.py`) — attribution-only field, never sent
  to executors. Text-mode parsing reads it from the call dict
  (`_extract_tool_calls`, `_parse_tool_calls`); native mode pops it out of the
  model's function arguments in `_validate_and_filter_calls`.
- Strict promotion in `_process_execution_results`: an attributed successful
  call promotes only its own request's unknown facts; unattributed and
  unknown-ID calls promote nothing (`_cleanup_empty_claims` still reverts
  claimless promotions).
- Attempt ledger: `ExecutionState.request_attempts`
  (`{request_id: [{tool, query, success, results}]}`), recorded per call,
  persisted across research invocations; planning re-links it to regenerated
  request IDs by target-fact overlap (`_carry_over_attempts`, dedup by
  tool+query).
- Retry prompt: new `research.system_request_outcomes` section renders
  per-request tried queries + unresolved facts, with an explicit
  change-strategy instruction.
- Prompts: `request_id` documented in `research.system` tool_calls spec (+
  example), native-mode instruction + `research.user`/`research.user_native`
  headers updated to show the ID column.

**Verification:** 15 new unit tests (parsing, argument popping, strict
promotion — own/unattributed/unknown-ID, ledger recording, outcomes rendering
incl. resolved/unattempted skips, ID assignment, carry-over overlap + dedup,
schema augmentation); full suite 863 passed, ruff clean.

**Runtime smoke test (08-25 trade rerun, run `997dbddd`)** — pipeline healthy:
fresh run (no checkpoint reuse), 3 research passes (10+17+12 calls), retry
loop worked, 10/15 facts verified (reviewer-driven), 2 verified conclusions,
zero junk facts. But **request_id echo failed 0/39**: the model never emitted
the undeclared argument, so the ledger stayed empty and the retry outcomes
section never fired. Root cause: native tool-calling models emit arguments
matching the *declared* schema — an undeclared parameter is dropped even when
the prompt asks for it. Fix: `_augment_tools_with_request_id` declares
`request_id` as an optional property on every tool schema passed to native
chat completions (popped before execution as before). Note: zero attribution
is a benign failure mode — fact verification is reviewer-driven
(`research_review.py:268`), not auto-promotion-driven, so coverage doesn't
collapse; only traceability and retry feedback are lost.

**Schema fix verified (08-25 run `08afc2e2`, trade-policy single-pass):**
attribution now works — the ledger recorded `req0001`–`req0006` (1 attempt
each across 6 requests; 10 calls total, so 4 calls went unattributed — the
model echoes request IDs when it follows a request and omits them on
self-generated supplementary searches). Run itself: clean single pass
(no retries — outcomes section correctly stayed out of scope), 10/12 facts
verified, 5 verified conclusions, 0 omitted, 4278-char answer. One residual
gap found and fixed: `active_run.py` `_handle_event`'s `tool_result` branch
built a fixed 5-key entry and dropped `request_id` from persisted
`detail.tool_results` (attribution survived only in the compact ledger
counts). Added the key passthrough + 3 new tests in
`backend/tests/test_active_run.py` (new file — module previously untested).

**Retry runs verified (08-25, water `d9e0e892` + telescope `e942fa33`):**
request_id persisted per call, ledger carry-over across planning
regenerations confirmed (water pass 3 `req0006: 3`), zero repeated
web_search queries across passes. Attribution rates: water 11/14, telescope
18/39 — unattributed calls are model-initiated supplementary searches. But
these runs exposed a **recall-collapse failure mode**: on retry, planners
restricted candidate tools to `recall_source` alone, and the water run's
store held almost nothing relevant (pass-1 searches had returned junk
arxiv papers on keyword overlap — "water distribution networks", "garbage
collection"). Water retried 3× recaling the same single relevant citation
(`cit012`) and finished 2/9 facts verified, 1 conclusion, 1277-char answer.
Telescope's pass-3 recall mining (20 calls over 41 stored citations) was by
contrast productive — recall legitimately produces new fact↔citation links
(both runs' only new links came from recalled content), but it is
*undirected*: it links what content supports, not what the plan needs.

Fixes applied for this failure mode (Phase 3.1):
- **Evidence-store visibility for the planner.**
  `_format_prior_citations` (`_helpers.py`) now renders a markdown table
  (`id | depth | linked facts | title`) instead of `id | title | URL`.
  `depth` = `page Nk` (full stored content, mineable) vs `snippet`
  (search-result excerpt, nothing left to extract); `linked facts` inverts
  `fact.citation_ids` to show which sources are tapped vs unmined. URLs
  dropped (planner never fetches); pipes in titles escaped. Both
  `planning.system_retry_context` and `research.system_retry_context`
  received column explanations + rules: recall is justified for
  `page`-depth unmined sources; a store of snippet/fully-linked sources
  means recall is exhausted — search instead; a retry round shouldn't be
  recall-only unless multiple `page`-depth sources remain unmined.
- **Within-batch recall dedup** (`_execute_tools`, research.py): duplicate
  `recall_source` calls for the same citation in one batch get a synthetic
  "already recalled above" pointer (no content re-injection, up to 5K chars
  saved per repeat) and a call-count decrement, mirroring the url_content
  URL dedup. Scope is deliberately the batch, not the pass: cross-round and
  cross-pass re-reads stay legal because earlier results scroll out of the
  model's context. Root cause of same-batch duplicates: planner emits N
  recall requests for related facts, model best-matches each onto the same
  only-relevant citation.
- Tests: 4 dedup tests in `test_research_recall_source.py` (same-batch
  dedup, count decrement, distinct citations pass through, cross-batch
  re-read allowed), 3 table-render tests in `test_research_internals.py`,
  updated retry-context assertions in `test_research_node.py`. Suite 872
  passed, ruff clean.

Verification pending the next retry-prone run: planner cites depth columns
when choosing tools; no same-citation duplicate recalls within a batch.

### Phase 3.2 note: root cause upstream of recall collapse

The water run's deeper problem is retrieval quality (pass-1 keyword-stuffed
queries returned irrelevant sources), which no recall-planning fix can
recover — see `agent-docs/retrieval-quality.md`. The depth/linked-facts
table at least makes store poverty *visible* to the planner so it searches
instead of recalling.

### Phase 3.3: Context budget management

**Incident (08-25 run `fdaeb0e2`, tyranitar):** research pass 2 died with
`HTTPStatusError: 400 — request (39457 tokens) exceeds the available context
size (32768)`. Mechanism: the model issued a 7-call fan-out (5×
`pokeapi__pokemon_retrieve`, 2× `pokeapi__type_retrieve`); `RESTTool`
returns Path A content up to 10K chars per call (rest_tool.py:170), and the
native loop feeds each result back *untruncated* (research.py:890 →
research.py:1545-1546). PokeAPI JSON is token-hostile (every field a full
URL), so round 2's request hit ~39K tokens: base prompt + retry sections
(~13K) + ~25K of tool-result feedback. First occurrence in any run to date
— latent defect independent of planning-freedom; any wide fan-out on a
JSON API trips it on a 32K-context model.

This phase is scoped separately from 3.1 deliberately: keep commit change
volume reasonable and let eval deltas attribute cleanly.

1. **Per-result feedback cap (implement first).** Cap each tool-result
   message fed back into the loop at ~3K chars, with a pointer appended:
   full content is stored in the citation (bounded by
   `_CITATION_CONTENT_LIMIT`, see the limit retune below); `recall_source`
   is the deliberate re-read path. Same rationale as url_content's built-in
   cap — if the information isn't in the first few thousand chars of cleaned
   content, odds of it being later are low. Only the model-facing copy is
   capped.

   **Implemented (3.3.1):** `_TOOL_RESULT_FEEDBACK_LIMIT = 3_000` +
   `_cap_feedback_body` in research.py, applied in
   `_process_execution_results` to both feedback paths — Path A structured
   bodies (`content`/`snippet`) and Path B unstructured outputs. The
   appended pointer names the citation and the exact `recall_source` call
   to re-read it. **`recall_source` results are exempt** — recall is the
   re-read path the pointer promises; capping it would make stored content
   above the feedback cap permanently unreachable (it remains bounded by
   the citation content limit and within-batch dedup). Zero-results Path A
   feedback was already bounded (`_SNIPPET_MAX_LENGTH`). Tests:
   `TestFeedbackCap` (helper boundary, at-limit passthrough, Path B cap +
   storage fidelity, recall exemption) + updated
   `TestPathAContentFeedback` (truncation assertions + storage check).

   **Smoke test (08-25 tyranitar rerun, run `d5222b28`):** the reproducer
   scenario now completes — all 16 steps, peak research input 29,552 tokens
   (pass 3) vs the 32,768 limit, 3,578-char answer. (Follow-up forensics on
   this run found a second cap bug, fixed in the retune below.)

   **Limit retune + sync fix (3.3.1a):** the citation-content cap is now
   **5K** (was 10K). Rationale: the 10K value was set on 07-21 under a
   large-context workflow model; the current Q5 model has a 32K context,
   and `recall_source` re-injects stored content un-capped — a handful of
   page-depth recalls in one round can approach overflow (telescope pass 3
   did 20). Git history shows 5K→15K→10K same-day knob-turning with no
   recorded rationale; the snapshot safety net in
   `knowledge_summary()` silently stayed at 5K, so DB forensics
   under-reported what runs actually served (the d5222b28 discovery).
   Structural fix, per the no-numbers-in-comments rule:
   - Canonical constant `CITATION_CONTENT_LIMIT` now lives with the
     Citation schema (`models/knowledge.py`); research.py imports it
     (module-local alias preserved for tests); the snapshot slices with it
     directly — no independent number to drift.
   - Tool-side mirrors (`url_content._METADATA_CONTENT_LENGTH`,
     `rest_tool._CONTENT_LIMIT`) set to match and documented by name only.
   - The pipeline now enforces the cap at the Path A storage boundary
     (`_process_execution_results` slices metadata content), so tool-side
     drift can never enlarge stored content — "the tool provides the data,
     the pipeline enforces its own limits" now holds mechanically.
   All comments reference constants by name; no synced numbers remain.
   Suite 876 passed, ruff clean.
2. **URL-field pruning at the model-facing boundary (second commit).**
   General hypermedia rule, not API-specific: prune a `url` field only when
   a sibling identifying field (`name`/`title`/`id`) exists in the same
   object — the URL is then a redundant link. When a URL is the sole
   content of an object, keep it. Real value is signal density (model
   reasons over names, not URL walls) more than token savings; the cap alone
   fixes the incident.

   **Implemented (3.3.2):** shared module `moira/tools/url_pruning.py`
   (`prune_url_fields` on parsed objects, `prune_redundant_urls` on JSON
   strings; prose and sole-content URLs untouched; prefix+suffix field
   matching covers `url_front` and `sprite_url` variants alike).
   **Deviation from the original spec, with rationale:** pruning had to run
   *inside* `_serialize_json_truncated` (rest_tool.py) on the parsed
   object, not on feedback copies after the fact — the metadata `content`
   slice (`output[:_CONTENT_LIMIT]`) chops mid-JSON, so stored content no
   longer parses and post-hoc pruning silently no-ops (measured: 0% pruned
   against the d5222b28 PokeAPI citations when applied at recall time
   only). Consequently RESTTool citations store *pruned* output, not
   untouched output: redundant relationship URLs never serve re-reads, and
   pruning-before-truncation means the 5K window carries identifying data
   (types/stats/abilities) instead of link walls. url_content citations
   (markdown prose) are untouched by construction. research.py still prunes
   valid-JSON model-facing copies (`_cap_feedback_body`, recall_source
   serving) for any JSON that reaches it unpruned. PokeAPI-shaped payload
   measured: relationship walls eliminated, media-asset URLs (sprites/
   cries — URL is the content) retained by the sole-content rule, output
   remains valid JSON. Tests: `TestPruneRedundantUrls` (8, in
   test_research_internals.py) + `TestSerializeJsonTruncatedPruning` (3, in
   test_rest_tool.py). 887 passed, ruff clean.

   **Measured effect (08-26 tyranitar run `065e23cc`, vs live re-fetches of
   the same three endpoints):** URL chars in the 3×3K feedback windows fell
   2,542 → 165 (28% → 1.8% of window); in the 5K store/recall window,
   4,642 → 215. Total reclaimed ~6.8K chars ≈ ~1.7K tokens (~5% of the 32K
   context) — real but modest as a *size* win; the *density* win is the
   story: windows now carry names (abilities, damage relations) instead of
   link walls. Run outcome vs the pre-prune crash run `c815f4a1` /
   post-cap run `d5222b28`: 1 research pass instead of 3, 20 facts (14
   verified) vs 9 (6), 4 pokeapi-backed verified facts — 3 from
   `type_retrieve`'s now-URL-free `damage_relations`. Single run; no
   causal claim. Known limit, accepted deliberately: `pokemon_retrieve`'s
   `types`/`stats` sit behind a 150-entry `moves` array (~379K chars even
   pruned) and remain out of window — the agent worked around it via
   `type_retrieve` + web_search. Per project decision: no API-specific
   tuning (no array-key reordering, no per-endpoint rules); impenetrable
   payload structure is "how the web works" and the agent adapts around
   it. Evidence base is a single JSON domain (PokeAPI) — treat the
   percentages as indicative until more APIs are exercised.

   **Residual exposure (08-26 run `c815f4a1`, post-cap/retune/pruning):**
   research pass 3 *still* overflowed (37,981 vs 32,768 tokens) on a round
   of 10 `recall_source` calls — recall re-injection is exempt from the
   feedback cap by design and remains the one unbounded growth path
   (pruning shrinks each recall's payload; it does not bound their count).
    Fix direction, per the project's no-shadow-budgets principle (call-count
    limits are guardrails; char-accounting alongside the cost budget is not
    wanted): **no per-round recall char budget.** Resolution of the options:
    1. *Interim guardrail — DONE (08-26):* recall `tool_call_step_limit`
       lowered 20 → 4 (per-run 50 → 12) in the tool config + seeds
       (`standard.py`). A "step" is one research pass (baseline captured at
       invocation start, `research.py:410-423`), so this bounds the whole
       pass's re-injection at ~4 × 5K chars ≈ ≤7K tokens even for URL-dense
       JSON — worst case with a retry-heavy base prompt lands ~25K of 32K.
       Sizing rationale: observed recall demand is small when the store is
       poor (water: ≤2/pass) and large-fanout mining is exactly the behavior
       that crashed; legitimate deep reading can spread across planning
       retries. Verified live on run `3322e4c7` (single-pass, 3/4 recalls,
       no overflow; the rejection path itself remains exercised only by the
       shared limit code web_search hits every run).
    2. *Structural:* 3.3.3 rolling compression below — now justified
       specifically if overflow recurs even under the lowered guardrail.
    3. *Long-term:* the proposed `summarize_source` tool
       ([`summarize-source.md`](summarize-source.md)) removes the need to
       re-inject whole sources into the caller's context at all — deep
       reading happens in a sub-context and returns compact facts. That
       plan owns this problem now; this plan tracks only the crash-safety
       interim.
3. **Rolling compression of older rounds (defer until 1+2 measured).**
   Demote round ≤ N−1 tool messages to snippets when a new round starts.
   The cap defers but doesn't eliminate growth (~`calls × cap` per round);
   justified now only if recall-heavy or wide multi-round runs still
   overflow after the cap + pruning (+ interim recall guardrail). Most
   invasive (mutates adapter-tracked history) — needs evidence first.

**Verification:** unit tests for cap + pruning; a wide-fan-out run stays
under the model context limit; eval delta attributable per commit (cap
commit separate from pruning commit).

**Deferred (this phase):** a "grep + neighborhood" focused-retrieval tool —
search stored citation content and return only the relevant section ±
context. Powerful but complicated; revisit after retrieval-quality work.

#### Calibration forensics (done 08-26, script replayed all history)

Replay corpus: 275 runs / 3,475 web_search queries from `data/moira.db`,
each query scored against its priors sequentially (at-scoring-time DF,
run-scoped — mirrors the intended interception exactly). Results:

- **42 normalized-exact duplicate pairs** historically (≈1.2% of queries)
  — rejected outright by rule, independent of threshold.
- **Threshold table** (% of distinct-pair queries whose max weighted
  similarity to any prior clears the line): 0.55→9.7%, **0.65→4.8%**,
  0.75→2.0%.
- **Precision banding from eyeballed examples:** everything measured
  w≥0.84 is an unambiguous word-shuffle dupe (15/15 inspected);
  the [0.52–0.63] band *interleaves* both classes — lazy-modifier dupes
  ("+Rock Ground Steel", "list critics") sit next to legitimate
  entity/angle changes (Kingambit-vs-Excadrill template query 0.58,
  Bling-vs-SHELLPRO product swap 0.53). IDF weighting already pushes the
  entity-swap class down out of clear-dupe territory; no lexical cut can
  split the residue.
- **Adversarial pair check:** the original tyranitar trio ("…tier list
  Pokemon viable" / "Smogon …rankings 2024" / "…current rankings Smogon")
  scores 0.08–0.44 across all similar historical sequences — below any
  sane threshold because each re-roll carried fresh tokens (`smogon`,
  `rankings`, years). Confirms the accepted limit: synonym re-rolls are
  4b/reviewer territory, explicitly not the guardrail's job.
- Cost asymmetry decides the cutpoint: a missed dupe wastes one Brave
  call; a false rejection blocks real acquisition. Precision favored.

**Chosen threshold: reject when max weighted similarity ≥ 0.65**
(plus normalized-exact always). Expected incidence ≈5% of queries,
i.e. ≤1 interception per typical run — a guardrail, not a straitjacket.
Threshold lives as a module constant until eval evidence argues for
exposure in config.

### Phase 4: Query discipline

**Design decided (08-26).** Two mechanisms; both mechanical, stdlib-only,
and calibrated against historical data before finalizing thresholds.

#### 4a. Mechanical near-duplicate interception

**Decision summary** (rationale recorded from design discussion):

- **Hard-reject, not warn-through.** Deterministic and symmetric with the
  recall/url_content dedups; warn-only invites re-roll thrashing.
- **Run-scoped ledger.** Scored against every web_search query issued this
  workflow run across all research passes — cross-pass hammering is the
  observed failure mode (`req0001` accumulating attempts across planning
  retries on water/telescope/tyranitar), so step-scoping would leave the
  biggest hole open. Brave returns ~the same results for an identical
  query regardless of context reset, so a cross-pass repeat buys zero new
  information; materially different phrasing slips under the threshold
  naturally (the escape hatch *is* the mechanism).
- **IDF-weighted overlap, not raw Jaccard.** Raw Jaccard is uniform-weight
  set arithmetic: adding `excadrill` (discriminative) or `rankings`
  (boilerplate) to a query scores identically. The weighted form:

  ```
  sim(A,B) = Σ IDF(w)·[w ∈ A∩B] / Σ IDF(w)·[w ∈ A∪B]
  IDF(w)   = log((N+1)/(df+1)) + 1     # N = prior queries; df at scoring time
  ```

  DF is computed **at scoring time over prior queries only**, so this
  run's own boilerplate down-weights itself, while a fresh discriminative
  token (`excadrill`, a site name, a scope word) pulls similarity down
  decisively. Normalized-identical queries reject outright without
  scoring.
- **Known limit, accepted:** no lexical metric catches synonym re-rolls
  (tyranitar's trio survived partly via fresh tokens `smogon`/`rankings`
  that were semantically redundant). That residue belongs to 4b's
  discipline and the reviewer, not to the guardrail. Known-bad historical
  pairs become adversarial calibration cases — flagged if possible,
  explicitly accepted as misses otherwise.

**Mechanism (as implemented):** interception lives in `_execute_tools`
(research.py) as a dedicated partition layer: web_search calls are split
through `_partition_web_search_dupes`, which scores sequentially against
the accepted set so dupes *within* one fan-out batch are caught too. The
ledger is `ExecutionState.issued_queries` (original query text, seeded in
`research()` from state and persisted back — covers unattributed
supplementary searches, which the request-attempt ledger does not).
Helpers: `_tokenize_query` / `_normalize_query` (stopword-stripped,
order/punctuation-insensitive canonical form; normalized-identical rejects
outright without scoring), `_query_similarity` (IDF-weighted overlap, DF at
scoring time over priors only). Rejected duplicate ⇒ synthetic ToolResult
via `_build_synthetic_dupe_result` ("you already searched «X» … rephrase
materially or target a different evidence request") + call-count decrement
(free: no Brave call, no budget hit, cap unchanged), mirroring the existing
dedup pattern. A rejected query never enters the ledger (rejections must
not poison the IDF corpus). Steady-state cost ~20 lines of scoring code per
call over ≤20 prior queries — microseconds; stdlib only.

#### 4b. Coverage-driven rounds

**Revised design (08-26, after run `9d8d6dc2`).** The original sketch — a
per-request checklist rendered before each extra round — was superseded by
what the live run exposed: the reviewer ignored yield signals entirely
(it retried with near-identical verdicts even after a **zero-call research
pass**), so the fix had to be structural routing, not more prompt prose.

Shipped as two pieces:

1. **Exhaustion protocol (researcher side).** Duplicate-rejected attempts are
   recorded in the ledger (`deduped: true`) and rendered distinctly in
   `_format_request_outcomes` ("Rejected as duplicate …"); prompts direct:
   a request showing only rejections is exhausted; if *every* unresolved
   request is exhausted, stop issuing calls and finish — no filler turns.
2. **Structural progress gate (router side).** Each research pass records
   `research_progress = {new_facts: N, stalled: bool}` in execution state
   (`_count_newly_claimed_facts` vs. a pre-pass claim snapshot). Both
   routers honor it ahead of the model's route: a stalled pass forces
   review-retry → `evaluation`, and evaluation-declined →
   `report_generation`. Reviewer and evaluator prompts receive a
   `{research_progress_block}` signal telling them to acknowledge the stall
   and frame remaining gaps as known unknowns.

Runtime validation on run `9d8d6dc2`: 4a intercepted 2 duplicates live
(one fuzzy w=0.81, one normalized-exact permutation), executed queries all
distinct, ledger counters matched offline replay exactly, zero false
rejections. The zero-call stall this phase targets occurred one cycle
earlier than this fix — verified by router unit tests, next smoke run
should show it live.

#### 4c. Fact-append dedup (added 08-27, after run `008200e8`)

The stall run exposed a second minting surface: pass-2 planning/research
produced **byte-identical shell facts** (f016/f017: `Clefable | Defensive
type coverage and team synergy principles` twice), plus per-candidate
variant clones. The append path in `_apply_discovered_facts` accepted any
`fact_id: null` entry with a non-empty `fact_needed`, without comparing
against existing facts.

**Calibration forensics first** (`/tmp/opencode/fact_forensics_v2.py`,
snapshot-replay methodology; raw discovered_facts streams are not
persisted — per-round responses overwrite and extraction calls aren't
stored): across 287 runs / 2909 facts —

- **Tier-1 normalized-identity collisions: 17 incidents**, all real:
  per-Pokémon shell twins (c31d: Excadrill/Garganacl/Corviknight/Hydrapple),
  the Clefable pattern, and one wholesale re-decomposition (9439: water
  f001–f006 duplicated as f007–f012 with identical questions).
- **Fuzzy near-misses:** every comparable pair ≥0.8 inspected is
  *legitimate* decomposition (systolic-vs-diastolic, ability-vs-moveset,
  normotensive-vs-hypertensive twins scoring ~0.77–0.83). IDF weighting
  correctly holds these apart, but no safe fuzzy cutoff exists between them
  and hypothetical paraphrase dupes (which don't appear in history).

**Shipped design — narrower than the two-tier reject originally discussed:**

1. **Hard reject on exact identity only**: token-normalized
   `(subject, fact_needed)` compared against all existing facts + entries
   appended earlier in the same response. Token-normalization (not just
   strip/lower) on both fields keeps entities robust to punctuation/case
   drift ("chien pao!" == "Chien-Pao"). Identity helper `_fact_identity`;
   check helper `_is_duplicate_fact`; wired at all three append sites
   (shell facts, overflow splits, claim-fallback) with a dedup ledger.
2. **Advisory-only similarity warnings** at ≥0.75 for equal-or-empty
   subjects (`_ADVISORY_FACT_SIMILARITY_THRESHOLD`): logged, not blocked —
   accumulates signal for a future cutoff without risking false merges.

Tests: identity-match rejections (byte-twin, case/order variant,
within-response repeat, different-subject-passes guard); legit-twin
must-pass pair from dd95; end-to-end f016/f017 shape yields one fact.
Suite 912 passed, ruff clean.

#### Calibration-first sequencing

1. **Forensics script (step 0, no product changes) — DONE** (results above):
   replayed every historical web_search pair; raw-Jaccard vs IDF-weighted
   distributions compared; cutoff picked from measured data.
2. **Interception implementation + unit tests — DONE (08-26).**
   `_partition_web_search_dupes` + `_query_similarity` + tokenizers in
   research.py; `ExecutionState.issued_queries` seeded/persisted in
   `research()`; threshold 0.65 (`_QUERY_DUPE_THRESHOLD`). 13 tests
   (helper units + interception behaviors: shuffle reject, normalized-exact
   catches, entity-swap pass at ~0.21, sequential in-batch scoring,
   cross-pass seeded ledger, no-ledger-poisoning, call-count decrement).
   Suite 899 passed, ruff clean. **Runtime confirmed on run `9d8d6dc2`**:
   live intercepts (fuzzy w=0.81 + normalized-exact permutation), distinct
   executed queries, counters matched replay, zero false rejections.
3. Progress gate + exhaustion protocol — DONE (08-26), see revised §4b.
4. Smoke test on a hammer-prone question (water is the classic);
   confirmation folds into Phase 5 eval batches.

**Verification:** forensics report with measured threshold ✓; unit test
intercepts a near-duplicate (incl. ≥0.84 shuffle pairs as must-catch and
the Kingambit entity-swap pair as must-pass); stalled-pass routers force
evaluation (graph tests ✓); live confirmation on `9d8d6dc2` ✓ for 4a,
progress gate verified by router tests pending next live stall.

### Open decisions taken during design (recorded so they don't reopen)

| Question | Decision | Why |
|---|---|---|
| Hard-reject vs warn-through | Hard-reject | deterministic guardrail; warn invites thrashing |
| Step-scoped vs run-scoped ledger | Run-scoped | cross-pass hammering is the observed failure |
| Threshold source | Measured in DB forensics | prevalence before severity (project rule) |
| Catch synonyms? | No — accepted limit | lexical metric can't; 4b owns that residue |

### Phase 5: Measurement

**Status: code complete** — both dimensions computed per question and
written into `moira_eval/results/<sha>/<question>.json` via
`compute_metrics` (additive keys per the result stability rule).

**Planner dimension** (from `planning` steps' `structured_output.evidence_requests`,
captured by `_extract_planning_attempts`): `evidence_request_count`,
`avg_facts_per_request`, `max_facts_per_request`, `multi_fact_request_count`,
`distinct_targeted_fact_count`, `domain_first_request_share` (share of
requests whose first candidate is a non-generic tool — the preference-quality
heuristic), `unknown_facts_total` / `unknown_facts_targeted` /
`unknown_facts_never_targeted` (coverage measured against the run's final
unknown set — never-targeted unknowns are planner misses).

**Researcher dimension** (from tool trace + planning attempts + research
pass signals): `verified_facts_per_search` (denominator = EXECUTED
web_search calls only; duplicate-intercepted calls did no retrieval work),
`executed_web_search_calls`, `duplicate_queries_intercepted` (synthetic
rejection results, matched by output prefix — `active_run` does not persist
the metadata dict on tool entries), `targeted_fact_resolution_rate`
(verified ∩ targeted / targeted — untargeted unknowns stay on the planner
side), `stalled_research_pass_count` (Phase 4b progress-gate firings;
pre-4b runs record `None`, counted as not stalled).

Capture additions (`moira_eval/capture.py`): `planning_attempts`,
`research_passes`.

**Verification:** 28 metric unit tests (fixture-based, incl. executed-only
denominator, targeted-vs-untagged resolution split, legacy-run tolerance);
live end-to-end on run `2276d7c9` emits plausible values (13 requests, avg
1.0 facts/request, 0 multi-fact — the bundling guard holds; all 3 final
unknowns were targeted; 1 stalled pass). Batch/diff consume the new keys
without schema changes. Two pre-existing failures in
`test_evaluation_invoke.py` (CLI POST-count assertions) fail on the clean
tree too — unrelated to this change.

### Phase 6: Decision gate

Run ≥2 Q5 batches after Phases 2–4 (same commit), compare against main's
08-18 Q5 baseline.

**Prerequisite:** fix the eval-harness stale-checkpoint defect first — two
prior batches re-judged a checkpoint-resumed trade-policy run (`f20c5c4b`,
evaluation + report steps only, zero research) instead of executing a
fresh run. Left unfixed, it silently poisons batch-level comparisons.

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
- **"Grep + neighborhood" focused retrieval over stored citation content** —
  returns only the section matching a query ± surrounding context, as an
  alternative to re-reading whole pages via `recall_source`. Complicated;
  see Phase 3.3 deferred note and `retrieval-quality.md` (passage-level
  retrieval). The LLM-driven counterpart — directed whole-source
  extraction in a sub-context — now has its own pre-plan:
  [`summarize-source.md`](summarize-source.md).
