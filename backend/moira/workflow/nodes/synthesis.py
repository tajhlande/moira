"""Synthesis node: derives conclusions from discovered facts.

Uses the intelligence model. Does NOT use tools. Derives conclusions
ONLY from the provided facts. On retry, conclusions are replaced entirely.

Phase A: uses stub prompts. Full prompts added in Phase B."""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import Conclusion, ResearchState, next_id
from moira.workflow.budget import can_execute, deduct_cost
from moira.workflow.nodes._helpers import _check_stop, _get_model, _now, _parse_json_object, _response_meta

logger = logging.getLogger(__name__)

NODE_NAME = "synthesis"

_STUB_SYSTEM = (
    "You are a synthesis assistant. Derive conclusions from the provided "
    "facts ONLY. Each conclusion must reference supporting fact IDs. "
    "Respond with JSON: {conclusions: [{conclusion: string, "
    "supporting_fact_ids: list, reasoning: string}]}. "
    "Do NOT use world knowledge."
)

_STUB_USER = (
    "User goal: {user_goal}\n"
    "Topic: {topic}\n"
    "Entities: {entities}\n"
    "Facts:\n{facts_with_claims}"
)


def _format_facts_with_claims(facts: list) -> str:
    lines = []
    for f in facts:
        if f.get("status") in ("verified", "unverified") and f.get("claim"):
            cit_str = ", ".join(f.get("citation_ids", []))
            lines.append(
                f"{f['id']} | {f['subject']} | {f['fact_needed']} | "
                f"{f['claim']} | {f['status']} | [{cit_str}]"
            )
    return "\n".join(lines)


async def synthesis(state: ResearchState, config: RunnableConfig) -> dict:
    """Derive conclusions from discovered facts."""
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
        topic=knowledge.get("topic", ""),
        entities=", ".join(knowledge.get("entities", [])),
        facts_with_claims=_format_facts_with_claims(knowledge["facts"]),
    )

    registry = _get_model(config)
    resolved = await registry.resolve("intelligence")
    messages = [
        {"role": "system", "content": _STUB_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    retry_count = es.get("synthesis_retry_count", 0)
    logger.info("SYNTHESIS Start (retry=%d)", retry_count)
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
            "SYNTHESIS: model returned empty content (thinking=%d chars)",
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

    conclusions: list[Conclusion] = []
    for item in parsed.get("conclusions", []):
        if isinstance(item, dict) and item.get("conclusion"):
            c_id = next_id("c", conclusions)
            conclusions.append(
                Conclusion(
                    id=c_id,
                    conclusion=item["conclusion"],
                    supporting_fact_ids=item.get("supporting_fact_ids", []),
                    reasoning=item.get("reasoning", ""),
                    status="unverified",
                )
            )

    detail["structured_output"] = {"conclusions": [dict(c) for c in conclusions]}

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
    logger.info("SYNTHESIS Complete (%d conclusions)", len(conclusions))

    return {
        "knowledge": {
            "conclusions": conclusions,
        },
        "execution_state": {
            **es,
            "budget_remaining": new_budget,
        },
    }
