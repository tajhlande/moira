# Agent Craft Notes

Running notes on the *non-structural* work that determines whether MOiRA actually behaves like a trustworthy research agent. Architecture defines what we build; the implementation plan defines how and when; this document captures what makes it good once built.

Keep this living. Add observations as you tune prompts and watch the system run. Date entries in the Prompt Evolution Log so you can trace how behavior changed.

---

# The Bet

## What we are trying to achieve

A research agent whose answers are **more trustworthy than a single-pass tool-using agent**. Specifically:

- Fewer fabricated citations
- Fewer confidently-wrong claims
- Honest "I couldn't verify this" flagging when verification cannot confirm a claim
- Visible chain-of-evidence: every claim mappable to a source the user can inspect

The bar to clear is modest in absolute terms. Single-pass agents routinely hallucinate sources and assert false things with confidence. Even a modestly-functional verification loop catches a meaningful fraction of those failures.

## What the architecture buys us

- **Verification as a distinct phase with its own tool access.** Most "trustworthy agent" attempts fail because verification is implicit ("look it over before answering") rather than separated.
- **Structured `VerificationReport` fed back to Planning.** Without structure, retries are noise. With it, the next pass can target specific failures.
- **Hard budget ceiling.** Termination is principled, not arbitrary.
- **Honest `unverified_claims` reporting.** The system is designed to *admit* what it couldn't verify rather than paper over it. This alone clears the bar over most agents.

## What the architecture cannot buy us

- **Capability beyond the model's epistemic ceiling.** Self-critique by the same model class shares the drafter's blind spots.
- **Information beyond tool coverage.** If no configured tool can reach the answer, no loop helps. Flagging "unverified" is the win here, not magically producing an answer.
- **Adversarial behavior by default.** Telling a model to "be adversarial" in a prompt does not reliably produce adversarial behavior. This is prompt-engineering work.

Roughly: structure delivers ~60% of the outcome; the remaining ~40% is prompt craft, eval discipline, and calibration.

---

# Failure Modes to Watch For

Ordered by expected impact on trustworthiness. The top three are the ones most likely to undermine the whole bet.

## 1. Verifier re-affirms drafter (highest impact)

The verifier reads the draft and concludes "looks reasonable" without genuinely challenging claims. Single most common failure mode of reflection loops, and the one that most undermines the trustworthiness claim — the system *appears* to be verifying but is rubber-stamping.

**Signals**: verification passes too readily; `VerificationReport.failed_claims` is consistently empty; verification almost never triggers a retry even on hard questions.

## 2. Planning re-runs the same strategy on retry

Verification fails, retry happens, Planning produces a plan that looks structurally identical to the prior one. The system burns budget without making progress.

**Signals**: `verification_history` shows the same failed claims across attempts; tool selection produces the same tools each pass; the final answer is identical to the first draft.

## 3. Compression drops critical nuance

Tool outputs are long. Compression summarizes them with the task model. Critical qualifications, dissenting sources, or exact quotes get smoothed out. The drafter then synthesizes a confident answer from over-confident inputs.

**Signals**: claims in the draft are stronger than the underlying evidence warrants; citations point to sources that say something more hedged than the draft suggests.

## 4. Tool-selection blind spots

Semantic discovery returns a top-K that doesn't include the right tool. The model never gets to choose it because it was never surfaced. Common with tools whose descriptions don't lexically resemble the query.

**Signals**: a question that should have triggered tool X gets answered without it; manual inspection of LanceDB results shows X ranked below the top-K cutoff.

## 5. Budget exhaustion patterns

Budget runs out before verification can converge. Two sub-patterns:
- **Front-loaded**: Research Execution burns most of the budget on speculative tool calls, leaving none for retry.
- **Oscillating**: Each retry partially fixes one issue and breaks another, never converging.

**Signals**: `unverified_claims` appears regularly even on questions that should be answerable; budget consumption per node is skewed heavily toward one node.

## 6. Confident-source bias

The model trusts sources that *sound* authoritative (formal tone, structured prose) over ones that contradict them. Web search returns confident SEO content that overrides a more accurate but less polished source.

**Signals**: citations skew toward content-farm-style sources; contradictory primary sources are ignored in the synthesis.

## 7. Citation drift

Draft cites source A for claim X. Source A actually says something close to but not exactly X — sometimes more hedged, sometimes about a related-but-different thing. Common when the synthesizer paraphrases without re-checking.

**Signals**: spot-checking citations finds frequent "yes but" mismatches between the claim and the cited content.

---

# Tactics

Concrete countermeasures organized by failure mode.

## For verifier re-affirmation

- **Force structured output that requires critique.** Don't ask "is this verified?" — ask "list at least 3 claims you could not find primary-source support for" and "list 2 places this draft could be wrong." If the verifier produces empty lists, the prompt isn't working.
- **Ground verification in tool calls, not the verifier's prior knowledge.** Every flagged claim should be tied to either (a) a tool result that contradicts it, (b) a tool query that returned no support, or (c) a contradiction between two retrieved sources.
- **Use a different model for verification where possible.** Not just a re-prompted same model — different family or size. A 70B verifier critiquing a 70B drafter shares too many blind spots. If only one intelligence model is configured, at minimum vary temperature: low temperature for drafting (commit to a position), higher for verification (explore alternatives).
- **Eval against known-wrong drafts.** Maintain a small fixture set of drafts containing planted errors. Run verification against them. If verification doesn't catch the planted errors, the prompt is broken — not the model.

## For Planning strategy diversity

- **Inject prior failures as anti-instructions.** Planning prompt should include `verification_history` with explicit "the prior approach failed for these reasons — pursue a different source class, different tool selection, or different decomposition." Vague "try harder" framing is useless.
- **Forbid prior tool sets on retry.** If attempt 1 used tools `[web_search, paper_search]` and failed, attempt 2's prompt should explicitly exclude or de-prioritize those. Forces the model out of the rut.
- **Decompose on retry.** If the original question failed as a single inquiry, attempt 2's planning should break it into sub-questions and answer each separately. Often what looked like a single question was actually three.

## For compression nuance loss

- **Preserve verbatim quotes for citation-eligible content.** Compression strips prose but should retain direct quotes that may become citations.
- **Mark dissent.** If two sources disagree, compression must preserve both positions, not synthesize them into a single "view." Architect the compression prompt to look for and preserve contradictions specifically.
- **Compress conservatively.** Default to less aggressive compression. Context-exhaustion risk is real but recoverable; nuance loss is silent and unrecoverable.

## For tool-selection blind spots

- **Write tool descriptions for retrieval, not for human reading.** Embed the kinds of questions a user might ask, not just what the tool does. "search_papers — finds academic papers, peer-reviewed articles, preprints, scientific publications, journal articles, citations for empirical claims" beats "search_papers — search the academic literature."
- **Include synonyms and adjacent terms.** Embedding similarity is brittle to vocabulary mismatch. Pad descriptions with synonyms a user might use.
- **Inspect the top-K manually on hard cases.** When semantic discovery looks wrong, look at the top-K results before blaming the model — usually the right tool just wasn't surfaced.

## For budget exhaustion

- **Tune cost weights against observed usage.** The default weights (Planning=2, Research=5, etc.) are guesses. After running a few real sessions, measure actual cost contribution and adjust.
- **Cap Research Execution tool calls per pass.** Otherwise a single pass can burn the entire budget chasing tangents.
- **Set `budget_limit` generously during tuning.** Tight budgets in early dev mask whether the loop is converging vs just running out. Once converging behavior is reliable, tighten the budget.

## For confident-source bias

- **Prefer primary sources in tool descriptions.** When two retrieval tools exist (one for general search, one for primary sources), make the primary-source tool's description rank higher for citation-eligible queries.
- **Verification prompt: "find a source that contradicts this."** Adversarial framing biases the verifier toward dissent, partially counteracting confident-source bias.

## For citation drift

- **Verification must include "re-read the cited source."** Don't trust the draft's paraphrase — pull the source content and check that it says what the draft claims it says.
- **Structured citations from the start.** Findings should include the exact excerpt that grounds the claim, not just the URL. Re-checking is then a string match against the excerpt.

---

# Calibration: What "Good" Looks Like by Model Scale

The architecture works at every scale, but the *flavor* of the win shifts with model capability. Calibrate expectations accordingly.

## Frontier closed models (Claude 4.x, GPT-4-class)

- Verification meaningfully catches its own errors when prompted adversarially
- Planning diversifies on retry without heavy prompt scaffolding
- The system can credibly produce "verified" answers
- Trustworthiness win is primarily on reducing hallucination on factual recall

## 25–100B open models (Llama 3.x 70B, Qwen 2.5 72B-class)

- Adversarial verification works but needs heavier prompt scaffolding
- Planning diversification needs explicit anti-instructions
- The system is often most valuable as an **honest "I couldn't verify this" flagger** rather than a fully-verified-answer producer
- Trustworthiness win is primarily on visible epistemic humility — fewer confident wrong answers, more "here's what I found and what I couldn't confirm"

## Smaller models (7–13B)

- Self-critique is unreliable; verifier rubber-stamping is the dominant failure
- Structural scaffolding compensates a lot but the quality gap is real
- Best deployed with a frontier model for verification (drafter local, verifier remote) if hybrid is acceptable
- If fully local with small models only, expect the system to function as a "structured tool-using agent with citations" — the trustworthiness gains over single-pass narrow

The key insight: at smaller scales, **honest unverified-flagging is itself the product**. A system that confidently says "I couldn't verify these 3 claims, but here's what I did find" is more trustworthy than a frontier model that confidently asserts the same content unflagged.

---

# Eval Discipline

Trustworthiness gains are invisible without evals. Maintain a fixture set:

## Fixture categories

1. **Known-answer questions** — questions with verifiable single-fact answers. Measures: did the system get it right, did it cite a real source, does the cited source actually support the claim.
2. **Planted-error drafts** — pre-written drafts with deliberate factual errors. Run verification only. Measures: did verification catch the error.
3. **Tool-coverage-gap questions** — questions that require unavailable information. Measures: did the system honestly flag "unverified" rather than fabricate.
4. **Contradictory-source questions** — topics where retrieval will return conflicting sources. Measures: did compression preserve the contradiction; did the draft acknowledge it.

Run these after any meaningful prompt change. A single-pass agent's baseline for comparison should also be part of the fixture set so the trustworthiness delta stays measurable.

---

# Prompt Evolution Log

Dated entries as prompts at Planning, Verification, Compression, and Synthesis get tuned. Each entry: what changed, why, what improved, what regressed.

The log is the highest-value section of this document over time. Without it, you re-discover the same failure modes a year in.

---

## YYYY-MM-DD — example entry format

**Node**: Verification
**Change**: Switched from "review the draft for accuracy" to "list at least 3 claims you cannot find primary-source support for; for each, state what source you searched and what was missing."
**Why**: Verifier was rubber-stamping drafts on the planted-error fixture set (catching 1/10 errors).
**Result**: Planted-error catch rate up to 7/10. Side effect: verification occasionally flags well-supported claims as unverified because the verifier's tool query was poorly formulated.
**Follow-up**: tune the "what source you searched" requirement to encourage broader queries.

---

# Open Questions

Things to revisit as the system matures.

- **When does verifier-model separation become worth the config complexity?** Currently one "intelligence" model serves both. At what point is the trustworthiness gain from running a distinct verifier worth the operational cost?
- **Hypothesis branching** (Phase 7) — when does parallel exploration of competing hypotheses produce better results than sequential retry? Probably on comparative and analytical questions, less so on factual recall.
- **Persistent knowledge graph across sessions** (Phase 7) — does accumulating verified findings improve later trustworthiness, or does it propagate prior errors? Needs an explicit unverification path before this is safe.
- **When is "good enough" reached?** At what point does further prompt tuning stop producing measurable trustworthiness gains? Without this threshold, prompt iteration is unbounded.
- **Should the Compression node be removable on shorter research?** If findings fit in context, compression risks nuance loss with no benefit. Worth a length threshold.
