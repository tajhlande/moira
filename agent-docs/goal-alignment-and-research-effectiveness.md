# Goal Alignment and Research Effectiveness

## Phase Tracking

| Phase | Scope | Changes included | Status | Decision gate after eval |
|-------|-------|------------------|--------|--------------------------|
| 0 | Prerequisite commits | Phase 2 URL dedup (uncommitted), web_search cache fix (uncommitted) | Complete (`ce33f98`, `5c5776c`) | Clean git history before prompt work |
| 1 | Search strategy diversity | Change 3 (items 1–4) | In progress — prompts edited, pending eval | Did goal alignment improve? If yes, skip Phase 2 for demo |
| 2 | Derivation core (inference pipeline) | Foundational Change + Change 1 + Change 2 | Deferred (conditional on Phase 1) | `unsupported` stays at 0? Goal alignment >2? |
| 3 | Report hedging (transparency) | Change 4 | Deferred (optional) | Can fold into Phase 2 or skip |

## Problem

Eval batch 8105cc0b (post-Phase-2) showed 5 of 7 questions failing specifically on
**Goal alignment** (score 2/5, below the ≤2 hard-fail threshold). Other rubric
categories score 3-5. The agent is disciplined (good grounding, good citations,
good critique) but **doesn't answer the question**.

### Failure pattern (measured)

| Question                   | Facts decomposed | Verified | Unknown | What stayed unknown                                                      |
|----------------------------|------------------|----------|---------|--------------------------------------------------------------------------|
| telescope-mount-cost       | 13               | 3        | **9**   | ALL cost drivers (material costs, production volume, economies of scale) |
| water-blood-pressure       | 10               | 2        | **8**   | ALL correlation facts (RCT results, dose-response, mechanisms)           |
| trade-policy-manufacturing | 10               | 1        | **9**   | ALL causal-impact facts                                                  |
| jazz-trumpeters            | 5                | 2        | 3       | Influence/impact facts                                                   |

The decomposition is **good** — it correctly identifies the facts needed to answer.
The search queries **target the right topics**. But the specific information never
gets extracted, and when research is incomplete, the agent writes a gap-catalog
instead of synthesizing the best available answer.

### Root causes

1. **Synthesis is too conservative.** The prompt says "If the facts are
   insufficient to support a conclusion, do NOT draw that conclusion." There is
   no concept of "best available" synthesis. Conclusions are strict restatements
   of verified facts, never reasoned inferences that connect facts to the
   question.

2. **Report generation is forbidden from inferring.** "Do NOT use any built-in
   world knowledge — if a fact is not in the provided facts, it does not appear
   in the report." Even when available facts logically imply an answer, the
   report can't draw the connection.

3. **Evaluation penalizes inferences as "unsupported."** The evaluation prompt
   treats anything beyond strict fact restatement — "comparative judgments, or
   causal claims without evidence" — as unsupported. This filters out reasoned
   inferences before they reach the report.

4. **Retry prompts have no positive search strategy.** "Do not repeat queries"
   and "focus on gaps" are purely negative directives. No guidance on
   reformulating queries, trying different source types, or shifting scope.

5. **Search queries are keyword-stuffed.** Despite the prompt saying "3-6 words,"
   actual queries are 8-12 word technical phrases. These don't surface the
   specific information needed.

## Design tension

The system is intentionally conservative to prevent hallucination. Loosening
inference rules risks overclaiming. The resolution: **distinguish reasoned
inferences from speculation.**

| Pattern                       | Verdict                     | Example                                                |
|-------------------------------|-----------------------------|--------------------------------------------------------|
| Strict restatement of fact    | Always OK                   | "f002 says mounts use precision tolerances"            |
| Logical entailment from facts | Reasoned inference (permit) | "f002 + f004 → higher complexity → likely higher cost" |
| Domain knowledge not in facts | Speculation (forbid)        | "Costs more because of supply and demand"              |
| Causal claim without evidence | Speculation (forbid)        | "Precision tolerances CAUSE higher prices"             |

The boundary: **the inference must be a logical consequence of cited verified
facts, not domain knowledge dressed up as logic.**

## Implementation Plan

### Foundational Change: Structured derivation metadata on conclusions

**Files:**
- `backend/moira/models/knowledge.py` (Conclusion TypedDict — add field)
- `backend/moira/workflow/nodes/synthesis.py` (parse field from model output)
- `backend/moira/workflow/nodes/evaluation.py` (allow evaluator to re-label)
- `backend/moira/resources/prompts.md` (synthesis, evaluation, report prompts)

**Problem:** The current `Conclusion` schema records whether a conclusion survived
evaluation (`status`), but not how it was derived. There is no structured way to
distinguish a strict restatement of a fact from a reasoned inference. The plan
needs this distinction so synthesis can label inferences honestly, evaluation can
apply the correct standard per type, the report can hedge inferred conclusions,
and end users can see which conclusions are rock-solid vs. reasoned.

**Schema change:** Add a `derivation` field to `Conclusion`:

```python
class Conclusion(TypedDict):
    ...
    derivation: NotRequired[str]  # "direct" | "inferred"
```

Two categories (initially):
- `direct` — strict restatement of one or more verified facts. The conclusion
  asserts nothing beyond what the cited facts establish.
- `inferred` — goes beyond strict restatement: the conclusion is considerably
  likely to be true, given the cited verified facts. Hedged language ("likely,"
  "probably," "suggests") is appropriate in the report.

Defaults to `direct` when absent (backward compatibility with existing runs).

**Why two categories, not three:** A `deduced` (necessary consequence) category
would be more philosophically precise, but in practice almost all conclusions
that address the user's question are `inferred` (probabilistic). Two categories
keeps the model's job reliable; we can split `inferred` into `deduced` vs
`induced` later if eval data justifies it.

**Pipeline flow:**
1. Synthesis — model sets `derivation` on each conclusion.
2. Evaluation — evaluator verifies the label is honest. A `direct` claim that
   smuggles in inference is re-labeled to `inferred`, or marked `unsupported`
   if the inference isn't sound. An `inferred` claim with sound logic and
   citation is marked `verified`.
3. Report — report generator phrases conclusions according to `derivation`:
   `direct` stated flatly; `inferred` stated with hedging.
4. UI/end user — the label is visible in structured output for transparency.

### Change 1: Allow reasoned inferences in synthesis

**File:** `backend/moira/resources/prompts.md` (`synthesis.system`)

**Current rule:**
> "If the facts are insufficient to support a conclusion, do NOT draw that conclusion."

**Add:**
- A concept of **"reasoned inference"** — a conclusion that logically follows from
  verified facts but goes beyond strict restatement to address the user's question.
- Every reasoned inference must (a) cite specific fact IDs, (b) show the reasoning
  chain, (c) set the `derivation` field to `"inferred"` (structured metadata, not
  prose — see Foundational Change above). Strict restatements use `"direct"`.
- Keep the prohibition on world-knowledge speculation and the rule that
  unknown/contradicted facts can't be used as support.

**Example to add to the prompt:**
```
Reasoned inferences: When verified facts don't directly answer the user's
question but logically imply a partial answer, draw the inference and show
the chain. Example: if f002 says "equatorial mounts require precision
tolerances" and f004 says "they use specialized single-axis tracking motors,"
you may conclude "The mechanical complexity of equatorial mounts (precision
tolerances + specialized tracking) likely contributes to their higher cost
compared to simpler alt-azimuth designs." This is permitted — it follows
logically from the cited facts. What's NOT permitted: asserting the cost
difference is due to "supply and demand" or other domain knowledge not in
the facts.
```

### Change 2: Align evaluation to accept reasoned inferences

**File:** `backend/moira/resources/prompts.md` (`evaluation.system`)

**Current rule:**
> "If the conclusion asserts something that the cited facts do not establish — additional domain knowledge, comparative judgments, or causal claims without evidence — mark it 'unsupported'."

**Modify to:**
- Distinguish **unsupported overclaims** (no grounding in verified facts, using world knowledge) from
  **reasoned inferences** (logical chain from verified facts).
- Mark a conclusion "verified" when: (a) reasoning is sound, (b) all supporting
  facts are verified, AND (c) the conclusion follows logically from those facts —
  even if it goes beyond strict restatement.
- Keep marking "unsupported" for conclusions that introduce domain knowledge,
  make causal claims without evidence, or assert things the cited facts don't
  logically imply.
- Confirm or correct the `derivation` label on each conclusion to indicate
  whether it is `direct` or `inferred`. (Synthesis sets the label initially;
  your job is to verify it.) A `direct` claim is a restatement of existing
  verified facts. An `inferred` claim is considerably likely to be true, given
  the existing verified facts.
- Verify the `derivation` label is honest. A `direct` claim smuggling in
  inference is re-labeled to `inferred`, or marked `unsupported` if the inference
  isn't sound. An `inferred` claim with sound logic + citation is marked
  `verified`, not `unsupported`.

**Test:** "Does the conclusion follow logically from the cited facts?" not "Does
the conclusion strictly restate the cited facts?"

### Change 3: Search strategy diversity

**Files:**
- `backend/moira/resources/prompts.md` (`research.system_retry_review`,
  `research.system_native_tools`, `research.system`)

**Changes:**

1. **Add positive search-strategy guidance to retry prompts.** When a fact
   stayed unknown after prior research, suggest specific alternative approaches:
   - Natural-language phrasings ("why are equatorial mounts expensive" vs
     "equatorial mount manufacturing cost")
   - Indirect sources (forums, reviews, comparisons, manufacturer pricing pages)
   - Terminology variation (colloquial terms, domain jargon)
   - Scope shifts (broader: industry-level analysis; narrower: specific product
     comparison)
   - Site-specific searches for domain expertise

2. **Strengthen query-length discipline in research prompts.** The current
   prompt says "3-6 words" but the model uses 8-12. Add concrete bad/good
   examples drawn from actual eval data:
   ```
   Too long (doesn't work): "telescope mount manufacturing production volume
     economies of scale market demand pricing"
   Better: "equatorial mount production volume"
   Better: "why are equatorial mounts expensive"
   Better: "telescope mount cost comparison forum"
   ```

3. **Add query-type guidance by question category.** Cost/price questions
   benefit from forum/review sources. Medical questions benefit from
   clinical-trial databases. Causal questions benefit from academic literature.

4. **Per-fact diversity within each batch.** Strengthen the "Search strategy"
   section in `research.system` and `research.system_native_tools`. The current
   "One well-chosen search per fact" bullet is efficiency-focused; add explicit
   batch-level diversity guidance:

   > "When issuing multiple searches in one round, each should target a
   > different fact. Multiple variations of the same query waste budget; multiple
   > searches aimed at different facts cover more ground. Multiple searches
   > for the same fact are fine when they use genuinely different strategies
   > (different source types, phrasings, or scope), but not mere restatements."

   Add parallel guidance to `research.system_retry_review`:

   > "Each search should target a different missing area, not the same gap
   > restated multiple ways."

   This is a directional nudge, not a hard constraint — the agent retains
   flexibility to issue multiple searches for one fact when warranted (hard
   question, different source types), but the default expectation is spreading
   searches across fact targets within a batch.

### Change 4: Report synthesis of best-available answer

**File:** `backend/moira/resources/prompts.md` (`report_generation.system`)

**Current rule:**
> "Do NOT use any built-in world knowledge — if a fact is not in the provided facts, it does not appear in the report"

**Add (without removing the above):**
- When research didn't fully answer, synthesize the best answer from verified
  facts + reasoned inferences.
- Clearly distinguish verified facts from reasoned inferences from unknowns.
- Don't simply catalog gaps — use available evidence to build the most helpful
  answer possible.
- For telescope-mount-cost style questions: if you found mechanical differences
  but not cost data, connect the mechanical differences to likely cost implications
  as a reasoned inference, clearly labeled.
- Use hedging language based on `derivation`: `direct` conclusions stated flatly;
  `inferred` conclusions stated with "likely," "probably," "suggests." The label
  should be visible to the end reader (parenthetical or report structure).

## Files touched

- `backend/moira/resources/prompts.md`
  - `synthesis.system` (Change 1)
  - `evaluation.system` (Change 2)
  - `research.system_retry_review` (Change 3)
  - `research.system_native_tools` (Change 3)
  - `research.system` (Change 3)
  - `report_generation.system` (Change 4)
- `backend/moira/models/knowledge.py` (Conclusion.derivation field — Foundational Change)
- `backend/moira/workflow/nodes/synthesis.py` (parse derivation from model output)
- `backend/moira/workflow/nodes/evaluation.py` (re-labeling logic for mislabeled derivation)
- `agent-docs/goal-alignment-and-research-effectiveness.md` (this document)

The foundational change adds a small schema field and parsing logic; the rest of
the changes remain prompt-only.

## Phased Implementation

The changes fall into two independent clusters addressing different root causes.
Phasing lets us isolate each cluster's effect on eval metrics.

**Cluster A — Search diversity (Change 3):** prompt-only, zero risk to
`unsupported` metric. Addresses "can't find specific information."

**Cluster B — Derivation/inference (Foundational + 1 + 2 + 4):** schema + code +
prompts. Addresses "catalogs gaps instead of synthesizing answers." Riskier —
could increase overclaiming.

### Phase 0: Prerequisite commits

Clean git history before prompt work begins.

1. **Commit Phase 2 (URL fetch dedup)** — 4 files, +895 lines. Verified working:
   42 url_content calls deduped across 7 eval runs. Files: `research.py`,
   `prompts.md`, `test_research_internals.py`, plan doc.
2. **Commit web_search cache fix** — 2 files (`web_search.py`,
   `test_web_search.py`). Cache now populates regardless of unresponsive engines.

### Phase 1: Search strategy diversity (Change 3)

**Scope:** All four sub-items of Change 3. Prompt-only — no code, no schema.

| Sub-item | Sections touched | What |
|----------|-----------------|------|
| 1. Retry guidance | `research.system_retry_review` | Positive alternative strategies: natural-language phrasings, indirect sources, terminology variation, scope shifts, site-specific searches |
| 2. Query-length discipline | `research.system`, `research.system_native_tools` | Concrete bad/good examples from eval data (keyword-stuffed vs. short focused) |
| 3. Query-type by category | `research.system`, `research.system_native_tools` | Cost→forums, medical→clinical trials, causal→academic literature |
| 4. Per-fact diversity | `research.system`, `research.system_native_tools`, `research.system_retry_review` | "Each search targets a different fact" nudge; "each retry targets a different missing area" |

**Verification:** `uv run pytest tests/ -q -x --ignore=tests/test_url_content.py`
+ `.venv/bin/ruff check` + `.venv/bin/ruff format --check` (should be no-op for
prompt-only changes).

**Eval:** `eval:invoke --batch --all` — fresh agent runs with new prompts (~1 hour).

**Metrics to watch:**
- Goal alignment score on telescope-mount-cost and water-blood-pressure (currently 2/5)
- Fact extraction rates — unknown count (telescope 9/13, water 8/10)
- PASS count (currently 1/7; target >3)
- `unsupported` stays at 0 (search changes shouldn't affect this)

### Decision point after Phase 1

- **Goal alignment improved significantly** → skip Phase 2 for the demo; ship as-is.
- **Still stuck at 2/5** → proceed to Phase 2 (derivation core).

### Phase 2: Derivation core (conditional)

**Scope:** Foundational Change + Change 1 + Change 2. Code + prompts.

Shipped in isolation so `unsupported` can be watched carefully. If it stays at 0,
the inference rules are sound. If it rises, we know exactly which change caused it.

### Phase 3: Report hedging (optional)

**Scope:** Change 4. Prompt-only. Can fold into Phase 2 or ship separately.

## Verification

### Per-change testing

- **Changes 1 + 2:** Manual testing — run telescope-mount-cost and verify
  synthesis produces reasoned inferences (e.g., "mechanical complexity likely
  contributes to higher cost") with `derivation: "inferred"`, and evaluation
  marks them "verified" rather than "unsupported." Verify a `direct` claim that
  smuggles in inference gets re-labeled or flagged.
- **Change 3:** Manual testing — check web_search query length and diversity
  in a fresh run. Verify the model uses shorter queries and tries different
  source types on retry.
- **Change 4:** Manual testing — verify the report attempts to answer rather
  than cataloging gaps.

### Eval batch

Run after all changes:
```
moira_eval invoke --batch --all
```

**Key metrics:**
- **Goal alignment score** (target: >2 on the 5 questions currently at 2/5)
- **PASS count** (target: >3 of 7)
- **Unsupported claim count** (must stay at 0 — the inference changes should
  NOT increase overclaiming)
- **Fact extraction rates** (should be stable or improving)

**Questions to watch:**
- `telescope-mount-cost`, `water-blood-pressure` — highest-signal for inference
  changes (currently 2/5 on goal alignment with strong grounding scores)
- `jazz-trumpeters` — tests search diversity (couldn't find key figures)
- `flaming-hot-cheetos`, `future-nostalgia` — watch for regressions from
  loosened inference rules

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Inference changes cause overclaiming | Keep the "logical consequence of cited facts" test. Unsupported count stays in eval metrics. |
| Evaluation becomes too permissive | The "logical consequence" test is strict — causation without evidence still fails. |
| Search diversity makes queries too vague | Keep the "3-6 words" guidance. Add examples of good short queries. |
| Report starts hallucinating | Maintain "no world knowledge" rule. Inferences must reference fact IDs. |
| `derivation` field mislabeled by model | Evaluator checks and corrects labels. `direct`→`inferred` re-label is recoverable; `inferred` smuggled as `direct` is flagged unsupported. |

## Deferred

- **Cross-invocation failure memory for url_content** — Phase 2 limitation noted
  in the previous plan. Failed URLs from prior research passes aren't tracked.
- **Within-batch url_content dedup** — if the model emits duplicate calls in one
  batch. Currently rare.
- **Query rewriting step** — a dedicated query-rewrite pass that expands each
  fact into multiple phrasings before search. Code change, not prompt-only.
  Defer until prompt-only changes are measured.
