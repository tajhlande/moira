# Source Quality and Verification

> **Status:** Brainstorm / pre-plan. Captures findings and design directions.
> Not yet phased. Related to but separate from the goal-alignment plan.

## Problem

The agent repeats claims from user-generated community sources (Reddit, forums)
as verified facts. In the `tyranitar-ou` case (run
`44cc5774-0399-4c19-a38c-dfd5048b0b8f`, 2026-07-20), a Reddit `r/stunfisk`
**question post** was the sole source for a false superlative claim that was
marked "verified" and stated as established fact in the report.

### The claim chain

| Stage | What happened | Where |
|-------|--------------|-------|
| Source (cit002) | Reddit `r/stunfisk` post: *"How has Tyranitar remained in OU over the years despite its [weaknesses]?"* Body asserts: *"Tyranitar has the most weaknesses out of any pokemon."* This is a user's framing for their question — not an authoritative answer. | `reddit.com/r/stunfisk/comments/14h3etq/...` |
| Fact extraction (f005) | Researcher extracted: *"There is limited specific Gen 9 OU data found, but Tyranitar has the most weaknesses of any Pokemon according to community discussion."* Honestly flagged as "community discussion" but still extracted as a fact. | `knowledge_snapshot.facts` |
| Verification (f005) | Marked **verified**. verification_note: *"Citation [cit002] explicitly states that Tyranitar has the most weaknesses of any Pokémon."* — **tautological verification**: "the source says X" → verified. | `knowledge_snapshot.facts` |
| Report | *"possessing the greatest number of type weaknesses among all Pokémon [1]"* — the "according to community discussion" hedge was **dropped entirely**. | `workflow_runs.report` |

### What went wrong at each layer

**Layer 1 — Research: false superlative extracted while authoritative data was in hand.**

The claim is **false**. Tyranitar (Rock/Dark) has 7 weaknesses — tied with
several other type combinations (e.g., Grass/Ice also has 7). The agent already
had authoritative type data from `pokemon.com/pokedex` (cit003), Bulbapedia
(cit004), and pokemondb.net (cit001), all listing the same 7 weaknesses. None of
them make the "most of any Pokemon" claim because it isn't true. The agent
extracted the Reddit user's assertion instead of computing the ranking from the
type data it already had.

Additionally, **f005 doesn't answer its own `fact_needed`** — the fact was
supposed to identify "The most prevalent Pokemon that exploit Tyranitar's
specific type weaknesses." The extracted claim is about the *count* of
weaknesses, not which Pokemon exploit them. It's a non-sequitur pulled from an
adjacent discussion because the researcher couldn't find the actual answer.

**Layer 2 — Evaluation: tautological standard.**

The evaluator's logic was *"does cit002 contain this claim?"* → yes → verified.
It never asked:
- Is the source authoritative for this type of claim?
- Is the claim actually true?
- Does any other source corroborate or contradict it?

The `evaluation.system` prompt is **completely silent on source credibility**
— unlike `research_review.system` (prompts.md:722-728), which has a credibility
instruction ("Official documentation, peer-reviewed research, and specialized
databases carry more weight than forum posts"). The evaluator has no such rule.

**Layer 3 — Report: qualifier dropped, false claim stated as established fact.**

f005 said "according to community discussion." The report dropped that and wrote
"the greatest number of type weaknesses among all Pokémon." This is part of a
known pattern of report drift from the knowledge model. **Deferred** — will
become relevant when the goal-alignment plan's Change 4 (report synthesis) is
implemented.

## Design tension: cognitive load on a mid-grade model

The agent runs on `Qwen3.6-35B-A3B` — a mid-grade model. Stacking sophisticated
judgments on it ("infer credibility from URL" + "assess if the claim is true"
+ "recognize superlatives as hard to verify" + "cross-reference against other
citations") is fragile. The model will succeed at some and fail at others
unpredictably.

**Core principle: mechanical rules based on structured data beat nuanced
judgment.** The fix is not asking the model to be smarter — it's reducing the
reasoning load by giving it structured inputs so the judgment becomes rule
application rather than open-ended assessment. "This source is tagged
`community`. The claim is factual. Rule: community sources alone can't verify
factual claims → don't verify." A mid-grade model can apply that rule reliably.
It struggles with "assess whether this Reddit post is credible enough for this
specific claim."

Two interventions attack the cognitive load from different angles:

1. **Source-type classification** — reduces the *classification* problem. The
   model doesn't infer "is reddit.com authoritative?" — it's told "this is
   community." The rule becomes mechanical.
2. **Full content via url_content** — reduces the *evidence* problem. A 200-char
   search snippet is hard to evaluate because it lacks context. Full page
   content gives the model material to work with — and the evaluator inherits
   richer content automatically via `_format_citation_content`.

They are complementary. Source type without full content = knows what kind of
source but lacks text to evaluate. Full content without source type = has text
but no signal on how to weight it. Both together = structured input + adequate
evidence → mechanical rule application.

## Current architecture: what exists and what's missing

### No source-type field on citations

`Citation` TypedDict (`backend/moira/models/knowledge.py:30-40`) has:
`id, source, url, title, excerpt, snippets, content`. No `source_type`,
`domain`, `reliability_tier`, or `credibility` field. The evaluator sees the
raw URL in `_format_citation_content()` (`_helpers.py:412-415`) and must infer
credibility from the string.

### Tool reliability field exists but is never propagated

`ToolDefinition.reliability` (`backend/moira/tools/base.py:20`) is a free-text
field with values like "high" or "unknown." `pokeapi__*` tools carry reliability
but it is **never copied onto citations** when a tool produces a source. This is
a free win currently wasted.

### Evaluation prompt has no credibility instruction

`evaluation.system` (`prompts.md:773-828`) is silent on source credibility.
`research_review.system` (`prompts.md:722-728`) has a credibility instruction
but it's the reviewer, not the evaluator. The evaluator — the node that marks
facts verified — has no rule about source quality at all.

### Attachment points (ranked by leverage)

1. **`Citation` TypedDict** — `models/knowledge.py:30-40`. Add a
   `source_type: NotRequired[str]` field. Automatically persists via
   `knowledge_summary()` — no DB migration needed for in-memory classification.
2. **`_find_or_merge_citation()`** — `workflow/nodes/research.py:776-859`. Single
   chokepoint where every citation is constructed. A classification lookup
   (URL → domain → type) could be injected here.
3. **`web_search.py:431-438` and `url_content.py:122-131`** — where structured
   results are built. URL is already in hand; domain extraction + classification
   is trivial here.
4. **`_format_citation_content()`** — `_helpers.py:376-418`. The format string
   that renders citations for the evaluator/reviewer. If source_type is added to
   Citation, it must be rendered here for the evaluator to see it.
5. **`evaluation.system`** — `prompts.md:773-828`. Add credibility rule
   (currently silent).
6. **`reliability` on `ToolDefinition`** — `tools/base.py:20`. Existing field
   that could be copied onto citations at creation as a baseline signal.

## Classification approaches

| Approach | What it is | Pros | Cons |
|----------|-----------|------|------|
| **Tool reliability propagation** | Copy existing tool `reliability` field onto citations at creation | Zero maintenance, already built | Coarse — all `web_search` results get "unknown" regardless of site |
| **Curated registry (~30-50 domains)** | Hand-maintained domain → type map, with path-level rules (`smogon.com/dex` = reference, `smogon.com/forums` = community) | Precise, handles nuance cases | Maintenance burden, doesn't cover long tail |
| **Heuristic fallback** | TLD + URL pattern matching (`.gov` → institutional, `/forum/`, `/threads/` → community, known UGC platforms) | No maintenance, covers long tail | Less precise, can misclassify |
| **LLM classification at fetch time** | Ask a small model to classify URL + snippet | Most flexible | Adds latency/cost, another failure mode |

Likely sweet spot: **tool reliability propagation + curated registry (30-50,
with path-level nuance) + heuristic fallback.** The registry only needs entries
for domains where classification is ambiguous at the path level
(smogon.com, wikipedia.org, reddit.com with subreddit variation). Everything
else falls to heuristics.

### Minimal useful taxonomy

Four categories — simple enough for a mid-grade model to reason about,
meaningful enough to change behavior:

| Type | Description | Examples |
|------|-------------|----------|
| `reference` | Structured databases, encyclopedias with editorial oversight | Smogon dex, Wikipedia, PokeAPI, government statistics, IMDb |
| `editorial` | News, institutional, journals with editorial standards or peer review | BBC, NYT, `.gov` sites, PubMed |
| `community` | User-generated content, forums, social media — no editorial control | Reddit, Smogon forums, YouTube comments, personal blogs |
| `commercial` | Manufacturer/retailer sites, promotional content | Product pages, marketing blogs |

### The topical-authority nuance

Source type is domain-independent. But the *authority* of a source depends on
the topic: Smogon forums are authoritative for competitive Pokémon strategy but
irrelevant for medical claims. Two options:

1. **Source type only** — let the model reason about topical authority via
   prompts. The model already knows Smogon is authoritative for Pokémon.
   Lowest maintenance.
2. **Registry with topic tags** — mark domains as authoritative for topics
   (`smogon.com` → authoritative for "competitive Pokémon"). More precise but
   requires maintenance per topic.

Starting with source type only is recommended. The model is good at topical
reasoning; the gap is in source-type classification, not topical judgment.

## Policy options (what to do with the classification)

Where the actual behavior change happens:

1. **Prompt-level scrutiny (cheapest):** Add source_type to citation content the
   evaluator sees. Add a credibility rule to `evaluation.system` (currently
   lacks one). Let the model apply judgment. Measurable immediately, reversible.
2. **Corroboration requirement (medium):** Claims backed only by `community`
   sources need corroboration from a `reference` or `editorial` source to be
   `verified`. Enforced in evaluation logic. More reliable but rigid.
3. **New fact status (heaviest):** Add `community_supported` status between
   `verified` and `unverified`. Schema change, UI change, report change.

Recommended path: **prompt-only first, escalate to code-level enforcement if the
model doesn't apply the rule consistently.**

## url_content usage

Separate but complementary intervention. The evaluator currently sees whatever
the researcher captured — often just a search snippet (200 chars). Encouraging
the model to use `url_content` before extracting factual claims would give the
evaluator richer evidence to work with.

Note: for the specific Reddit case, url_content wouldn't have fixed it (the post
is a question regardless of length). The source-type tag is what would have
changed the outcome. But for many cases, full page content vs. snippet is the
difference between a claim that can be evaluated and one that can't.

**Related:** Phase 2 URL dedup (commit `ce33f98`) already deduplicates
url_content calls. The model IS using url_content (42 calls deduped across 7
eval runs), but the ratio of url_content to web_search calls hasn't been
measured. This is worth investigating before adding prompt guidance.

## Open questions

- Should source classification be a standalone track, or folded into the
  goal-alignment plan?
- What's the actual ratio of url_content to web_search calls in recent runs?
  Is underutilization real?
- Should the evaluator's credibility rule be per-claim-type (factual vs.
  opinion vs. superlative) or just per-source-type?
- Does the `reliability` field on tools use a controlled vocabulary, or is it
  free text? (Currently free text — may need standardization.)

## Deferred

- **Report drift from knowledge model** — the report dropped f005's "according
  to community discussion" qualifier and stated the claim as flat fact. This is
  a known pattern. Deferred until the goal-alignment plan's Change 4 (report
  synthesis) is implemented.
- **Cross-invocation source memory** — tracking source quality across research
  passes within a conversation.
