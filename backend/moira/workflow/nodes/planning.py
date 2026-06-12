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
from moira.prompts import get_prompt
from moira.workflow.budget import can_execute, deduct_cost
from moira.workflow.nodes._helpers import (
    _check_stop,
    _get_model,
    _now,
    _parse_json_object,
    _response_meta,
)

logger = logging.getLogger(__name__)

NODE_NAME = "planning"


def _format_unknown_facts(facts: list) -> str:
    lines = []
    for f in facts:
        if f.get("status") in ("unknown", "contradicted"):
            lines.append(f"{f['id']} | {f['subject']} | {f['fact_needed']}")
    return "\n".join(lines)


def _format_tools_with_costs_and_limits(
    tools: list, tool_costs: dict[str, float], call_counts: dict[str, int],
    tool_call_limits: dict[str, int],
) -> str:
    lines = []
    for t in tools:
        cost = tool_costs.get(t.name, 1.0)
        used = call_counts.get(t.name, 0)
        limit = tool_call_limits.get(t.name, 0)
        remaining = max(limit - used, 0) if limit > 0 else "unlimited"
        lines.append(
            f"- {t.name} | cost: {cost} | calls remaining: {remaining} | "
            f"{t.description[:120]}"
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
        writer({
            "event": "node_end",
            "payload": {
                "node": NODE_NAME,
                "budget_remaining": es["budget_remaining"],
            },
        })
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
        + step_costs.get("verification", 8)
        + step_costs.get("report_generation", 3)
    )
    available_for_tools = max(es["budget_remaining"] - reserved_budget, 0)

    user_prompt = get_prompt("planning.user").format(
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
        ),
        budget_remaining=es["budget_remaining"],
        reserved_budget=reserved_budget,
        available_for_tools=available_for_tools,
    )

    system_prompt = get_prompt("planning.system")
    # On retry, append retry appendix
    if es.get("research_retry_count", 0) > 0:
        verification_history = knowledge.get("verification_history", [])
        last_v = verification_history[-1] if verification_history else {}
        feedback = last_v.get("goal_assessment", "")
        unresolved = [
            f for f in knowledge["facts"]
            if f["status"] in ("unknown", "contradicted")
        ]
        system_prompt += "\n\n" + get_prompt("planning.system_retry").format(
            verification_feedback=feedback,
            unresolved_facts=_format_unknown_facts(unresolved),
            all_unknown_facts=_format_unknown_facts(knowledge["facts"]),
        )

    # Prior conversation context (multi-turn)
    prior_report = es.get("prior_report")
    if prior_report:
        prior_q = es.get("prior_question", "")
        system_prompt += "\n\n" + get_prompt("planning.system_prior_report").format(
            prior_question=prior_q,
            prior_report_answer=prior_report,
        )

    earlier_turns = es.get("earlier_turns")
    if earlier_turns:
        system_prompt += "\n\n" + get_prompt("planning.system_earlier_turns").format(
            earlier_turns=earlier_turns,
        )

    registry = _get_model(config)
    resolved = await registry.resolve("intelligence")
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
        writer({
            "event": "run_error",
            "payload": {
                "error": f"Model returned empty content for {NODE_NAME}",
                "budget_remaining": new_budget,
                "detail": detail,
                "purpose": NODE_NAME,
                "model": resolved.model_id,
                "call_count": 1,
            },
        })
        raise RuntimeError(
            f"Model returned empty content for {NODE_NAME} "
            f"(thinking={len(thinking)} chars)"
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

    writer({
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
    })
    logger.info("PLANNING Complete (%d planned calls)", len(tool_call_plan))

    return {
        "execution_state": {
            **es,
            "tool_call_plan": tool_call_plan,
            "budget_remaining": new_budget,
        },
    }
