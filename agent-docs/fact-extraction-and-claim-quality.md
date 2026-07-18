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

### Phase 2: url_content usage guidance

**Goal:** Guide the model to fetch full page content for promising sources
rather than relying solely on short snippets.

**Files:**
- `backend/moira/resources/prompts.md` (`research.system`,
  `research.system_native_tools`, `planning.user` or `planning.system`)

**Changes:**

1. **Add url_content guidance to the research prompts.**
   Instruct the model that web_search returns short snippets, and for sources
   that look promising but incomplete, it should use url_content to fetch the
   full page. Frame url_content as the natural second step after finding a
   relevant source:

   ```
   ## Using url_content

   web_search returns short snippets (a few sentences). When a search result
   looks directly relevant to a fact you need but the snippet doesn't contain
   the specific information, use url_content to fetch the full page content.
   This is especially valuable for:
   - Academic or technical content where the answer is in the body, not the
     meta description
   - Sources that mention your subject but where the snippet cuts off before
     the relevant detail
   - Any result where you need a precise number, date, or definition

   Do not fetch every URL — focus on sources that directly address an unknown
   fact but where the snippet is insufficient.
   ```

2. **Reinforce in the planning prompt.**
   The tool_call_plan is where the model decides its research strategy. Guide
   planning to pair web_search queries with url_content fetches for promising
   results, rather than treating web_search as the complete research step.

**Open questions to resolve during implementation:**

- **When to fetch?** After seeing promising snippets? Only when snippets are
  insufficient? For all top results? Start with "when snippets look relevant
  but incomplete" and refine based on observed behavior.

- **Interaction with call limits.** url_content has per-run=15, per-step=8.
  If the model fetches aggressively, it could hit these limits. Monitor and
  adjust if needed.

- **Should planning identify URLs to fetch?** Planning runs before research,
  so it doesn't know which URLs will appear in results. url_content decisions
  are better made dynamically during research. Planning can set the strategy
  ("fetch promising sources") but not specific URLs.

- **What makes a source "strong"?** Position in search results, domain
  authority, snippet relevance. The model decides this — prompting guides the
  judgment.

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

**Questions to watch:**
- `water-blood-pressure`, `jazz-trumpeters` — zero-fact failures. Watch
  whether url_content guidance helps the model find better material.
- `future-nostalgia` — direct measure of Phase 3 impact (process-metadata
  leak).
- `telescope-mount-cost` — had 5 unverified facts, none passed review. Watch
  whether claim correction (Phase 1) helps borderline contradictions.
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

| Day | Focus | Phase |
|---|---|---|
| 1 | Claim correction in research_review | Phase 1 |
| 2 | url_content prompt guidance + citation curation | Phases 2, 4 |
| 3 | Reviewer-based process-metadata detection + testing | Phase 3 |
| 4 | Eval run, iterate on prompts, polish | All |
| 5 | Demo prep, rehearsal, narrative | — |

Phase 1 is the highest-impact change. If only one phase lands, it should be
Phase 1 — it directly addresses the "1996 or 1997" contradiction problem and
reduces unnecessary retries. Phase 4 (citation curation) pairs naturally with
Phase 2 (url_content) since both improve the quality of material flowing into
the report.

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
