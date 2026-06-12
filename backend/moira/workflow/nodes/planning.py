"""Planning node: designs a cost-aware tool call plan.

Receives unknown facts and candidate tools with costs. Produces a
ToolCallPlan that fits within the remaining budget. Uses the intelligence
model.

Phase A: uses stub prompts. Full prompts added in Phase B."""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import ResearchState, ToolCallPlan
from moira.workflow.budget import can_execute, deduct_cost
from moira.workflow.nodes._helpers import _check_stop, _get_model, _now, _parse_json_object, _response_meta

logger = logging.getLogger(__name__)

NODE_NAME = "planning"

_STUB_SYSTEM = (
    "You are a research planning assistant. Design tool calls to discover "
    "the facts needed. Respond with a JSON object with key 'calls': a list "
    "of objects, each with 'tool' (string), 'args' (object), "
    "'target_fact_ids' (list of fact IDs), and 'rationale' (string). "
    "Consider the cost of each call vs. remaining budget."
)

_STUB_USER = (
    "User goal: {user_goal}\n"
    "Unknown facts (ID | subject | fact_needed):\n{unknown_facts}\n"
    "Available tools (name | cost per call):\n{tool_list}\n"
    "Budget remaining: {budget_remaining}"
)


def _format_unknown_facts(facts: list) -> str:
    lines = []
    for f in facts:
        if f.get("status") in ("unknown", "contradicted"):
            lines.append(f"{f['id']} | {f['subject']} | {f['fact_needed']}")
    return "\n".join(lines)


def _format_tools_with_costs(tools: list, tool_costs: dict[str, float]) -> str:
    lines = []
    for t in tools:
        cost = tool_costs.get(t.name, 1.0)
        lines.append(f"- {t.name} | cost: {cost} | {t.description[:80]}")
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

    user_prompt = _STUB_USER.format(
        user_goal=knowledge.get("user_goal", knowledge["question"]),
        unknown_facts=_format_unknown_facts(knowledge["facts"]),
        tool_list=_format_tools_with_costs(
            es.get("candidate_tools", []),
            es.get("tool_costs", {}),
        ),
        budget_remaining=es["budget_remaining"],
    )

    registry = _get_model(config)
    resolved = await registry.resolve("intelligence")
    messages = [
        {"role": "system", "content": _STUB_SYSTEM},
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
