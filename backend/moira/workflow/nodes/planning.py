"""Planning node: designs cost-aware evidence requests.

Receives unknown facts and candidate tools with costs. Produces
EvidenceRequests that describe what evidence is needed and which tools
might provide it, leaving query formulation to the research step.
Uses the intelligence model.
"""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import EvidenceRequest, ResearchState
from moira.prompts import render_prompt
from moira.workflow.budget import can_execute, deduct_cost
from moira.workflow.nodes._helpers import (
    _format_established_facts,
    _format_prior_citations,
    _format_prior_conclusions,
    _now,
    _parse_json_object,
    _response_meta,
)
from moira.workflow.nodes._helpers_deps import (
    _check_stop,
    _resolve_intelligence,
)

logger = logging.getLogger(__name__)

NODE_NAME = "planning"


def _split_bundled_requests(requests: list[EvidenceRequest], facts: list) -> list[EvidenceRequest]:
    """Split multi-fact evidence requests into one request per fact.

    Bundled requests (several target_fact_ids behind one evidence_needed)
    produced vague, hard-to-query evidence descriptions in the 08-24 eval:
    one description serving several facts dissolves into generality. A
    per-fact request keeps each description actionable. The planning prompt
    asks for one fact per request; this parse-time split is the mechanical
    backstop, not a rewrite of model output.

    Also drops target IDs that reference nonexistent facts — a request
    aimed at a dangling ID can never be satisfied or matched. A request
    whose targets are all unknown is dropped entirely (with a log).

    Split requests copy candidate_tools and fallback from the original.
    evidence_needed becomes the target fact's own fact_needed (the precise
    description of what must be established), falling back to the original
    bundle description when the fact has none.
    """
    fact_needed_by_id = {f["id"]: (f.get("fact_needed") or "").strip() for f in facts}
    result: list[EvidenceRequest] = []
    for req in requests:
        target_ids = [t for t in req.get("target_fact_ids", []) if isinstance(t, str)]
        known_ids = []
        for tid in target_ids:
            if tid in fact_needed_by_id:
                known_ids.append(tid)
            else:
                logger.warning(
                    "PLANNING: evidence request references unknown fact %s — dropping that target",
                    tid,
                )
        if not known_ids:
            logger.warning(
                "PLANNING: dropping evidence request with no valid target facts: %s",
                req.get("evidence_needed", "")[:80],
            )
            continue
        if len(known_ids) == 1:
            result.append({**req, "target_fact_ids": known_ids})
            continue
        for tid in known_ids:
            result.append(
                EvidenceRequest(
                    target_fact_ids=[tid],
                    evidence_needed=fact_needed_by_id[tid] or req.get("evidence_needed", ""),
                    candidate_tools=req.get("candidate_tools", []),
                    fallback=req.get("fallback", True),
                )
            )
    return result


def _assign_request_ids(requests: list[EvidenceRequest]) -> list[EvidenceRequest]:
    """Assign sequential IDs (req0001, req0002, ...) to evidence requests.

    IDs are mechanical pipeline metadata: the research prompt shows them,
    the model echoes them on tool calls, and ``_process_execution_results``
    attributes results to requests. Always assigns fresh IDs — requests
    are regenerated on every planning entry, so stale IDs would be
    meaningless. Attempt history is re-linked separately by
    ``_carry_over_attempts``.
    """
    return [EvidenceRequest(**{**req, "id": f"req{i + 1:04d}"}) for i, req in enumerate(requests)]


def _carry_over_attempts(
    old_attempts: dict[str, list[dict]],
    new_requests: list[EvidenceRequest],
    old_requests: list[EvidenceRequest],
) -> dict[str, list[dict]]:
    """Re-link attempt history from the previous plan to regenerated requests.

    Retries re-enter through planning, which regenerates evidence
    requests with fresh IDs. The attempt ledger keyed by those IDs would
    then be orphaned. Re-link by target-fact overlap: attempts recorded
    against an old request attach to a new request when they share at
    least one target fact. This preserves "what was tried for this fact"
    across plan regeneration, feeding the retry prompt's failure summary
    and suppressing repeated queries.

    Entries are deduplicated by (tool, query) so an old request and a
    new request sharing facts don't double-count the same attempt.
    """
    if not old_attempts:
        return {}
    old_request_by_id = {r.get("id"): r for r in old_requests if r.get("id")}
    carried: dict[str, list[dict]] = {}
    seen: dict[str, set[tuple[str, str]]] = {}
    for new_req in new_requests:
        new_id = new_req.get("id")
        if not new_id:
            continue
        new_targets = set(new_req.get("target_fact_ids", []))
        if not new_targets:
            continue
        for old_id, attempts in old_attempts.items():
            old_req = old_request_by_id.get(old_id)
            if old_req is None or not (new_targets & set(old_req.get("target_fact_ids", []))):
                continue
            for attempt in attempts:
                key = (attempt.get("tool", ""), attempt.get("query", ""))
                dupes = seen.setdefault(new_id, set())
                if key in dupes:
                    continue
                dupes.add(key)
                carried.setdefault(new_id, []).append(attempt)
    return carried


def _format_unknown_facts(facts: list) -> str:
    """Format non-verified facts for the planning prompt.

    Includes 'unknown', 'unverified', and 'contradicted' facts — all
    represent gaps that planning should address.  'verified' facts are
    excluded since they are already resolved.
    """
    lines = []
    for f in facts:
        if f.get("status") != "verified":
            lines.append(f"{f['id']} | {f['subject']} | {f['fact_needed']}")
    return "\n".join(lines)


def _format_tools_with_costs_and_limits(
    tools: list,
    tool_costs: dict[str, float],
    call_counts: dict[str, int],
    tool_call_limits: dict[str, int],
    tool_call_step_limits: dict[str, int] | None = None,
    step_baseline: dict[str, int] | None = None,
) -> str:
    lines = []
    for t in tools:
        cost = tool_costs.get(t.name, 1.0)
        used = call_counts.get(t.name, 0)
        limit = tool_call_limits.get(t.name, 0)
        remaining = max(limit - used, 0) if limit > 0 else "unlimited"

        # Per-step remaining: calls allowed in the current research step
        step_str = ""
        if tool_call_step_limits and step_baseline:
            step_limit = tool_call_step_limits.get(t.name, 0)
            if step_limit > 0:
                step_used = used - step_baseline.get(t.name, 0)
                step_remaining = max(step_limit - step_used, 0)
                step_str = f" (step: {step_remaining})"

        desc = t.description[:120].replace("\n", " ").strip()

        # Include parameter names so the model uses correct arg keys
        params_str = ""
        schema = getattr(t, "argument_schema", None)
        if schema and "properties" in schema:
            required = set(schema.get("required", []))
            props = schema["properties"]
            param_parts = []
            for pname, pdef in props.items():
                ptype = pdef.get("type", "any")
                req = "required" if pname in required else "optional"
                param_parts.append(f"{pname} ({ptype}, {req})")
            if param_parts:
                params_str = f" | params: {', '.join(param_parts)}"

        lines.append(
            f"{t.name} | {desc}{params_str}"
            f" | cost per call: {cost}"
            f" | calls remaining: {remaining}{step_str}"
        )
    return "\n".join(lines)


async def planning(state: ResearchState, config: RunnableConfig) -> dict:
    """Plan tool calls to resolve unknown facts within budget."""
    _check_stop(NODE_NAME, config)
    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": NODE_NAME, "timestamp": _now()}})

    es = state["execution_state"]
    knowledge = state["knowledge"]
    if not can_execute(es["step_costs"], NODE_NAME, es["budget_remaining"]):
        writer(
            {
                "event": "node_end",
                "payload": {
                    "node": NODE_NAME,
                    "budget_remaining": es["budget_remaining"],
                },
            }
        )
        return {
            "execution_state": {
                **es,
                "error": f"Insufficient budget for {NODE_NAME}",
            },
        }

    # Compute reserved budget for remaining pipeline steps after research
    step_costs = es["step_costs"]
    reserved_budget = (
        step_costs.get("synthesis", 5)
        + step_costs.get("research_review", 3)
        + step_costs.get("evaluation", 5)
        + step_costs.get("report_generation", 3)
    )
    available_for_tools = max(es["budget_remaining"] - reserved_budget, 0)

    user_prompt = render_prompt(
        "planning.user",
        user_goal=knowledge.get("user_goal", knowledge["question"]),
        topic=knowledge.get("topic", ""),
        entities=", ".join(knowledge.get("entities", [])),
        concepts=", ".join(knowledge.get("concepts", [])),
        unknown_facts=_format_unknown_facts(knowledge["facts"]),
        tool_descriptions_with_costs_and_limits=_format_tools_with_costs_and_limits(
            es.get("candidate_tools", []),
            es.get("tool_costs", {}),
            es.get("tool_call_counts", {}),
            es.get("tool_call_limits", {}),
            es.get("tool_call_step_limits", {}),
            # At planning time the upcoming research step hasn't started,
            # so the baseline = current counts (step_used = 0).
            es.get("tool_call_counts", {}),
        ),
        budget_remaining=es["budget_remaining"],
        reserved_budget=reserved_budget,
        available_for_tools=available_for_tools,
    )

    system_prompt = render_prompt("planning.system")

    # On retry from evaluation, append evaluation feedback
    research_retry_count = es.get("research_retry_count", 0)
    if research_retry_count > 0:
        evaluation_history = knowledge.get("evaluation_history", [])
        if evaluation_history and evaluation_history[-1].get("route") == "retry":
            last_eval = evaluation_history[-1]
            feedback = last_eval.get("goal_assessment", "")
            failed_conclusions = [
                f"{r.get('conclusion_id', '')} | {r.get('result', '')} | {r.get('reason', '')}"
                for r in last_eval.get("conclusion_results", [])
                if r.get("result") != "verified"
            ]
            system_prompt += "\n\n" + render_prompt(
                "planning.system_retry_evaluation",
                evaluation_feedback=feedback,
                failed_conclusions="\n".join(failed_conclusions)
                if failed_conclusions
                else "(none)",
            )

    # On retry from research_review, append review feedback so planning
    # knows what gaps remain and can formulate better queries / choose
    # different tools for the remaining unknown facts.  Also include
    # context from the previous research pass so the planner can avoid
    # re-searching what is already established.
    review_count = es.get("review_count", 0)
    if review_count > 0:
        review_history = knowledge.get("review_history", [])
        if review_history and review_history[-1].get("route") == "retry":
            last_review = review_history[-1]
            system_prompt += "\n\n" + render_prompt(
                "planning.system_retry_review",
                coverage_assessment=last_review.get("coverage_assessment", ""),
                missing_areas="\n".join(
                    f"- {area}" for area in last_review.get("missing_areas", [])
                ),
            )
            system_prompt += "\n\n" + render_prompt(
                "planning.system_retry_context",
                established_facts=_format_established_facts(knowledge["facts"]),
                prior_conclusions=_format_prior_conclusions(knowledge.get("conclusions", [])),
                prior_citations=_format_prior_citations(
                    knowledge.get("citations", []), knowledge.get("facts", [])
                ),
            )

    # Prior conversation context (multi-turn)
    prior_report = es.get("prior_report")
    if prior_report:
        prior_q = es.get("prior_question", "")
        system_prompt += "\n\n" + render_prompt(
            "planning.system_prior_report",
            prior_question=prior_q,
            prior_report_answer=prior_report,
        )

    earlier_turns = es.get("earlier_turns")
    if earlier_turns:
        system_prompt += "\n\n" + render_prompt(
            "planning.system_earlier_turns",
            earlier_turns=earlier_turns,
        )

    resolved = await _resolve_intelligence(config)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    logger.info("PLANNING Start")
    response = await resolved.client.chat_completion(
        messages=messages,
        model=resolved.model_id,
        temperature=DEFAULT_TEMPERATURE,
    )

    raw = response.content or ""
    thinking = getattr(response, "thinking", "") or ""
    new_budget = deduct_cost(es["step_costs"], NODE_NAME, es["budget_remaining"])

    detail = {
        "prompt": user_prompt,
        "response": raw,
        "model": resolved.model_id,
    }
    if thinking:
        detail["thinking"] = thinking

    if not raw.strip():
        logger.error(
            "PLANNING: model returned empty content (thinking=%d chars)",
            len(thinking),
        )
        writer(
            {
                "event": "run_error",
                "payload": {
                    "error": f"Model returned empty content for {NODE_NAME}",
                    "budget_remaining": new_budget,
                    "detail": detail,
                    "purpose": NODE_NAME,
                    "model": resolved.model_id,
                    "call_count": 1,
                },
            }
        )
        raise RuntimeError(
            f"Model returned empty content for {NODE_NAME} (thinking={len(thinking)} chars)"
        )

    parsed = _parse_json_object(raw)
    raw_requests: list[EvidenceRequest] = []
    for req in parsed.get("evidence_requests", []):
        if isinstance(req, dict) and req.get("target_fact_ids"):
            raw_requests.append(
                EvidenceRequest(
                    target_fact_ids=req.get("target_fact_ids", []),
                    evidence_needed=req.get("evidence_needed", ""),
                    candidate_tools=req.get("candidate_tools", []),
                    fallback=req.get("fallback", True),
                )
            )
    # Mechanical backstop for the prompt's one-fact-per-request rule:
    # splits bundles, drops dangling fact references.
    evidence_requests = _split_bundled_requests(raw_requests, knowledge["facts"])
    # Fresh sequential IDs each planning entry; attempt history is
    # re-linked to the new IDs by target-fact overlap below.
    previous_requests = es.get("evidence_requests", [])
    previous_attempts = es.get("request_attempts", {})
    evidence_requests = _assign_request_ids(evidence_requests)
    request_attempts = _carry_over_attempts(
        previous_attempts, evidence_requests, previous_requests
    )

    detail["structured_output"] = parsed

    writer(
        {
            "event": "node_end",
            "payload": {
                "node": NODE_NAME,
                "budget_remaining": new_budget,
                "detail": detail,
                "purpose": NODE_NAME,
                "model": resolved.model_id,
                "call_count": 1,
                **_response_meta(response),
            },
        }
    )
    logger.info("PLANNING Complete (%d evidence requests)", len(evidence_requests))

    return {
        "execution_state": {
            **es,
            "evidence_requests": evidence_requests,
            "request_attempts": request_attempts,
            "budget_remaining": new_budget,
            # Entry-count semantics: increment on every planning entry so
            # the router can limit research retries.  First entry: 0→1.
            "research_retry_count": research_retry_count + 1,
        },
    }
