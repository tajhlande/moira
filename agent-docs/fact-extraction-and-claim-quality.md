# Fact Extraction and Claim Quality

## Problem

Eval scores are flat across recent batches (57-67% range, 2-3 of 7 PASS).
Investigation of the 2026-07-18 eval batch (7 runs) identified three issues in
fact extraction and claim quality. The citation chain is intact (zero verified
facts lack citations) and search quality is adequate when engines function —
the bottleneck is in how claims are written, refined, and validated.

### Issue 1: Contradicted facts cannot be corrected without expensive retry

When research_review marks a fact "contradicted" because the claim is slightly
imprecise, the only recovery path is a full research retry. The reviewer already
knows what the evidence actually says but can only assign a binary verdict.

Example: the model claims an event happened in "1997." The source says "1996 or
1997." The reviewer marks the fact contradicted. The truth — "1996 or 1997" —
is known to the reviewer but cannot be applied. A full retry burns budget and
search calls to re-discover what the reviewer already sees.

### Issue 2: url_content is almost never used

> **Note:** This premise was **disproven** by later eval batches. By the
> 07-19 batch, url_content was called ~15 times per run (76 calls vs 70
> web_search across the batch). The model uses the tool aggressively.
> Phase 2 was reframed to address the real problem — URL retry waste.
> See the Phase 2 section below for details.

Across the 7-run benchmark:

| Tool | Total calls | Per-run average |
|---|---|---|
| web_search | 69 | ~10 (always hits the cap) |
| url_content | **3** | 0.4 (only one run used it) |
| calculator | 0 | 0 |

The model relies almost entirely on 500-character search snippets. Snippets
often lack the specific information needed to write precise claims. The
url_content tool exists to fetch full page content but the model rarely reaches
for it.

### Issue 3: Process-metadata claims leak through detection

6 of 65 facts (9%) across the benchmark have process-metadata claims that
describe the search process rather than the research subject. All 6 are in a
single run (future-nostalgia), which scored 14/25 FAIL.

The claims use academic phrasings the current patterns miss:
- "No peer-reviewed research findings were found identifying..."
- "No research findings were found documenting..."
- "No validated instruments were found for..."
- "No empirical data was found on..."
- "No theoretical models were found addressing..."
- "No peer-reviewed publications on... were identified..."

The existing `_PROCESS_METADATA_PATTERNS` catch shorter phrasings ("insufficient
data", "no sources found") but miss these longer academic variants. Rather than
extending the regex patterns, Phase 3 replaces the pattern-based approach
entirely with reviewer-based detection.

## Approach

### Principles

1. **Fix the common case efficiently.** The "1996 or 1997" pattern — where the
   reviewer can see the corrected truth in the evidence — should not require a
   full retry. Fix it at the point of detection.

2. **Corrections are optional, not forced.** The reviewer should correct only
   when the evidence clearly supports a better claim. When it cannot, the fact
   stays contradicted — no guessing.

3. **Guide, don't force, url_content usage.** The model should reach for
   url_content when snippets are insufficient. Prompting is the starting point;
   the model still decides dynamically based on snippet quality.

4. **Prefer model judgment over brittle patterns.** Process-metadata claims
   should be detected by the reviewer (which understands any phrasing) rather
   than regex patterns (which require maintenance and miss variants).

## Implementation Plan

### Phase 1: Claim correction in research_review

**Goal:** When the reviewer detects a contradiction and the cited evidence
clearly supports a better claim, allow it to provide a corrected claim that is
applied immediately — flipping the fact to verified without a retry.

**Files:**
- `backend/moira/workflow/nodes/research_review.py`
- `backend/moira/resources/prompts.md` (`research_review.system`)
- `backend/moira/models/knowledge.py` (add correction tracking to Fact, optional)
- `backend/tests/test_research_review_node.py` (new or extended)

**Changes:**

1. **Extend the research_review output schema.**
   Add `corrected_claim` (and optionally `corrected_citation_ids`) as optional
   fields on each item in `fact_results`. The prompt instructs the model to
   provide a correction only when the cited evidence clearly supports a better
   claim:

   ```
   When you mark a fact "contradicted" because the claim is imprecise but the
   cited evidence clearly supports a better claim, provide a corrected_claim
   that accurately reflects what the evidence says. If the evidence is
   insufficient to write a complete correction, leave corrected_claim empty and
   the fact will remain contradicted.

   Example: claim "1997", source says "1996 or 1997"
   → result: "contradicted"
   → corrected_claim: "The event occurred in 1996 or 1997"
   → the fact will be updated to verified with the corrected claim
   ```

2. **Apply corrections in the review processing code.**
   In the fact_results loop (`research_review.py:211-220`), when a fact is
   marked contradicted AND a non-empty corrected_claim is provided, apply the
   correction and flip the status to verified:

   ```python
   for fr in parsed.get("fact_results", []):
       fid = fr.get("fact_id", "")
       result = fr.get("result") or fr.get("status") or "unverified"
       evidence = fr.get("evidence") or ""
       corrected_claim = (fr.get("corrected_claim") or "").strip()

       for fact in facts:
           if fact["id"] == fid:
               if result == "contradicted" and corrected_claim:
                   fact["claim"] = corrected_claim
                   fact["status"] = "verified"
                   fact["corrected"] = True
                   if fr.get("corrected_citation_ids"):
                       fact["citation_ids"] = fr["corrected_citation_ids"]
               else:
                   fact["status"] = result
               if evidence:
                   fact["verification_note"] = evidence
               break
   ```

3. **Track that a fact was corrected.**
   Add a `corrected: bool` flag to the Fact model, defaulting to `False`. Set
   to `True` when a corrected_claim is applied (in the logic below). This lets
   downstream nodes and the report surface corrections for transparency — the
   report can indicate which facts were refined during review.

4. **Routing impact.**
   Corrected facts flip from contradicted to verified, so they no longer
   count toward the "critical facts remain contradicted" threshold that
   triggers retries. This naturally reduces unnecessary retry loops.

**Design consideration — synthesis staleness:**

Synthesis runs before research_review, so conclusions are built from original
(pre-correction) claims. If a fact's claim changes during review, conclusions
referencing that fact have stale wording.

Example after correction:
- Fact f003: "The event occurred in 1996 or 1997" (corrected, verified)
- Conclusion c001: "The event happened in 1997" (built from old claim, verified)

Report generation receives both verified facts and verified conclusions, so the
model may see slight inconsistencies. This is accepted — evaluation is the
safety net, and the corrected fact is accurate in the knowledge model. Moving
synthesis after research_review would be a major topology change not justified
by this issue.

### Phase 2: URL fetch dedup (hard enforcement)

**Goal:** Prevent the model from wasting budget on url_content calls for URLs
it has already fetched (successfully or unsuccessfully) within the same
research session.

**Why the original Phase 2 was reframed:** The initial premise — "url_content
is almost never used" (3 calls across a 7-run benchmark) — was disproven by
later eval batches. By the 07-19 batch, url_content was called ~15 times per
run (76 calls vs 70 web_search across the batch). The model uses the tool
aggressively; the real problem is that it retries URLs it has already
fetched.

**Problem (measured):** Database investigation of the water-blood-pressure
run (`8879d3a9`) showed 15 url_content calls with heavy retry waste:

| URL | Attempts | Outcome |
|---|---|---|
| `ahajournals.org/.../01.CIR.101.5.504` | 3 | FAIL × 3 |
| `pmc.ncbi.nlm.nih.gov/articles/PMC10807041/` | 3 | OK × 3 (content already in citations) |
| `sciencedirect.com/.../S2161831323001424` | 2 | FAIL × 2 |
| `ncbi.nlm.nih.gov/books/NBK526069/` | 2 | OK × 2 |

Failure rate is ~30-50% (paywalled/JS-rendered sites). The model retries both
successes (content already available) and failures (same URL will fail
again). Prompt-only guidance is insufficient — the model already ignores its
own previous tool results within the same session.

**Files:**
- `backend/moira/workflow/nodes/research.py` (new helpers + loop integration)
- `backend/moira/resources/prompts.md` (`research.system`,
  `research.system_native_tools` — document the dedup behavior)

**Changes:**

1. **Track URL fetch attempts in research state.**
   A `fetched_urls` dict maps URL → `{status, cit_id?, error?}`. It is
   initialized from `seen_urls` at the start of each research invocation,
   so URLs fetched in prior passes are also covered. It is updated after
   each round's tool execution.

2. **Pre-dispatch filter (`_partition_url_content_calls`).**
   After `_validate_and_filter_calls` accepts calls but before
   `executor.execute_batch`, partition url_content calls:
   - URL previously succeeded (citation exists) → synthesize a recurring
     result pointing to the existing citation
   - URL previously failed → synthesize a failure with the prior error
   - URL not yet attempted → execute normally

3. **Synthetic results (`_build_synthetic_url_result`).**
   Build `ToolResult` objects marked with `metadata["synthetic"] = True`:
   - Success: carries `metadata["results"]` pointing at the existing
     citation so `_process_execution_results` marks it as "(recurring
     source)" — the model sees the citation ID and knows not to refetch.
   - Failure: empty `metadata["results"]` with `success=False`, output
     text tells the model the URL previously failed and to try a
     different one.

4. **Cost skipping in `_process_execution_results`.**
   Synthetic results don't execute, so they don't consume budget. The
   cost-charging loop checks `metadata.get("synthetic")` and skips the
   deduction. `call_counts` is also decremented for synthetics so per-run
   and per-step limits reflect only real executions.

5. **Prompt guidance.**
   Both `research.system` and `research.system_native_tools` gain a rule:
   "Never call url_content on a URL you have already fetched in this
   research session." This reinforces the hard enforcement at the model
   level.

**What is NOT covered (deferred):**

- **Cross-invocation failure memory.** Failed URL fetches leave no
  citation record, so across research retries the model can't know which
  URLs failed in a prior pass. Within-invocation failures ARE tracked.
  Cross-pass failure tracking would require persisting `fetched_urls` in
  execution_state.

- **Within-batch dedup.** If the model emits two identical url_content
  calls in a single batch, both execute. This is rare (the model usually
  emits different calls in one batch) and can be added later if observed.

- **URL normalization.** URLs with different fragments or trailing slashes
  are treated as distinct. The model usually copies URLs verbatim from
  search results, so this is a minor concern.

### Phase 3: Process-metadata claim detection via research_review

**Goal:** Eliminate the brittle `_PROCESS_METADATA_PATTERNS` regex layer and let
the research reviewer detect claims that describe the search process rather than
the research subject.

**Problem with the current approach:** `_PROCESS_METADATA_PATTERNS` in
`research.py` is a defensive regex layer that tries to catch "no results found"
phrasings at write time. It requires a new pattern for every phrasing variant,
and local models are endlessly creative. The academic variants ("No peer-reviewed
research findings were found...") leaked through because the patterns only
matched shorter phrasings.

**Better approach:** The research reviewer already evaluates whether each claim
answers `fact_needed` and is supported by citations. A process-metadata claim
fails on both counts — it describes the search process, not the subject, and
typically has no citations. The reviewer can recognize this in any phrasing
without patterns.

**Files:**
- `backend/moira/workflow/nodes/research_review.py` (handle "unknown" result)
- `backend/moira/resources/prompts.md` (`research_review.system`)
- `backend/moira/workflow/nodes/research.py` (remove `_PROCESS_METADATA_PATTERNS`,
  `_is_process_metadata_claim`, and related call sites)
- `backend/tests/test_research_internals.py` (remove pattern tests)
- `backend/tests/test_research_review_node.py` (new — reviewer detection tests)

**Changes:**

1. **Add "unknown" as a valid reviewer result.**
   The reviewer currently uses verified/contradicted/unverified. Add "unknown"
   for facts whose claims are not factual assertions about the subject:

   ```
   - "unknown": the claim describes the research process rather than answering
     fact_needed (e.g., "no results found", "insufficient data", "no studies
     exist", "no peer-reviewed publications were found"), or the claim is
     otherwise not a factual assertion. The fact will be reverted to unknown
     status so it can be re-researched.
   ```

2. **Handle "unknown" result in the review processing code.**
   When the reviewer marks a fact "unknown", revert the fact to unknown status
   and clear the claim so it doesn't contaminate downstream nodes:

   ```python
   for fr in parsed.get("fact_results", []):
        fid = fr.get("fact_id", "")
        result = fr.get("result") or fr.get("status") or "unverified"
        evidence = fr.get("evidence") or ""
        corrected_claim = (fr.get("corrected_claim") or "").strip()

        for fact in facts:
            if fact["id"] == fid:
                if result == "unknown":
                    fact["status"] = "unknown"
                    fact["claim"] = ""
                    fact["citation_ids"] = []
                elif result == "contradicted" and corrected_claim:
                    # ... Phase 1 correction logic ...
                else:
                    fact["status"] = result
                if evidence:
                    fact["verification_note"] = evidence
                break
   ```

3. **Remove `_PROCESS_METADATA_PATTERNS` and `_is_process_metadata_claim`** from
   `research.py` entirely. Remove the pattern checks in
   `_apply_discovered_facts` and `_cleanup_empty_claims`. The reviewer is now
   the sole defense against process-metadata claims.

   Note: `_cleanup_empty_claims` should still revert facts with empty claims to
   unknown (that logic is unrelated to process-metadata detection and handles a
   different case — facts auto-promoted to unverified that never received a
   claim during the research loop).

**Why this works:**

- The reviewer is a model — it recognizes any phrasing of "no results found"
  without regex patterns
- "unknown" status naturally triggers retry routing, giving the model another
  chance to find real information
- The claim is cleared so it doesn't confuse the retry context or downstream
  nodes
- Less code, more robust, no pattern maintenance burden

**Why early detection (during research) is not needed:**

The patterns currently fire during research to prevent process-metadata claims
from being stored. Without them, such claims persist as "unverified" through
the research loop. This is harmless:
- The model sees the fact as pending via `_format_unknown_facts` regardless of
  status (unknown or unverified)
- The reviewer catches the claim at review time and reverts to unknown
- Retry routing fires, giving the model a fresh research pass
- Temporary storage of a process-metadata claim doesn't affect correctness

### Phase 4: Curate citations from verified facts for report generation

**Goal:** Report generation should receive only citations referenced by verified
facts and conclusions, pre-numbered. This eliminates the `cit001 → [1]`
translation problem and removes noise from irrelevant sources.

**Problem in detail:** `report_generation.py:190,222` currently passes ALL
accumulated citations to the model — including sources for contradicted facts,
unknown facts, tangential searches, and fallback-extracted facts. The model
must simultaneously write prose, curate relevant sources, AND translate
`cit001`-style IDs to positional `[1]` markers. The translation is mechanical
overhead that local models frequently botch, resulting in silently dropped
citations.

**Files:**
- `backend/moira/workflow/nodes/report_generation.py`
- `backend/moira/workflow/nodes/synthesis.py` (auto-populate conclusion
  citation_ids from supporting facts — needed so conclusions also contribute
  to the curated citation set)
- `backend/tests/test_report_generation.py` (new or extended)

**Changes:**

1. **Auto-populate citation_ids on conclusions in synthesis.**
   Synthesis conclusions currently only carry `supporting_fact_ids`
   (`synthesis.py:148-155`). After synthesis produces conclusions, collect
   citation_ids from each conclusion's supporting facts:

   ```python
   fact_lookup = {f["id"]: f for f in facts}
   for conclusion in conclusions:
       collected = []
       for fid in conclusion.get("supporting_fact_ids") or []:
           fact = fact_lookup.get(fid)
           if fact:
               collected.extend(fact.get("citation_ids") or [])
       conclusion["citation_ids"] = sorted(set(collected))
   ```

   No prompt change needed — the data is already there via fact linkage.

2. **Collect referenced citation IDs in report_generation.**
   Build a set of citation IDs referenced by verified facts and verified
   conclusions:

   ```python
   referenced_ids = set()
   for f in verified_facts:
       referenced_ids.update(f.get("citation_ids") or [])
   for c in verified_conclusions:
       referenced_ids.update(c.get("citation_ids") or [])
   ```

3. **Build a curated, ordered citation list.**
   Preserve order of first appearance across verified_facts then
   verified_conclusions. Build a `cit_id → positional` map:

   ```python
   seen = []
   seen_set = set()
   for f in verified_facts:
       for cid in f.get("citation_ids") or []:
           if cid not in seen_set:
               seen.append(cid)
               seen_set.add(cid)
   # repeat for verified_conclusions
   cit_to_pos = {cid: i for i, cid in enumerate(seen, 1)}
   curated_citations = [
       next(c for c in all_citations if c["id"] == cid)
       for cid in seen
       if any(c["id"] == cid for c in all_citations)
   ]
   ```

4. **Rewrite `_format_facts` and `_format_conclusions`** to render positional
   numbers using the map:

   ```python
   def _format_facts(facts, cit_to_pos):
       for f in facts:
           positions = sorted(
               cit_to_pos[cid] for cid in (f.get("citation_ids") or [])
               if cid in cit_to_pos
           )
           cit_str = ", ".join(str(p) for p in positions)
           # ... format line with [cit_str] instead of [cit001, cit003]
   ```

5. **Pass curated_citations (not all_citations) to `_format_citations` and
   `_prune_and_renumber_citations`.** The citation list the model sees is the
   curated subset, numbered 1..N. The model writes `[1][3]` markers directly.

**What this eliminates:**
- The `cit001 → [1]` translation problem (pre-numbered)
- Noise from irrelevant sources (contradicted, unknown, tangential)
- The model curating sources while writing prose

**What this preserves:**
- Excluded sources remain in the knowledge state and surface in the report's
  side channels (`uncited_sources`, `contradicted`, `unknown_facts`) for audit.

### Phase 5: Fact extraction discipline

**Goal:** Stop the model from writing "insufficient data found" claims when
the search results actually contain the information. Address the root cause
through prompt changes — the model should either extract a factual claim or
omit the fact entirely.

**Root cause analysis:**

Database investigation revealed that the 07-14 jazz-trumpeters run had 6
contradicted facts — ALL of which were process-metadata claims like
"Insufficient data found about Miles Davis's specific 1960s stylistic
directions." The reviewer correctly contradicted these because the cited
sources DID contain the information. After the `_PROCESS_METADATA_PATTERNS`
detection was added (cleanup-empty-claims branch), these claims are caught
at research time and the facts stay `unknown`. But the underlying extraction
failure remains: the model finds relevant sources but writes "insufficient
data" instead of extracting the factual content.

Three gaps in `research.system_native_tools` (the prompt the agent uses)
cause this behavior:

1. **Missing "omit" instruction.** The non-native prompt (`research.system`)
   explicitly says: "If you cannot find any information for a fact, simply
   omit it from discovered_facts." The native-tools prompt does NOT have
   this instruction. The model feels obligated to write something for every
   fact — and "insufficient data found" is what it writes when it gives up.

2. **No prohibition of process-describing language.** Neither prompt says
   "never write claims about the research process." The model thinks
   "Insufficient data found about X" is a valid factual observation.

3. **No extraction discipline.** The prompt says "extract specific fact
   claims" but doesn't say "before concluding a fact can't be answered,
   review all search results for mentions of the subject."

**Files:**
- `backend/moira/resources/prompts.md` (`research.system_native_tools`)

**Changes (prompt-only, no code):**

1. **Port the "omit" instruction** into the native-tools Rules section:
   ```
   - If you cannot find any information for a fact, omit it from
     discovered_facts. Do not write entries for facts you could not resolve.
   ```

2. **Prohibit process-metadata language:**
   ```
   - Never write claims describing the research process — "insufficient data
     found", "no results available", "no studies exist" are NOT factual
     claims. A claim must be a factual assertion about the subject. "Miles
     Davis pioneered modal jazz in the 1960s" is a claim. "Insufficient data
     found about Miles Davis" is not.
   ```

3. **Add extraction discipline** after the Search strategy section:
   ```
   Extraction discipline:
   - Before concluding a fact cannot be answered, carefully review ALL search
     results for mentions of the fact's subject. If any result mentions the
     subject, extract the specific factual information — do not abandon
     extraction just because the answer isn't in the first snippet.
   ```

**Interaction with other phases:**
- Makes **Phase 3** (reviewer-based process-metadata detection) a safety net
  rather than the primary defense. `_PROCESS_METADATA_PATTERNS` can still be
  removed — the claims should stop being written at the source.
- Complements **Phase 2** (url_content): extraction discipline handles "the
  info is in the snippet but the model gave up"; url_content handles "the
  info is in the page body but not the snippet."

**Why this is prompt-only:**
The `_is_process_metadata_claim` detection in `research.py:543-554` already
catches these claims and skips the update — the fact stays `unknown`. This
prevents pollution but wastes the research budget: the model searched, found
relevant results, but produced no usable output. The prompt fix attacks the
root cause so the model extracts real claims instead of process descriptions.

## Verification

### Per-phase testing

- **Phase 1:** Unit tests for the correction application logic — when
  corrected_claim is provided, fact flips to verified with the new claim; when
  absent, fact stays contradicted. Integration test verifying the prompt
  schema produces parseable corrections.
- **Phase 2:** Manual testing — run 2-3 eval questions and verify url_content
  call count increases. Check that fetched content improves claim precision.
- **Phase 3:** Unit tests for the reviewer's "unknown" result handling — when
  reviewer marks a fact unknown, it reverts to unknown status with cleared claim.
  Integration test verifying the reviewer prompt detects all six future-nostalgia
  phrasings. Verify `_PROCESS_METADATA_PATTERNS` removal doesn't break existing
  research flow.
- **Phase 4:** Unit test for the curation helper — given verified facts with
  citation_ids and a full citation list, produces the correct curated, numbered
  subset. Test the `cit_id → positional` rewrite on fact formatting. Test
  synthesis auto-population of conclusion citation_ids.
- **Phase 5:** Manual testing — run jazz-trumpeters (the question most affected
  by process-metadata claims). Verify: facts with non-empty claims increase,
  zero "insufficient data" style claims in discovered_facts, verified fact
  count increases, and `_is_process_metadata_claim` warning logs drop to
  near-zero.

### Eval batches

Run the eval harness after Phase 1 (highest impact) and after subsequent
phases:

```
moira_eval log --batch --commit <sha>
```

**Key metrics:**
- PASS count (target: >3 of 7)
- Contradicted fact count (should drop as corrections flip them to verified)
- url_content call count (should increase from baseline of 3)
- Process-metadata claim leakage (should drop to 0)
- Citation precision (reports should only cite sources from verified facts)
- Facts with non-empty claims (should increase — Phase 5 extraction discipline)

**Questions to watch:**
- `water-blood-pressure`, `jazz-trumpeters` — zero-fact failures (all facts
  stay `unknown`). Phase 5 (extraction discipline) is the primary fix; Phase 2
  (url_content) helps for body-of-page content. These are the highest-signal
  questions for Phase 5 impact.
- `future-nostalgia` — direct measure of Phase 3/5 impact (process-metadata
  leak). Phase 5 should prevent the claims from being written; Phase 3 catches
  any that slip through.
- `telescope-mount-cost` — niche-technical question with facts staying unknown
  (6 of 14 in recent runs). Phase 5 (extraction discipline) and Phase 2
  (url_content for technical specs) are the primary fixes. Note: this question
  produces ZERO contradicted facts — Phase 1 (claim correction) will not
  trigger here.
- `tyranitar-ou`, `flaming-hot-cheetos` — consistent passers. Watch for
  regressions.

### Demo preparation (Day 5)

After quality fixes land:

1. **Curate 3-5 reliable demo questions** from the eval set and beyond. Pick
   questions where the verification loop is visible — where the agent catches
   and corrects itself, where citations clearly support claims, where the
   structured fact lifecycle is on display.

2. **Rehearse the live walkthrough.** Run each demo question end-to-end,
   confirming the report renders cleanly with inline citations and the
   workflow steps are visible in the UI.

3. **Prepare the differentiation narrative.** For AI tinkerers, the key
   differentiators are:
   - Verification loop (research → review → evaluation → retry or accept)
   - Structured facts with lifecycle (unknown → unverified → verified)
   - Claim correction during review (contradictions resolved without retry)
   - Inspectable pipeline (every step, every tool call, every fact transition
     visible)
   - Budget management (per-step and per-run tool call limits)

## Five-day timeline

| Day | Focus                                                             | Phase                  |
|-----|-------------------------------------------------------------------|------------------------|
| 1   | Claim correction in research_review                               | Phase 1 (done)         |
| 2   | URL fetch dedup + citation curation                               | Phase 2 (done), 4 (deferred) |
| 3   | Extraction discipline + reviewer-based process-metadata detection | Phases 5, 3 (done)     |
| 4   | Eval run, iterate on prompts, polish                              | In progress            |
| 5   | Demo prep, rehearsal, narrative                                   | —                      |

Phase 5 (extraction discipline) is the highest-impact change for the zero-fact
failure mode — it addresses why the model writes "insufficient data found"
when search results contain the information. Phase 1 (done) addresses the
"1996 or 1997" imprecision pattern that occurs when claims ARE extracted.
Phase 4 (citation curation) pairs naturally with Phase 2 (url_content) since
both improve the quality of material flowing into the report. Phase 3 becomes
a safety net once Phase 5 prevents the claims from being written.

## Deferred (with rationale)

- **Search quality** — fine when engines work; not a real problem to solve.
- **Model output format robustness** — no evidence of a problem in the
  examined runs.
- **Fuzzy fact_id matching** — no evidence of mismatches in examined runs.
- **Retry context improvements** (including unverified-with-claims facts) —
  secondary to the core quality issues.

## Files touched

- `backend/moira/workflow/nodes/research_review.py`
- `backend/moira/workflow/nodes/research.py`
- `backend/moira/workflow/nodes/report_generation.py`
- `backend/moira/workflow/nodes/synthesis.py`
- `backend/moira/resources/prompts.md`
- `backend/moira/models/knowledge.py` (add `corrected` field to Fact)
- `backend/tests/test_research_review_node.py`
- `backend/tests/test_research_internals.py`
- `backend/tests/test_report_generation.py`
