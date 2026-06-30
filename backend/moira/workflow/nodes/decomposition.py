"""Decomposition node: breaks the user question into structured components.

Identifies the user goal, topic, entities, concepts, and unknown facts
needed to answer the question. Uses the intelligence model (needs world
knowledge). Does NOT use tools.
"""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import Fact, ResearchState, next_id
from moira.prompts import render_prompt
from moira.workflow.budget import can_execute, deduct_cost
from moira.workflow.nodes._helpers import (
    _check_stop,
    _now,
    _parse_json_object,
    _resolve_intelligence,
    _response_meta,
)

logger = logging.getLogger(__name__)

NODE_NAME = "decomposition"


async def decomposition(state: ResearchState, config: RunnableConfig) -> dict:
    """Decompose the user question into goal, topic, entities, concepts, and
    unknown facts. Uses the intelligence model."""
    _check_stop(NODE_NAME, config)
    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": NODE_NAME, "timestamp": _now()}})

    es = state["execution_state"]
    knowledge = state["knowledge"]
    question = knowledge["question"]
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

    resolved = await _resolve_intelligence(config)
    system_prompt = render_prompt("decomposition.system")
    user_prompt = render_prompt("decomposition.user", question=question)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    logger.info("DECOMPOSITION Start")
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
            "DECOMPOSITION: model returned empty content (thinking=%d chars)",
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

    # Build facts from parsed unknown_facts
    facts: list[Fact] = []
    for item in parsed.get("unknown_facts", []):
        if isinstance(item, dict) and item.get("fact_needed"):
            fact_id = next_id("f", facts)
            facts.append(
                Fact(
                    id=fact_id,
                    subject=item.get("subject", ""),
                    fact_needed=item["fact_needed"],
                    status="unknown",
                )
            )

    updated_knowledge = {
        "user_goal": parsed.get("user_goal", ""),
        "topic": parsed.get("topic", ""),
        "entities": parsed.get("entities", []),
        "concepts": parsed.get("concepts", []),
        "facts": facts,
    }

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
    logger.info("DECOMPOSITION Complete (%d facts)", len(facts))

    return {
        "knowledge": updated_knowledge,
        "execution_state": {
            **es,
            "budget_remaining": new_budget,
        },
    }
