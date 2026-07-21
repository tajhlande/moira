# Claim Validation and Overclaim Detection

## Problem

The research loop has two gaps in claim validation:

1. **Review and evaluation workflow steps are blind to source content.** They receive claim text and citation ID lists, but never the actual source content behind those citations. The model is asked to "review the claim against its cited evidence" but the evidence isn't in the prompt. Cross-referencing is impossible.

2. **No overclaim detection.** Synthesis produces conclusions from facts, but nothing programmatically verifies that conclusions stay within the bounds of their supporting facts. The synthesis prompt instructs against overclaiming, but the node passes through whatever the model emits. Two `strict=True` xfail tests in `test_evaluation_fixtures.py` encode this unbuilt contract.

The evaluation node is the right place for overclaim detection — it already has the mandate to check conclusion validity and route accordingly. The xfail tests target synthesis, which is the wrong node (the generator shouldn't be its own validator).

## Current State

### Data flow gap

During the research loop, the model sees full `ToolResult.output` formatted as per-citation summary blocks. But when the research node returns state:

- `Citation.content` — does not exist. No field stores the source text.
- `Citation.excerpt` — capped at 500 chars, derived from search snippets or truncated tool output.
- `Citation.snippets` — each capped at 500 chars.
- `ToolResult.output` (full) — discarded. Never persisted in state.
- `ToolResult.metadata` — used for citation extraction, then discarded.

The comment at `research.py:1214` explicitly notes: *"tool_results are NOT included here."*

### Graph flow

```
research → synthesis → research_review → evaluation → report_generation
```

Synthesis runs before research_review, so conclusions (with `supporting_fact_ids`) exist by the time review runs. This enables a structural sanity check at review time.

### xfail tests

Two tests in `TestSynthesisTrapFixture` (`test_evaluation_fixtures.py`):

1. `test_synthesis_does_not_claim_best_partner` — mocks synthesis emitting "Corviknight is the best partner" (overclaim, no usage data). Expects synthesis to reject it.
2. `test_synthesis_qualifies_fighting_neutral` — mocks synthesis emitting "resists Fighting" (factually wrong). Expects synthesis to qualify it.

Both target the wrong node. They should target evaluation.

## Approach: Adversarial Grounding Verification

Use the LLM itself as validator, but with different information and framing than the synthesis pass. No regex, no mechanical pattern matching.

### Principles

1. **Source content must reach the evaluator.** The evaluation prompt currently has citation IDs only. It needs the actual source text to cross-reference claims against what sources said.

2. **Adversarial framing.** Instead of "is this conclusion correct?" (which invites agreement), frame as: "For each conclusion, identify what it asserts. Then check whether each assertion is grounded in a specific cited fact. If the conclusion asserts something the facts don't establish, it's an overclaim."

3. **Calibrated skepticism.** The adversarial prompt should flag genuine overclaims — conclusions that explicitly go beyond their supporting facts — not cast doubt on well-supported reasoning. Start conservative; monitor retry rates.

4. **Structural sanity check at review.** Before evaluation spends model tokens on semantic analysis, programmatically verify that `supporting_fact_ids` reference facts that actually exist. This catches hallucinated references without any model involvement.

## Implementation Plan

### Phase 1: Enrich Citation with source content

**File:** `models/knowledge.py`

Add `content: NotRequired[str]` to the `Citation` TypedDict. This stores the source text that was available to the research model — the formatted tool output per citation.

No DB migration needed. `knowledge_snapshot` is serialized as JSON.

### Phase 2: Populate source content in research

**File:** `workflow/nodes/research.py`

In `_process_execution_results`, when creating or updating citations:

- **Structured results** (web_search): store the search result snippet as `content` (the substantive source text for that citation).
- **Unstructured results** (url_content, calculator): store `result.output` capped at 5,000 chars as `content`.

When merging citations across rounds (`_find_or_merge_citation`), preserve the longest `content` seen (don't overwrite with a shorter one). Rationale: within a single workflow run we treat URL fetches as idempotent, so the longest body seen is the canonical one; re-fetches are treated as duplicate retrieval rather than distinct content. This differs deliberately from the `snippets` overlap-dedup-then-append logic — `snippets` collects genuinely distinct search fragments across rounds, whereas `content` represents a single coherent source body, and one canonical body is a cleaner input for downstream evaluation.

### Phase 3: Supporting fact ID sanity check at research_review

**Files:** `workflow/nodes/research_review.py`, `models/knowledge.py`

**Schema change:** Add `"unsupported"` to the `Conclusion.status` documented values in `models/knowledge.py`:

```python
status: str  # "unverified" | "verified" | "contradicted" | "unsupported"
```

`"unsupported"` means: the conclusion was checked and found to lack grounding — its support does not exist, or it asserts beyond what the facts establish. This is distinct from `"unverified"` (not yet checked) and `"contradicted"` (actively refuted by conflicting evidence). Note: today both `"unverified"` and any non-bucketed status are silently dropped from the report; `"unsupported"` is introduced specifically to give this class of failure observable, honest handling (see Phase 7).

After applying the model's fact verdicts, programmatically check all conclusions' `supporting_fact_ids`:

```python
known_fact_ids = {f["id"] for f in facts}
for conclusion in state["knowledge"]["conclusions"]:
    for fid in conclusion.get("supporting_fact_ids", []):
        if fid not in known_fact_ids:
            conclusion["status"] = "unsupported"
            conclusion["verification_note"] = (
                f"References non-existent fact ID: {fid}"
            )
```

This is a structural check — no model call, no semantic analysis. Conclusions with hallucinated fact references are flagged `"unsupported"` before evaluation runs. `"unsupported"` (not `"contradicted"`) is the correct status: there is no contradicting evidence, only missing support.

`"unsupported"` is a **terminal** status within a workflow run:
- **Evaluation skips it** (see Phase 6) — the structural verdict is preserved and no model tokens are spent re-judging a conclusion that structurally cannot be verified.
- **It does not auto-trigger retries.** Retry is driven by evaluation's holistic `route` decision, not by individual conclusion statuses. Feeding "you hallucinated fact X" back to synthesis is a double-edged sword (see Risks) and is deliberately deferred.

### Phase 4: Flow source content into evaluation prompt

**File:** `workflow/nodes/evaluation.py`

Add citation content to the formatted user prompt. Apply a **citation content budget**:

- Per-citation cap in prompt: 2,000 chars
- Total citation content cap: ~32,000 chars (~8,000 tokens)
- **Prioritization:** Citations referenced by the conclusions being evaluated always come first. When over budget, truncate or omit unreferenced citations first.

Format:

```
Source content:
[cit001] web_search | https://example.com/page | Example Page
<up to 2000 chars of source text>

[cit002] url_content | https://example.com/another
<up to 2000 chars of source text>
```

### Phase 5: Flow source content into research_review prompt

**File:** `workflow/nodes/research_review.py`

Same treatment — include citation `content` alongside facts in `_format_facts_for_review`. No prompt rewrite; the existing prompt already says "review the claim against its cited evidence." Adding the evidence is sufficient.

Apply the same citation content budget and prioritization as evaluation.

### Phase 6: Adversarial evaluation and unsupported-status handling

**Files:** `resources/prompts.md` — `evaluation.system`; `workflow/nodes/evaluation.py`

**Prompt rewrite** with cross-examination framing. Overclaims and grounding failures are marked **`unsupported`**; `"contradicted"` is reserved for genuine conflict (a fact is actively refuted, or reasoning contains a logical error):

> For each conclusion, trace every assertion back to a specific cited fact. If the conclusion asserts something that the cited facts do not establish — additional domain knowledge, comparative judgments, or causal claims without evidence — mark it **unsupported** and identify what it adds beyond the facts.
>
> Cross-reference each fact's claim against its source content. If the claim misrepresents or overstates what the source said, mark the conclusion **unsupported**.
>
> Reserve **contradicted** for cases where a supporting fact is actively refuted by other evidence or the reasoning contains a logical error. A lack of grounding is **unsupported**, not **contradicted**.
>
> Be precise: a conclusion is an overclaim only if it explicitly goes beyond what the supporting facts establish. Well-supported conclusions should be marked verified.

Update the evaluation prompt's allowed result values (`prompts.md:566`) to include `unsupported`: `"verified" | "unverified" | "contradicted" | "unsupported"`.

The prompt should be calibrated conservatively — flag only clear overclaims where the conclusion explicitly goes beyond the cited facts. The goal is catching genuine grounding failures, not casting doubt on well-supported reasoning.

**`goal_met` is judged on verified-conclusion sufficiency.** Preserve and sharpen the existing framing in `evaluation.system` (prompts.md:556-558, 568-569): `goal_met` asks whether the **verified** conclusions adequately answer the user's question. Conclusions that are `unverified`, `unsupported`, or `contradicted` do not count toward sufficiency, but their mere presence does not make the goal unmet if the verified conclusions suffice. The evaluator must make this judgment with **all conclusions of all statuses in context** — this is one of the two most cognitively demanding tasks asked of the intelligence model in this agent (the other being sound synthesis itself), so the prompt should present the full conclusion set, clearly labelled by status, and require an explicit sufficiency rationale in `goal_assessment`.

**Show unsupported in context, but don't re-judge them.** In `evaluation.py` (`evaluation.py:176-185`), keep `"unsupported"` conclusions (set structurally in Phase 3) visible in the prompt context so the evaluator can weigh them for the `goal_met` judgment, but do not require or apply a per-conclusion verdict for them — guard the status-application loop to skip them, preserving the structural verdict. Spending no verdict-tokens on conclusions that structurally cannot be verified realizes Phase 3's "no model involvement" promise, while still informing the holistic sufficiency call.

Both detection layers now emit the same status for the same class of problem (lack of grounding): Phase 3 structural (hallucinated fact ID) and Phase 6 semantic (overclaim).

### Phase 7: Report handling of unsupported conclusions

**File:** `workflow/nodes/report_generation.py`

Today the report buckets only `verified` / `contradicted` / `unknown`-facts (`report_generation.py:180-185, 356-366`), and the "fully verified" report reason blocks on `goal_met AND no contradicted conclusion` (`report_generation.py:143-145`). Any other status is silently dropped. The handling below makes non-verified conclusions' treatment deliberate and inspectable, and **removes the report-side guard that overrides evaluation's `goal_met`**:

- **Keep out of answer prose.** Only `verified` conclusions/facts are written from (already the case, `report_generation.py:180-181, 194-195`). Make the exclusion of `unsupported`/`unverified`/`contradicted` from the prose-writing buckets deliberate rather than the accidental fall-through that exists today.

- **Defer the report reason to `goal_met`; drop the contradicted guard.** Change `report_generation.py:143-145` from `goal_met and not any(c status == "contradicted")` to just `goal_met`. The report is "verified" iff evaluation judged the verified conclusions sufficient — regardless of whether some conclusions are `unsupported`, `unverified`, or `contradicted`. This trusts the evaluator's holistic sufficiency call (see Phase 6) over a mechanical status guard, and is consistent with the prompt's existing verified-sufficiency framing. The `retries_exhausted` / `budget_exhausted` / `incomplete` paths are unchanged.

- **Fix misleading `budget_exhausted` path instruction when evaluation ran.** When the evaluation route is `"retry"` but budget is insufficient for another cycle (`report_generation.py:159-163`), the code sets `generation_reason = "budget_exhausted"` and feeds the model the `report_generation.reason_budget_exhausted` instruction: *"Verification could not be completed for all claims. Some facts and conclusions remain unverified."* This is misleading — by the time this branch is reached, `evaluation_history` is populated (line 140 gates on it), so evaluation DID run and verification WAS completed. The real situation is that the evaluator correctly found the research insufficient (`goal_met: false`) and wanted another cycle, but budget wouldn't allow it. Since the review router reserves `eval_cost` before allowing retries, evaluation almost always runs — the `else` branch at line 170-173 (no evaluation history) is a rare edge case that keeps the existing instruction. The fix is to use a new, more accurate instruction for the `eval_route == "retry"` budget-insufficient path: *"Evaluation was completed but found the research insufficient. The verified conclusions do not fully answer the user's question. Present what was verified with caveats about the gaps."*

- **Surface existence via side channels.** Keep the existing `contradicted` bucket, and add an `omitted_conclusions` bucket (conclusions with status `"unsupported"`, as full dicts including `verification_note`) to the report metadata/state output, plus a brief count in the user-facing summary (e.g., "N conclusions omitted as unsupported"). This is not just convenience — it is the **audit trail for the evaluator's most consequential judgment**: the user can see everything that was dropped (and why) to form their own view on whether the evaluator correctly decided the verified set sufficed. Including all claims in the output while not furnishing them for report writing is exactly what makes trusting the evaluator legible.

### Phase 8: Rewrite xfail tests

**File:** `tests/test_evaluation_fixtures.py`

Move both tests from `TestSynthesisTrapFixture` to a new `TestEvaluationOverclaimFixture` (or similar):

- Mock synthesis to emit the overclaim (unchanged mock setup)
- Mock evaluation to detect it via the adversarial prompt + source content
- Assert evaluation marks the conclusion `unsupported` with appropriate reason
- Remove `@pytest.mark.xfail`

Add a new test for the `supporting_fact_ids` sanity check at research_review: mock a conclusion referencing `f999` (non-existent), verify research_review marks it `unsupported` (terminal), and verify evaluation does not overwrite it.

Add a test that report_generation omits `"unsupported"` conclusions from the answer prose and surfaces them in the `omitted_conclusions` metadata, while the report reason follows `goal_met` — i.e., a single `unsupported` (or `contradicted`) conclusion does **not** downgrade the reason from `verified` when `goal_met` is true.

## Risks and Mitigations

### Risk: Increased skepticism degrades output quality

Adding adversarial evaluation without improving fact reasoning could make the system overly conservative — more conclusions flagged as overclaims, more retries, incomplete answers.

**Mitigation:** The adversarial prompt is calibrated to flag only clear overclaims (conclusions that explicitly go beyond cited facts). The `max_evaluation: 2` retry limit prevents infinite loops. Monitor retry rates after deployment and tune the prompt's strictness.

`"unsupported"` is deliberately a **terminal, non-retry** status. Feeding "this conclusion was hallucinated/overclaimed" back to synthesis is double-edged: it could provoke a more reliable response, or merely a different but equally unreliable one. For the structural case (hallucinated fact ID), the genuinely useful signal would be the *valid fact-ID whitelist* (a factual constraint), not the claim text — that is left as a future enhancement. For the semantic overclaim case, per-conclusion feedback is deferred in favor of evaluation's existing holistic `goal_assessment` retry guidance.

**Future work:** Improve fact reasoning quality (better research prompts, smarter tool result processing) alongside tightening evaluation. The claim validation and fact reasoning improvements should evolve together. Consider targeted Phase-3 feedback (valid fact-ID whitelist to synthesis) if hallucinated-reference rates remain high after deployment.

### Risk: The "verified" report reason now rests entirely on the evaluator

Dropping the report-side `contradicted` guard (Phase 7) means the report is labeled "verified" purely on evaluation's `goal_met`. Judging whether verified conclusions suffice — with all claims of all statuses in context — is one of the two most demanding tasks for the intelligence model. A miscalibrated evaluator could label a report "verified" while material conclusions are unsupported or contradicted.

**Mitigation:** The adversarial prompt (Phase 6) is calibrated conservatively and requires an explicit sufficiency rationale in `goal_assessment`. The decisive backstop is inspectability: all non-verified conclusions are surfaced in side channels (the existing `contradicted` bucket plus `omitted_conclusions`), so the user can audit the evaluator's call. Trusting a hard model judgment is made legible by showing everything that was dropped and why. Monitor `goal_met` calibration against user feedback after deployment and tune the prompt's sufficiency guidance.

### Risk: Context window pressure

Adding citation content to evaluation and review prompts increases context usage. With 20 citations at 2,000 chars each, that's ~10,000 additional tokens.

**Mitigation:** Citation content budget caps total source content at ~8,000 tokens. Citations not referenced by any conclusion under evaluation are omitted first. For a 49K context model, this keeps total prompt under ~12K tokens — well within safe operating range. Do not over-optimize for specific model architectures; the caps are model-agnostic.

**Future work:** Sub-agent architecture to narrow down on relevant source content and present smartly-prepared snippets, rather than blunt truncation. This would replace the per-citation cap with intelligent content selection.

### Risk: State size growth

`Citation.content` at up to 5,000 chars per citation increases the serialized knowledge state.

**Mitigation:** With 20 citations at 5,000 chars, that's ~100KB of additional state. LangGraph checkpoints handle this fine. The `knowledge_snapshot` JSON blob in the DB absorbs it without schema changes.

## Future Work

- **Sub-agent content selection:** Specialized tool or agent that narrows down source material to smartly-prepared snippets relevant to specific conclusions, replacing blunt truncation.
- **Long conversation context management:** Architecture for processing knowledge state too big for a single model context window.
- **Fact reasoning improvements:** Better research prompts, smarter tool result processing, fact hygiene — to improve the quality of inputs that evaluation scrutinizes.
- **Claim classification:** Classify wanted facts as verifiable claims vs. opinion/consensus claims, with different validation standards for each.
