# Verification outcome analysis

1. The draft is correct and acceptable as is. 

Outcome: generate report

2. The draft has some unsupported claims but is acceptable as is

Outcome: generate report

Guidance: note which claims are supported and which are not. 

3. The draft has some unsupported claims but the report will be fine if they are removed, because the supported claims adequately answer the question

Outcome: generate report

Guidance: remove unsupported claims

4. The draft has unsupported claims, and doesn't adequately answer the question without them

Outcome: return to planning (retry_plan)

Guidance: find a different approach to answering the question

5. The draft lacks claims altogether, as the research step couldn't answer the question.

Outcome: generate report

Guidance: Note inability to answer question

6. The draft is factually wrong — not just unsupported claims, but actively incorrect claims that contradict known evidence. This is worse than "unsupported" and should be treated differently.

Outcome: return to planning (retry_plan)

Guidance: Note that claims are contradicted by known evidence, and cite the evidence.

7. The draft answers a different question than what was asked — it's internally coherent but went off-topic. The verification step currently has no way to check relevance to the original question.

Outcome: retry draft (retry_draft) — findings are sufficient, re-synthesize only

Guidance: Note that the draft content is off topic

8. The draft is internally contradictory — it makes claims that conflict with each other. Currently the verification prompt asks about contradictions "with the evidence," but not contradictions within the draft itself.

Outcome: retry draft (retry_draft) — findings are sufficient, re-synthesize only

Guidance: Note that the draft is self-contradictory.

9. The draft is too shallow or incomplete — it technically answers the question but at such a high level that it's not useful. Not the same as #5 (no claims at all) — this has claims but they're trivially obvious or barely scratch the surface.

Outcome: return to planning (retry_plan)

Guidance: Note that the draft is too trivial to accept

10. The model didn't produce a draft at all — the draft synthesis step returned empty or garbage (e.g., the model output its thinking trace instead of content). This is distinct from #5 and is more of a technical failure than a research failure.

Outcome: stop and exit process with explanatory failure message

Guidance: none

We can't produce guidance without an analysis of the technical failure, which will be difficult to automate.

11. The verification response itself was unparseable — which we now handle as a failure, but it's worth calling out as a distinct case: the verification step can't assess the draft because the verifier model failed.

Outcome: stop and exit process with explanatory failure message

Guidance: none


# Grouped outcome guidance

## Generate report if:

1. The draft is correct and acceptable as is. 
2. The draft has some unsupported claims but is acceptable as is
3. The draft has some unsupported claims but the report will be fine if they are removed, because the supported claims adequately answer the question
5. The draft lacks claims altogether, as the research step couldn't answer the question.

Guidance: note which claims are supported and which are not.  If no claims, note that we could not answer the question.


## Repeat planning step if (retry_plan):

4. The draft has unsupported claims, and doesn't adequately answer the question without them
6. The draft is factually wrong — not just unsupported claims, but actively incorrect claims that contradict known evidence. This is worse than "unsupported" and should be treated differently.
9. The draft is too shallow or incomplete — it technically answers the question but at such a high level that it's not useful. Not the same as #5 (no claims at all) — this has claims but they're trivially obvious or barely scratch the surface.

Guidance: note the deficiencies in the previous report and ask the planner to find a different approach.

## Retry draft synthesis if (retry_draft):

7. The draft answers a different question than what was asked — it's internally coherent but went off-topic.
8. The draft is internally contradictory — it makes claims that conflict with each other.

These are synthesis-specific problems. The findings contain sufficient evidence but the draft misused it. Re-synthesizing with verification feedback is cheaper (7 budget points) than a full cycle (17).

Guidance: note the specific synthesis failure (off-topic or self-contradictory) so the synthesizer can correct it.

## Halt the process with an error if:

10. The model didn't produce a draft at all — the draft synthesis step returned empty or garbage (e.g., the model output its thinking trace instead of content). This is distinct from #5 and is more of a technical failure than a research failure.
11. The verification response itself was unparseable — which we now handle as a failure, but it's worth calling out as a distinct case: the verification step can't assess the draft because the verifier model failed.

Guidance: Exit with error
