"""Planning node: designs a cost-aware tool call plan.

Receives unknown facts and candidate tools with costs. Produces a
ToolCallPlan that fits within the remaining budget. Uses the intelligence
model.
"""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import ResearchState, ToolCallPlan
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
                prior_citations=_format_prior_citations(knowledge.get("citations", [])),
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
    tool_call_plan: list[ToolCallPlan] = []
    for call in parsed.get("calls", []):
        if isinstance(call, dict) and call.get("tool"):
            tool_costs = es.get("tool_costs", {})
            cost = tool_costs.get(call["tool"], 1.0)
            tool_call_plan.append(
                ToolCallPlan(
                    tool=call["tool"],
                    args=call.get("args", {}),
                    target_fact_ids=call.get("target_fact_ids", []),
                    cost=cost,
                )
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
    logger.info("PLANNING Complete (%d planned calls)", len(tool_call_plan))

    return {
        "execution_state": {
            **es,
            "tool_call_plan": tool_call_plan,
            "budget_remaining": new_budget,
            # Entry-count semantics: increment on every planning entry so
            # the router can limit research retries.  First entry: 0→1.
            "research_retry_count": research_retry_count + 1,
        },
    }
