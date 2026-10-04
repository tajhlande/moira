# Query-Writer Register Machinery (retrieval-quality Phases 4, 5, 7)

> **Parked:** 2026-10-03 (retrieval-quality-amended Step 4). These three
> phases improve *queried* recall — the hit rate of searches that actually
> run — but the Phase 1 baseline showed planning coverage is the binding
> factor (recall@5 0.108 ≈ coverage 0.21 × queried recall 0.52), and
> coverage is being addressed by giving the research agent more freedom to
> choose its actions ([research-agency.md](../research-agency.md)). The
> designs below are preserved intact for reuse as internals of that plan's
> Phase 4 (director-chosen scoped sub-loops).

## Pickup context

- **When to pick up:** research-agency Phase 4 — the director step that
  chooses which facts get a scoped research sub-loop
  ([research-agency.md](../research-agency.md); the fan-out shape is also
  sketched as grind-mode Stage 2 in
  [grind-mode.md](grind-mode.md)). Phases 4+7 below are candidate
  internals of that sub-loop (register-diverse query generation +
  within-loop feedback); Phase 5 is the sub-loop's execution shape.
- **Why not sooner:** enforced register diversity only helps facts that
  get searched at all. With coverage binding, better query phrasing on
  already-queried facts has a hard ceiling (~queried recall); fan-out
  under today's call limits trades breadth for depth at the request of
  planning, which is exactly the decision the director step should own.
- **Code state at parking (2026-10-03):** the query-writer hook exists and
  is behind `research.query_writer_enabled` (default false,
  `SourceStoreConfig` sibling in `backend/moira/config.py`). The 2026-09-18
  A/B showed the writer fired on ~85% of attributed calls but collapsed to
  one register (102/102 `technical`) — that result is the motivation for
  the code-enforcement below, and remains valid whenever this is picked
  up. The rewrite ledger records `register` per variant;
  `_record_request_attempt` already maintains the per-request outcome
  memory Phase 7 consumes. `research.py` has been heavily modified since
  these designs were written (Step 1–3 observability work) — function
  names below are stable anchors, line numbers are not.
- **Inherited principle:** structured-data rules over prompt-hope — the
  three-register prompt contract did not survive contact with the model,
  so every mechanism below enforces its guarantee in code.

## Register-enforced query generation (original Phase 4)

**Goal:** variant sets are register-diverse by construction — enforced in
code, not prompt-hope. Extracted from the Phase-3 gate (2026-09-18): the
writer hook fired on ~85% of attributed calls across the sweep, but every
rewrite came back in the `technical` register (102/102) — the prompt's
three-register contract did not survive contact with the model.

- **Slot assignment is code's job:** the hook assigns 2–3 distinct
  registers per fact from the fact-type template table
  (retrieval-quality.md §"Fact-type query templates": numeric/spec →
  spec-sheet + product-review registers, causal/medical → scholarly,
  cost/comparative → forum/"vs" colloquial, event/historical → news). The
  writer does not choose registers; it fills them.
- **Fill-and-check:** the writer prompt passes the assigned slots; the
  response validator (`_normalize_response` in
  `backend/moira/workflow/nodes/query_writer.py`) requires one query per
  assigned register. A missing or off-register slot falls back to a
  deterministic templated skeleton built from `fact_needed`/`subject`;
  duplicate registers collapse. Compliance is a structural property of
  the output, never an assumption.
- **No execution change:** one query per fact still executes (first
  variant); `queued_variants` queue for fan-out (below). The rewrite
  ledger already records `register` per variant — it becomes a gate metric
  (register distribution), reported alongside recall by the harness.
- **Tests:** assignment is table-driven by fact type; skeleton fallback
  when the writer omits or echoes slots; off-register relabel-or-drop;
  distribution shows ≥ 2 distinct registers per fact set. Harness variant
  `query-writer-enforced` (fold into `query-writer` if it wins).
- **Design note:** classifying fact type in code (keyword heuristics on
  `fact_needed`) vs asking the writer for it with the assignment — code
  keeps it deterministic; the decomposition node already emits `subject`
  that can anchor it.

**Gate:** enforced vs current query-writer on the same question set, same
judge: register distribution must show ≥ 2 registers in real sweeps,
recall@5 compared against the 2026-09-18 query-writer numbers.

## Per-fact fan-out + fact-type templates (original Phase 5)

**Goal:** issue all register variants per fact; a fact sinks only if every
register misses.

- In the writer hook: when `research.fanout_enabled`, execute all
  variants (each a real web_search call — costs and the per-run call
  limit apply honestly). The dupe guard must compare *across variants*:
  variants are exempt from `_partition_web_search_dupes` against each
  other by design (they are deliberately different phrasings), but still
  deduped against `issued_queries` from prior rounds.
- Result merge: existing URL merge in `_find_or_merge_citation` already
  unifies duplicates; per-fact attribution extends
  `_record_request_attempt` with per-variant outcomes.
- Budget interplay: fan-out trades breadth for depth under the same
  budget and web_search call limits. The harness measures whether to
  raise the limit or keep breadth — no default change until numbers say
  so.
- Templates: the fact-type → registers table feeds code-enforced slot
  assignment (Phase 4 above); fan-out's addition is *execution* of the
  filled variant set, not more prompt surface.
- **Tests:** hook issues N variants once each; cross-variant exemption +
  prior-round dedup; merge behavior; budget accounting (N calls charged).
  Harness variant `fanout`.

**Gate:** harness variant `fanout` vs `query-writer`: recall lift per
additional search spent.

**Research-agency note:** the decision "which facts deserve fan-out
depth" is a planning choice, not a config flag — in the agency plan this
shape belongs inside the director's scoped sub-loop
([research-agency.md](../research-agency.md) Phase 4; the identical
observation is recorded in [grind-mode.md](grind-mode.md) Stage 2).

## Within-run feedback memory (original Phase 7)

**Goal:** mechanical feedback instead of re-rolling guesses.

- **Query outcome memory:** `_record_request_attempt` data already
  exists; feed `queries_tried` + per-query yield into the query-writer
  input (the hook reads the request's ledger). Mechanical rule in the
  writer: a zero-yield register is not re-sampled for the same fact —
  pick a different register (code-enforced choice from the template
  table, not prompt-hope).
- **Pseudo-relevance feedback:** on retry for a request with irrelevant
  results, extract salient terms from whatever snippets returned
  (TF-based, over the returned snippets only) and pass them as
  `corpus_terms` to the writer — reformulate with the corpus's vocabulary.
- **Fact-level provenance (UI parity):** facts resolved by a
  writer/fan-out call carry the winning query + register in
  `request_attempts`; serialize a compact `retrieved_via` hint on the fact
  in `knowledge_summary()` and render it on fact rows in
  `KnowledgePanel.vue`.
- Cross-run query playbook stays deferred (retrieval-quality.md
  §Deferred → parked list there).
- **Tests:** writer input assembly from ledger; register-forcing rule;
  PRF term extraction; provenance serialization + UI badge. Harness
  variant `feedback` (repeats should show tighter variance, not just
  higher mean).

**Gate:** harness repeats: unresolved-fact variance shrinks vs writer /
fan-out artifacts.
