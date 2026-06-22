# Research Loop Critique

## Overall assessment

The overhaul is directionally strong. It addresses real failure modes in the current
system: poor tool selection, excessive generic search, weak grounding of conclusions,
and limited inspectability into how an answer was produced.

The strongest idea in the proposal is the separation of the workflow into distinct
phases for fact discovery, synthesis, verification, and report generation. That is a
much better fit for a research agent than letting one loop do all of those jobs at
once.

The main concern is that the implementation plan turns a good architectural direction
into a high-risk rewrite. It replaces the workflow state model, graph, prompts,
persistence shape, APIs, frontend types, and tests before the plan proves that the new
approach actually improves tool choice or reduces search-heavy behavior. The proposal is
likely correct in spirit, but too much of the system is being replaced at once.

## Strengths

### 1. Better separation of responsibilities

The proposed workflow is much better aligned with the actual problem than the current
pipeline. Decomposition, tool identification, planning, research, synthesis,
verification, and report generation each have a clearer responsibility. That should
reduce the current tendency to mix evidence gathering with reasoning and then mix both
with prose generation.

This is especially important for the stated failure mode where the model combines
correct individual facts into incorrect conclusions. Separating fact discovery from
conclusion generation is the right response.

### 2. The knowledge model is a strong product and debugging primitive

Treating the evolving research state as a structured artifact is a strong idea. A
workflow that carries explicit facts, conclusions, citations, and verification results
should be easier to debug, easier to inspect in the UI, and easier to test than a
workflow that mostly stores step outputs and narrative reports.

This also matches the broader MOiRA goal of transparency and inspectability.

### 3. Tool-cost awareness directly targets the current search abuse problem

Assigning higher cost to `web_search`, lower cost to specialized tools, and making that
visible to planning is one of the most concrete parts of the proposal. This is well
aimed at the current failure mode where the agent repeatedly falls back to generic
search and burns budget without improving quality.

### 4. Claim-level verification is a substantial improvement

Moving from report-level critique to fact-level and conclusion-level verification is a
real architectural upgrade. It should produce clearer failure signals, support tighter
retry routing, and make it easier to explain why an answer is weak.

### 5. Tool description enrichment is well motivated

Rewriting tool descriptions around the questions a tool can answer, rather than just
its interface, is sensible. If tool discovery is semantic, this is one of the most
plausible ways to improve retrieval quality without making the whole system domain
specific.

### 6. The implementation plan is concrete

The plan goes beyond high-level workflow ideas. It specifies schema shapes, state,
graph routing, storage, streaming messages, frontend types, testing, and phased
delivery. That level of specificity is useful and makes the proposal actionable.

## Weaknesses and risks

### 1. The plan is too much of a rewrite at once

This is the biggest issue.

The implementation plan replaces the core state model, graph structure, prompts,
workflow storage, streaming payloads, frontend types, and tests in one initiative. It
also proposes dropping and recreating workflow tables and removing the old state model.

That makes the migration high risk in exactly the part of the system that is already
hard to reason about. It also removes the current baseline before the new loop is
proven against real evaluations.

The architecture would be safer if it were introduced behind a feature flag or a
parallel graph path so the current system remains available as a comparison point.

### 2. The plan does not define success in terms of the stated problems

The problem statement is explicit: the model overuses `web_search`, fails to prefer
specialized tools, makes too many calls per step, and may not be producing better
reports.

The implementation plan does not translate those concerns into explicit evaluation
criteria. Most exit criteria are about code shape and system operability, such as tests
passing, prompts loading, or the graph running end to end.

That is not enough. The plan needs an evaluation harness with concrete metrics such as:

- average `web_search` calls per run
- percentage of runs that use at least one domain-specific tool when available
- citation-backed fact coverage
- contradiction rate in verification
- answer quality on a fixed test set

Without this, it will be hard to know whether the overhaul solved the actual problem or
just made the workflow more elaborate.

### 3. The tool-selection problem is softened rather than solved

The concept document highlights deterministic routing from fact type to tool as a key
recommendation. The implementation plan does not really implement that.

Instead, it still depends on a chain of semantic retrieval, LLM planning, and a
research step that is allowed to deviate from the plan and add extra rounds. That may
still be better than the current system, but it is not deterministic routing. It is a
more structured probabilistic workflow.

That matters because the current core problem is not lack of workflow stages. It is
that the model keeps selecting the wrong tool class. If the new system still lets the
model decide too freely when to use generic search, it can recreate the same failure in
new form.

### 4. The knowledge model is overloaded with execution state

The concept document makes a useful distinction between the knowledge artifact and step
operations such as prompts, tool calls, and other workflow details.

The implementation plan blurs that boundary by putting `candidate_tools`,
`tool_call_plan`, retry counters, budget fields, `error`, and even the final `report`
into the single knowledge model that is also the graph state.

That makes the state model less conceptually clean and more operationally heavy. It
will increase prompt size, complicate retry behavior, and make it harder to separate
knowledge persistence from orchestration mechanics.

This would be cleaner if there were two related but distinct structures:

- a knowledge artifact containing question, facts, conclusions, citations, and
  verification history
- an execution state containing budgets, tool candidates, current plan, retry counts,
  routing metadata, and errors

### 5. Verification is asked to do too much in one node

The concept document suggests verifying facts and verifying reasoning as separate
activities. The implementation plan combines fact checking, conclusion checking, goal
assessment, retry recommendations, and budget-sensitive routing into one verification
stage.

That concentrates too much responsibility in the most important quality gate in the
system. When verification fails, it will be harder to tell whether the problem came
from unsupported facts, flawed reasoning, weak goal assessment, or overly aggressive
routing.

Even if implemented in one file, the logic would be safer if fact verification and
reasoning verification were treated as separate passes.

### 6. Reference semantics are more brittle than they should be

The plan introduces IDs for facts, conclusions, and citations, which is a good move.
But several prompt designs still rely on natural-language descriptions rather than IDs
for joining outputs back to state.

That is risky. Fact descriptions can be similar, wording can drift across retries, and
LLM output can paraphrase. If the system already has stable IDs, downstream prompts
should use them directly instead of matching on text wherever possible.

### 7. The budget model contains internal inconsistencies

The node-level cost tables and the later budget section do not agree on the step costs
for several nodes. That is not a minor documentation issue, because planning and retry
routing depend on those numbers.

If the budget math is ambiguous in the design doc, it is likely to be ambiguous in the
implementation as well.

### 8. Acceptance criteria are underdefined

The decomposition guidance says it is better to over-specify facts than to miss
important ones. At the same time, the verification and report-generation sections imply
that a successful run is one where the answer is verified and the goal is met.

What is not clear is whether every decomposed fact must be resolved and verified, or
whether only the material facts needed to satisfy the user goal must be verified.

That distinction matters. If every decomposed fact is treated as required, the system
will encourage unnecessary retries and budget burn. If only goal-critical facts are
required, then the workflow needs a clear definition of materiality.

### 9. The plan still assumes the decomposition step can be trusted early

The proposed workflow makes decomposition foundational. It seeds the facts, guides tool
identification, shapes planning, and constrains later reasoning. But the concept doc
already raises doubt about whether the relevant model is strong enough for this step.

That is a serious dependency. If decomposition omits key facts or decomposes the
question badly, the rest of the workflow may become a well-structured march in the
wrong direction.

This step needs early evaluation, not just implementation.

## Internal inconsistencies

### 1. Deterministic routing in the concept doc vs probabilistic routing in the plan

The concept document emphasizes deterministic routing from fact type to tool. The plan
implements semantic retrieval plus planner judgment plus research-time deviation. Those
are different strategies.

If deterministic routing is still the desired direction, the plan should say where hard
rules will exist. If not, the concept document should be revised to reflect the actual
design.

### 2. Separate verification activities in the concept doc vs one verification node in the plan

The concept document describes verifying facts and verifying logic as distinct steps.
The implementation plan merges them. That is a real design change and should be made
explicit.

### 3. Distinct knowledge artifact in the concept doc vs unified graph state in the plan

The concept document treats the knowledge model as distinct from operational step
details. The implementation plan turns it into the single state object for the graph
and mixes in orchestration fields. That weakens the conceptual clarity of the original
proposal.

### 4. Parallel validation language vs immediate cutover behavior

The phased implementation section says the new graph should be built alongside the old
one and validated before cutover. But the concrete Phase A work removes old state and
test structures immediately. Those two rollout stories do not match.

## Recommended changes before implementation

### 1. Add a Phase 0 evaluation harness

Before rewriting the graph, define a fixed set of test questions and baseline metrics.
Measure current behavior, then require the new workflow to beat it on the metrics that
motivated the overhaul.

#### Benchmark sheet: Tyranitar Gen9 OU canary

Use the following live question as the primary canary benchmark for the overhaul:

`What Pokemon synergize well with Tyranitar in Gen9 OU? Please pay special attention to verifying the type strengths and weaknesses, typical abilities, typical moves, and OU eligibility.`

This is a strong benchmark question because it stresses the exact failure modes the
overhaul is trying to fix:

- fact-rich retrieval across multiple fact classes
- type-matchup reasoning that is easy to state incorrectly
- ability facts that are easy to partially remember and misdescribe
- move facts where "can learn" and "typically runs" are not the same claim
- OU eligibility and OU relevance, which should not be conflated
- synthesis risk, where individually correct facts can still produce bad advice

The benchmark should not be scored by whether the system names one exact set of partner
Pokemon. It should be scored by whether it gathers the right facts, uses the right tool
classes, keeps those facts correct, and avoids unsupported team-building conclusions.

##### Run conditions

For old-vs-new comparisons, keep these conditions stable:

- same intelligence model
- same task model
- same enabled tool catalog
- same budget
- same credentials and provider configuration
- same prompt/config environment as much as possible

Run the old system once or a very small number of times, then save the trace. Do the
same for the new system. Avoid repeated live reruns because the search providers are a
known constraint.

##### Artifacts to capture for each run

Capture and save these outputs for both baseline and overhaul runs:

- full tool-call trace
- total tool calls
- total `web_search` calls
- candidate tools or tool-ranking output, if available
- final answer
- citations
- extracted or implied facts used in the answer
- verification output
- final report status and budget consumption

##### Scoring rubric

Score each category `0`, `1`, or `2`:

- `0`: clear failure
- `1`: mixed or partially acceptable
- `2`: strong

Total possible score: `16`.

Use these categories:

1. **Tool choice**
   Pass behavior: Pokemon-specific tools are used before generic search for species,
   abilities, moves, and legality facts.
   Fail behavior: `web_search` is used first for facts that specialized Pokemon tools
   should answer.

2. **Search discipline**
   Pass behavior: generic search is limited, clearly lower than the baseline, or used
   only for facts that structured tools do not cover well.
   Fail behavior: the workflow still burns most of its effort on generic search.

3. **Type correctness**
   Pass behavior: the answer contains no material mistakes in type matchups used in its
   reasoning.
   Fail behavior: any recommendation depends on an incorrect type strength, weakness,
   resistance, or immunity claim.

4. **Ability correctness**
   Pass behavior: abilities are correctly attributed and correctly described.
   Fail behavior: the answer confuses what an ability does, attributes the wrong
   ability, or treats a niche ability as the typical one without support.

5. **Typical-move discipline**
   Pass behavior: the answer distinguishes between raw learnset availability and moves
   that are actually typical in Gen9 OU play.
   Fail behavior: the answer overclaims common moves from species data alone or blurs
   "possible move" into "standard move."

6. **OU legality and metagame status**
   Pass behavior: the system separates "legal in Gen9 OU" from "credible or common in
   Gen9 OU." 
   Fail behavior: legality and serious metagame relevance are treated as the same
   thing.

7. **Synthesis discipline**
   Pass behavior: synergy claims stay modest and fact-backed.
   Fail behavior: the answer jumps from isolated correct facts to broad team-building
   advice without enough support.

8. **Verification quality**
   Pass behavior: verification flags weak support, contradictions, or overreach.
   Fail behavior: verification rubber-stamps a draft with shaky type, ability, move,
   legality, or synthesis claims.

##### Hard-fail categories

Even with a decent total score, a run should not count as a success if it fails any of
these categories:

- type correctness
- ability correctness
- OU legality and metagame status
- verification quality

For this benchmark, a wrong reason is more serious than a missed recommendation.

##### What counts as improvement

The overhaul should count as better on this canary if it does most or all of the
following relative to the old system:

- uses specialized Pokemon sources earlier
- makes fewer `web_search` calls
- makes zero material type or ability errors
- treats "typical moves" with more evidence and caution
- clearly separates legality from metagame relevance
- makes fewer unsupported synthesis leaps
- produces a verifier that pushes back on weak claims instead of endorsing them

##### Suggested companion offline fixtures

Because live evaluation is expensive and provider-limited, this canary should be paired
with a few offline regression fixtures:

1. **Verification stress fixture**
   A short Tyranitar-partner draft with planted mistakes in type, ability, typical
   moves, legality, and synergy reasoning. The verifier should catch them.

2. **Tool-routing fixture**
   The same question or its decomposed facts, run against a fixed tool catalog. The
   system should rank Pokemon-specific tools ahead of `web_search` for structured facts.

3. **Synthesis trap fixture**
   A fixed fact bundle containing individually true facts that tempt an unjustified
   recommendation. The system should qualify the claim or refuse to overstate it.


Together, these fixtures let the project iterate mostly offline while still preserving
one live benchmark that reflects the real failure mode.

### 2. Keep the old graph alive during early rollout

Implement the new graph behind a feature flag, alternate run type, or shadow mode. That
will let the project compare outputs directly and reduce migration risk.

Solution:

Keep a separate copy of moira.db with the current implementation's data, and 
keep a separate checkout of the git repo with that moira.db copy, where a branch can 
have eval harness commits on it, but not the other changes. 

Eval can be done manually, following the recommendation #1 above.

### 3. Split knowledge state from execution state

Keep the knowledge artifact small and semantically clean. Move planning and routing
machinery into a separate execution structure.

Solution:
To do this, take the existing state and separate the knowledge and the execution state
into two sub objects, like this: 

{
  "knowledge" : {
     "question": ...,
    "user_goal": ...,
    "topic": ...,
    "entities": [...],
    "concepts": [...],
    "facts": [...],
    "conclusions": [...],
    "citations": [...],
    "verification_history": [...],
  },
  "execution_state": {
    "candidate_tools": [...],
    "tool_call_plan": [...],
    "budget_remaining": ...,
    "budget_limit": ...,
    "step_costs": ...,
    "tool_costs": ...,
    "total_tool_cost_consumed": ...,
    "error": ...,
    "synthesis_retry_count": ...,
    "generation_path": ...,
  }
}

if these aren't the exact correct names for the schema elements, then
extrapolate this general idea to correct them.

### 4. Use IDs end to end

If facts and conclusions have stable IDs, every downstream node should reference those
IDs directly. Avoid description-based joins unless there is no better option.

Solution: do as is described here.

### 5. Add hard guardrails on generic search

Cost pressure alone may not be enough. The system likely needs explicit policy such as:

- require a specialized-tool attempt before `web_search` when one exists
- cap `web_search` calls per node or per run
- stop retrying generic search after repeated empty results
- give verification its own explicit search cap

Solution 1:
Let's include an optional call_limit_per_run in the tool metadata.
If present, the workflow should not allow more tool calls than the call limit, 
and the LLM should be presented with a message that the call limit for that tool
has been reached. 

Solution 2:
If there's a reasonable way to keep track of tool state between invocations,
then have the web_search tool stop attempting and return a "too many errors in previous calls"
message instead.

### 6. Define what counts as enough evidence

The workflow should explicitly distinguish between:

- facts needed to fully characterize the domain
- facts materially required to answer the user's question

That will make acceptance and retry rules more stable.

Solution:
We only care about "facts materially required to answer the user's question",
so let's be sure that prompts are updated to match this concern. 

### 7. Land the lowest-risk improvements first

The most promising incremental sequence is:

1. add tool invocation cost tracking and search guardrails
2. improve tool descriptions for discovery
3. add claim-level fact recording and verification
4. introduce the fuller knowledge model and graph rewrite only after the first three
   changes are measured

## Conclusion

This is a good architectural direction with clear insight into the current system's
failure modes. The proposal is strongest where it separates evidence from reasoning,
introduces structured state, and treats verification as a first-class concern.

The main weakness is not the core idea. It is the size and coupling of the planned
implementation. The proposal should be made more incremental, more measurable, and more
strict about tool-routing control before it replaces the current system wholesale.

# Follow-up Findings

1. retry_research / retry_synthesis budgeting still uses the old step costs, so the routing logic can underreserve budget and allow retries the new budget model should reject. In the verification section, the retry-cost example still says research(2) + synthesis(3) + verification(3) = 11 and synthesis(3) + verification(3) = 6 (agent-docs/research-loop-overhaul-plan.md:419-423), but the current node specs and budget table define those as research=10, synthesis=5, verification=8 (agent-docs/research-loop-overhaul-plan.md:316,351,379,465-499). This is the highest-risk inconsistency because the retry edge logic depends on it.
2. The response says “use IDs end to end” was adopted, but the planning and research prompt drafts still rely on description-based matching instead of IDs. ToolCallPlan is defined with target_fact_ids (agent-docs/research-loop-overhaul-plan.md:51-56), but the planner is still asked to emit target_fact_descriptions (agent-docs/research-loop-overhaul-plan.md:1009-1014), and the research prompt still says discovered facts should match an existing fact_needed description (agent-docs/research-loop-overhaul-plan.md:1107-1109). The detail examples also still use numeric placeholders instead of string IDs (agent-docs/research-loop-overhaul-plan.md:838-840,858-859). That leaves the same fuzzy join problem the critique called out, especially on retries when wording drifts.
3. The updated “material facts only” rule is good, but the plan still has conflicting success semantics. Verification now says the goal is met when materially required facts are verified and support the answer, and that not every decomposed fact must be resolved (agent-docs/research-loop-overhaul-plan.md:1233-1240). But report_generation still describes the verified path as “all facts and conclusions verified” (agent-docs/research-loop-overhaul-plan.md:447-455). That mismatch will leak into implementation and UI logic unless you decide whether “verified path” means “answer sufficiently verified” or literally “every fact/conclusion in state verified.”
4. The backend/frontend ResearchReport schemas are still out of sync. The backend report type includes budget_consumed and generation_path but not tool_cost_consumed (agent-docs/research-loop-overhaul-plan.md:131-141), while the frontend ResearchReport requires tool_cost_consumed (agent-docs/research-loop-overhaul-plan.md:691-701). If implemented as written, either the backend payload or the frontend typing will be wrong.
5. The call-limit change is only partially reflected in the planner design. Phase C says the planner should include call limits in its prompt (agent-docs/research-loop-overhaul-plan.md:1752-1757), but the actual planning prompt still presents tools as only “name, description, cost per call” (agent-docs/research-loop-overhaul-plan.md:1029-1034). That means the planner cannot reason about one of the main new guardrails, even though the execution layer will enforce it later.
Open questions / assumptions
- I’m assuming the intent is that workflow_runs.knowledge_snapshot now stores the full nested LangGraph state, not just the knowledge sub-object. The document still calls it the “full knowledge model JSON” in a few places (agent-docs/research-loop-overhaul-plan.md:540,605-607,740), which is slightly ambiguous now that execution_state exists.