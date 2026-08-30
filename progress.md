# Progress Log

Monthly record of implemented improvements to MOiRA's research pipeline —
things that have actually shipped and been verified. One section per month.
Each entry records: what improved (grouped by kind), the headline eval
picture for the period, and failure modes eliminated vs migrated. Unbuilt
designs and deferred plans do NOT belong here — record those in
`agent-docs/parked/`. Raw scores live in `EVAL_LOG.md`; this file records
the capability story behind them.

---

## 2026-08 (2026-07-22 → 2026-08-29, planning-freedom branch)

**Headline:** general-rubric aggregate flat at ~106/150 (within the ±4/question
noise band) — but achieved at half the retrieval budget (hard 10-search cap vs
~19 uncapped in July), a third of the context window (Q5/32K vs Q4/84K), and a
5K citation store. First 6/7 PASS batch ever recorded (2026-08-25, `1e5ebc7e`,
after the eval-harness correction below). Per-question floors rose across the
board (jazz 9→15, tyranitar 7→10 worst case); trade and telescope stabilized.

### Reliability — failure classes eliminated (each observed live, zero since)

- Context-overflow crashes from tool-result feedback (tyranitar `fdaeb0e2`):
  per-result feedback cap + hypermedia URL pruning + recall step-limit.
- Report JSON parse failures (6 of 284 historical report steps): deterministic
  quote/escape repair ladder in `_parse_json_object`; all 6 historical
  payloads now parse. Unparseable output still raises loudly by design.
- Zero-call research passes + non-converging reviewers (`9d8d6dc2`): structural
  `research_progress` (new_facts/stalled) signal; stalled passes override both
  routers to evaluation/report.
- Duplicate and near-duplicate search queries: run-scoped ledger with
  IDF-weighted similarity interception (threshold 0.65, calibrated on 3,475
  historical queries), live-verified on `9d8d6dc2`.
- Recall-source circularity on snippet-only citation stores: citation `depth`
  marker (page vs snippet); recall refuses snippet-depth citations and points
  at `url_content`.
- Unsupported conclusions and junk facts: zero across every branch batch.
- Eval mis-scoring: July's 32%-wrong-run bug, then this month's hollow-capture
  phantoms (checkpoint-resumed runs judged with 2-step captures). Capture now
  coalesces all attempts sharing a `user_message_id` (same stitching as the
  conversation UI); `--commit-sha` re-judge support in `eval:score`; the
  `1e5ebc7e` batch was re-judged and corrected (4P/3F → 6P/1F).

### Efficiency

- 10 web_search/run cap binding with equal-or-better scores vs July's 19–27.
- URL pruning: ~10x drop in URL density in PokeAPI feedback windows
  (~1.7K tokens/run measured on `065e23cc`); `type_retrieve` damage tables now
  URL-free.
- Verified-facts-per-search up to ~2.0 where `url_content` fires (water) vs
  ~0.5–1.0 search-only runs.

### Measurement and instrumentation (didn't exist in July)

- Planner/researcher metric dimensions per question (evidence-request
  granularity, domain-first share, targeted-fact resolution, stalled-pass
  count, dupe interceptions, verified-facts-per-search).
- Request attribution (`request_id` echo, schema-augmented native tool defs),
  attempt ledger, per-pass progress signals — all in step details.
- Step-detail JSON schema + offline validator + `step-detail-schema` agent
  skill; full-DB sweeps (3,863 steps) usable for archaeology.
- Deterministic harness: continue-on-failure batches, attempt coalescing,
  judge-only re-judging, transient-5xx retries in the inference client.

### Failure modes migrated (not eliminated — now named and stage-attributed)

- **telescope-mount-cost** — evaluation critique gap: passes conflated claims
  (class properties attributed to the subject, e.g. calibration confusion with
  manufacturing overhead).
- **tyranitar-ou** — partner-entity verification gap: rigor applied to the
  subject (PokeAPI) but never to recommended partner entities.
- **jazz-trumpeters** — acquisition variance + report recalibration: weak
  conclusion set presented hedged in the main answer instead of downgraded.
  Jazz has failed 11 of 13 batches all month; the one structural failure.

### Not moved

- The ~106/150 aggregate ceiling; run-to-run variance (retrieval sampling
  dominated); judgment quality (levers designed but not implemented — see
  `agent-docs/parked/judgment-quality-levers.md`).
- n=7 with ±4/question noise means single-batch PASS deltas are not
  meaningful; conclusions should rest on 2+ batches (the rule from 
  planning-freedom Phase 6).
