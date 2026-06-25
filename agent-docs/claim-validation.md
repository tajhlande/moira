# Claim Validation and Overclaim Detection

## Problem

The research loop has two gaps in claim validation:

1. **Review and evaluation models are blind to source content.** They receive claim text and citation ID lists, but never the actual source content behind those citations. The model is asked to "review the claim against its cited evidence" but the evidence isn't in the prompt. Cross-referencing is impossible.

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

When merging citations across rounds (`_find_or_merge_citation`), preserve the longest `content` seen (don't overwrite with a shorter one).

### Phase 3: Supporting fact ID sanity check at research_review

**File:** `workflow/nodes/research_review.py`

After applying the model's fact verdicts, programmatically check all conclusions' `supporting_fact_ids`:

```python
known_fact_ids = {f["id"] for f in facts}
for conclusion in state["knowledge"]["conclusions"]:
    for fid in conclusion.get("supporting_fact_ids", []):
        if fid not in known_fact_ids:
            conclusion["status"] = "contradicted"
            conclusion["verification_note"] = (
                f"References non-existent fact ID: {fid}"
            )
```

This is a structural check — no model call, no semantic analysis. Conclusions with hallucinated fact references are flagged as `contradicted` before evaluation runs. The `contradicted` status is appropriate because the conclusion's supporting evidence does not exist.

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

### Phase 6: Adversarial evaluation prompt

**File:** `resources/prompts.md` — `evaluation.system`

Rewrite with cross-examination framing:

> For each conclusion, trace every assertion back to a specific cited fact. If the conclusion asserts something that the cited facts do not establish — additional domain knowledge, comparative judgments, or causal claims without evidence — mark it **contradicted** and identify what it adds beyond the facts.
>
> Cross-reference each fact's claim against its source content. If the claim misrepresents or overstates what the source said, mark it **contradicted**.
>
> Be precise: a conclusion is an overclaim only if it explicitly goes beyond what the supporting facts establish. Well-supported conclusions should be marked verified.

The prompt should be calibrated conservatively — flag only clear overclaims where the conclusion explicitly goes beyond the cited facts. The goal is catching genuine grounding failures, not casting doubt on well-supported reasoning.

### Phase 7: Rewrite xfail tests

**File:** `tests/test_evaluation_fixtures.py`

Move both tests from `TestSynthesisTrapFixture` to a new `TestEvaluationOverclaimFixture` (or similar):

- Mock synthesis to emit the overclaim (unchanged mock setup)
- Mock evaluation to detect it via the adversarial prompt + source content
- Assert evaluation marks the conclusion `contradicted` with appropriate reason
- Remove `@pytest.mark.xfail`

Add a new test for the `supporting_fact_ids` sanity check at research_review: mock a conclusion referencing `f999` (non-existent), verify research_review marks it `contradicted`.

## Risks and Mitigations

### Risk: Increased skepticism degrades output quality

Adding adversarial evaluation without improving fact reasoning could make the system overly conservative — more conclusions flagged as overclaims, more retries, incomplete answers.

**Mitigation:** The adversarial prompt is calibrated to flag only clear overclaims (conclusions that explicitly go beyond cited facts). The `max_evaluation: 2` retry limit prevents infinite loops. Monitor retry rates after deployment and tune the prompt's strictness.

**Future work:** Improve fact reasoning quality (better research prompts, smarter tool result processing) alongside tightening evaluation. The claim validation and fact reasoning improvements should evolve together.

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
