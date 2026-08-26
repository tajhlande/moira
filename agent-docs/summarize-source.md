# Summarize Source: Directed Deep Reading via Sub-Context Extraction

Pre-plan for a `summarize_source` tool: a tool call that reads an entire
stored source in its **own** model context, extracts salient facts (optionally
directed at specific entities/topics), and returns a compact result to the
calling agent. Separate stream from `planning-freedom.md` — motivated by that
branch's context-budget work but useful independently of it.

Status: not scheduled. This document records the motivation, the mechanism,
and the design decisions that must be made before implementation.

## Motivation

The research model reads sources through a fixed window. Two measured
failure modes follow:

1. **Window starvation.** Feedback bodies are capped (`_TOOL_RESULT_FEEDBACK_LIMIT`)
   and `Citation.content` is capped (`CITATION_CONTENT_LIMIT`) at the same
   order of magnitude. Anything past the window is invisible unless the model
   re-reads via `recall_source` — which re-injects the *same* window into the
   *same* context. Deep reading of a long source is structurally impossible:
   the model can page through it only by spending its own context on content
   it has already seen.
2. **Impermeable payload structure.** PokeAPI `pokemon_retrieve` keeps
   `types`/`stats` behind a ~380K-char `moves` array even after URL pruning;
   they never enter any window. The agent worked around it (via
   `type_retrieve` + web_search — run `065e23cc`), but the general case has
   no workaround when the data exists in the store yet cannot be paged into
   view. Per project decision we do NOT tune for specific APIs — the agent
   must adapt. `summarize_source` is the general adaptation: it reads the
   whole source elsewhere and returns what matters.

The deeper point: **context is the scarce resource; storage is cheap.** A
sub-context extraction call prices deep reading in the primary budget
currency (an LLM invocation) instead of forcing the caller to spend its own
window. It is the LLM sibling of the deferred "grep + neighborhood"
focused-retrieval idea in planning-freedom.md Phase 3.3, and the same family
as the delegated query-writer pass proposed in retrieval-quality.md —
delegating context-heavy work to a sub-call that returns a compact result.

Also relevant: recall pressure observed on retries (recall-only rounds,
`c815f4a1`) exists partly because re-reading is the only deep-reading
mechanism. Giving the model a better tool for "get more out of this source"
should reduce recall hammering as a side effect.

## Mechanism (sketch)

```
model calls: summarize_source { citation_id: "cit012",
                                focus: "damage relations for rock-type attackers",
                                entities: ["Tyranitar", "Excadrill"] }
   ↓
tool loads full stored content for cit012 (NOT capped at CITATION_CONTENT_LIMIT)
   ↓
sub-context LLM call: source text + extraction directive → salient facts
   ↓
compact result returned to caller (bounded by the normal feedback cap)
```

- Interception model: like `recall_source`, the tool never touches the
  network. It can be intercepted in the research loop (synthesized from the
  in-scope citations) or implemented as a real tool with access to the
  citation store — see Decision 4.
- The `focus`/`entities` parameters carry the direction: the planner's
  `evidence_needed` per request is a natural source for this text.
- Output shape: a list of extracted claims, each with (claim, location
  reference, salience). The caller sees a bounded summary; the claims enter
  the normal fact pipeline as `unverified` facts (Decision 2 covers
  provenance).

## Design decisions to be made

These are the open questions. Each is listed with the options and the
current leaning; none are decided.

### Decision 1: Storage vs. context separation

Today `Citation.content` is capped at the same limit the context serves
(`CITATION_CONTENT_LIMIT`), because recall re-injects stored content
directly. For `summarize_source` to read "the entire source," stored content
must exceed the context window.

- **Option A — two-tier storage:** raise the storage cap substantially
  (e.g. 50–100K chars per citation), keep serving caps as they are
  (feedback cap, recall serving cap). Only the summarizer sub-call sees the
  full store. Partially reverses the 3.3.1a rationale ("capped at 5K
  because recall re-serves it") — the cap's *reason* changes from
  "storage = context mirror" to "serving window." Snapshot/persistence
  growth must be re-examined (knowledge_snapshot would grow; may need a
  separate summarizer-visible tier or snapshot truncation policy).
  - *Leaning:* this one. Storage is cheap; the DB snapshot policy is the
    main cost. But see the open question below.
- **Option B — re-fetch on summarize:** the tool re-fetches the URL at call
  time and summarizes the fresh body. No storage growth; but network
  latency/failure re-enters, cached/deduped fetch semantics get murky, and
  APIs with drift (or paid sources fetched once) become non-reproducible.
- **Open question:** how large is "enough"? PokeAPI's full payload is
  ~590K unpruned / ~379K pruned. A 100K store still can't hold it. Is a
  partial store (first N chars) acceptable, with the summarizer told the
  source is truncated? Or is store-size a per-source-class decision?

### Decision 2: Provenance of extracted facts

Extracted claims are model output about a source, not source text. If the
caller treats them as verified, we've built a hallucination path.

- **Option A — extracted claims become `unverified` facts citing the
  original citation,** with extraction metadata (e.g. an `extraction`
  field on Fact, or reuse of the `derivation` pattern) so the reviewer
  sees their origin. The existing research_review verification flow
  (reviewer-driven, checks claims against stored citation content) then
  applies. Risk: the reviewer only sees the same capped window — can it
  actually verify a claim about content at char 40K?
- **Option B — summarize results never enter the fact pipeline.** They are
  advisory context for the caller only; anything the caller asserts still
  needs its own citation-backed path. Safest, but wastes the extraction.
- **Option C — summarize returns verbatim quotes + locations**, not
  paraphrase. The caller (or pipeline) builds facts from quotes, keeping
  the verification anchor. Costs more output tokens; strongest provenance.
- *Leaning:* A with C's flavor — extraction returns claims each anchored
  to a short verbatim quote. The reviewer's window limitation is real and
  must be answered (possibly: the reviewer gets the same summarize call
  when verifying, or verification targets the quote).

### Decision 3: Which model runs the summarizer

- Workflow model (same Q5) — consistent, but each summarize call competes
  for the same inference capacity and quality.
- A smaller/cheaper model — extraction is a narrower task; a cheap model
  may suffice and keeps the expensive model's context untouched. Adds a
  model-resolution surface (config: which model, per-run override?).
- *Leaning:* configurable with the workflow model as default; revisit
  after measurement. Note the sub-call has its own generous context —
  on a small-context workflow model this asymmetry is the whole point.

### Decision 4: Execution shape

- **Intercepted in the research loop** (like `recall_source`): the loop
  synthesizes the result via an inference call. Pros: full access to
  in-scope citations, no executor plumbing, synthetic-result cost
  semantics already exist. Cons: the loop owns an LLM call, blurring
  "tool" vs "node"; retry/timeout handling for the sub-call needs design.
- **Real tool with store access:** a `SummarizeSourceTool` registered like
  any other, reading citations from a store handle. Pros: normal tool
  lifecycle, invocation_cost pricing is natural, usable outside research.
  Cons: needs a citation-store interface for tools (new plumbing).
- *Leaning:* real tool. Pricing it like a real tool (invocation_cost —
  see below) is cleaner in the primary budget, and a store interface is
  useful for the grep+neighborhood tool later. But this is genuinely open.

### Decision 5: Pricing and limits

- `invocation_cost`: should exceed web_search (it spends an LLM call).
  The budget system charges per call via `tool_costs` — a summarize at
  e.g. 2–3× web_search's cost prices deep reading honestly in the primary
  currency and naturally discourages hammering. No shadow/char budget
  needed (per project decision: call-count limits are guardrails, not
  budget).
- Guardrails: per-run and per-pass call limits via the existing
  `tool_call_limits` / `tool_call_step_limits` config, same as any tool.
- Open: does a summarize of an already-summarized citation cost less
  (cache), or is repeat summarization simply rate-limited?

### Decision 6: Interaction with decomposition/planning

- Should the planner see `summarize_source` as a candidate tool alongside
  fetch/search tools (it is listed in candidate_tools), or is it a
  research-loop-only escape hatch the model discovers from tool docs?
- Directed extraction wants the planner's `evidence_needed` text as
  `focus`. Natural flow: planner emits an EvidenceRequest whose
  candidate_tools include summarize_source and whose evidence_needed
  doubles as the focus directive. Zero schema change.
- Open: teach the planning prompt about when summarizing beats
  re-searching (e.g. "store holds page-depth source with relevant title
  but facts stayed unknown")?

## Risks

| Risk | Mitigation |
|------|------------|
| Summarizer hallucinates claims about the source | Claims enter as `unverified` with quote anchors; reviewer verifies (Decision 2) |
| Sub-call latency/cost balloons (long sources, chatty model) | invocation_cost pricing + per-pass call limits (Decision 5); output bounded by feedback cap |
| Storage growth in snapshots/DB | Snapshot policy decision (Decision 1); measure before/after |
| Model summarizes instead of searching when search is right | Planning guidance (Decision 6); measure tool-choice distribution in evals |
| Duplicates recall_source conceptually; model confusion | Keep recall for cheap re-read of the window; summarize for beyond-window extraction; tool docs must draw the line sharply |

## Verification (when scheduled)

- Unit: tool returns bounded output; claims carry citation anchors;
  focus/entities parameters shape the extraction (mocked model).
- Integration: a run with a known long source (PokeAPI pokemon_retrieve)
  verifies facts whose data sits past the window (e.g. `stats`) — the
  `065e23cc` unreachable-data case.
- Eval: tool-choice distribution (summarize vs recall vs re-search);
  verified-facts-per-run on long-source questions; no regression on
  short-source questions.
