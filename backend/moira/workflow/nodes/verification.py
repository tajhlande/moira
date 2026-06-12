"""Verification node: evaluates facts, conclusions, and goal attainment.

Performs fact verification, conclusion verification, and goal assessment.
May call tools to re-check claims. Produces a VerificationOutcome that
determines routing.
"""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import (
    Fact,
    ResearchState,
    VerificationOutcome,
    next_id,
)
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

NODE_NAME = "verification"


def _format_facts_for_verification(facts: list[Fact]) -> str:
    lines = []
    for f in facts:
        if f.get("claim"):
            cit = ", ".join(f.get("citation_ids", []))
            lines.append(
                f"{f['id']} | {f['subject']} | {f['fact_needed']} | "
                f"{f['claim']} | {f['status']} | [{cit}]"
            )
    return "\n".join(lines)


def _format_conclusions(conclusions: list) -> str:
    lines = []
    for c in conclusions:
        facts_str = ", ".join(c.get("supporting_fact_ids", []))
        lines.append(
            f"{c['id']} | {c['conclusion']} | [{facts_str}] | "
            f"{c.get('reasoning', '')} | {c['status']}"
        )
    return "\n".join(lines)


def _format_tools(tools: list) -> str:
    if not tools:
        return "(no tools available)"
    return "\n".join(f"- {t.name}: {t.description[:80]}" for t in tools)


async def verification(state: ResearchState, config: RunnableConfig) -> dict:
    """Verify facts, conclusions, and assess goal attainment."""
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

    facts = list(knowledge["facts"])
    conclusions = list(knowledge.get("conclusions", []))

    # Optional: tool-assisted fact checking for facts with claims
    tool_results_log = []
    candidate_tools = es.get("candidate_tools", [])
    call_counts = dict(es.get("tool_call_counts", {}))
    total_tool_cost = es.get("total_tool_cost_consumed", 0.0)
    new_budget = es["budget_remaining"]

    # Do model-based verification
    user_prompt = get_prompt("verification.user").format(
        user_goal=knowledge.get("user_goal", knowledge["question"]),
        question=knowledge["question"],
        facts_with_claims_and_sources=_format_facts_for_verification(facts),
        conclusions_with_supporting_facts=_format_conclusions(conclusions),
        tool_descriptions=_format_tools(candidate_tools),
    )

    registry = _get_model(config)
    resolved = await registry.resolve("intelligence")
    messages = [
        {"role": "system", "content": get_prompt("verification.system")},
        {"role": "user", "content": user_prompt},
    ]

    verification_attempts = es.get("verification_attempts", 0) + 1
    logger.info("VERIFICATION Start (attempt=%d)", verification_attempts)
    response = await resolved.client.chat_completion(
        messages=messages,
        model=resolved.model_id,
        temperature=DEFAULT_TEMPERATURE,
    )

    raw = response.content or ""
    thinking = getattr(response, "thinking", "") or ""
    new_budget = deduct_cost(es["step_costs"], NODE_NAME, new_budget)

    detail = {
        "prompt": user_prompt,
        "response": raw,
        "model": resolved.model_id,
        "tool_results": tool_results_log,
    }
    if thinking:
        detail["thinking"] = thinking

    if not raw.strip():
        logger.error(
            "VERIFICATION: model returned empty content (thinking=%d chars)",
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
                "tool_call_count": len(tool_results_log),
            },
        })
        raise RuntimeError(
            f"Model returned empty content for {NODE_NAME} "
            f"(thinking={len(thinking)} chars)"
        )

    parsed = _parse_json_object(raw)

    # Apply fact results
    for fr in parsed.get("fact_results", []):
        fid = fr.get("fact_id", "")
        result = fr.get("result", "unverified")
        for fact in facts:
            if fact["id"] == fid:
                fact["status"] = result
                if fr.get("evidence"):
                    fact["verification_note"] = fr["evidence"]
                break

    # Apply conclusion results
    for cr in parsed.get("conclusion_results", []):
        cid = cr.get("conclusion_id", "")
        result = cr.get("result", "unverified")
        for conclusion in conclusions:
            if conclusion["id"] == cid:
                conclusion["status"] = result
                if cr.get("reason"):
                    conclusion["verification_note"] = cr["reason"]
                break

    # Build verification outcome
    new_facts_descriptions = parsed.get("new_unknown_facts", [])
    new_unknown_facts: list[Fact] = []
    for desc in new_facts_descriptions:
        if isinstance(desc, str) and desc.strip():
            new_id = next_id("f", facts + new_unknown_facts)
            new_unknown_facts.append(
                Fact(
                    id=new_id,
                    subject="",
                    fact_needed=desc.strip(),
                    status="unknown",
                )
            )

    outcome = VerificationOutcome(
        fact_results=parsed.get("fact_results", []),
        conclusion_results=parsed.get("conclusion_results", []),
        new_unknown_facts=[f["fact_needed"] for f in new_unknown_facts],
        goal_met=parsed.get("goal_met", False),
        goal_assessment=parsed.get("goal_assessment", ""),
        route=parsed.get("route", "accept"),
    )

    verification_history = list(knowledge.get("verification_history", []))
    verification_history.append(outcome)

    # Add new unknown facts to the facts list
    all_facts = facts + new_unknown_facts

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
            "tool_call_count": len(tool_results_log),
            **_response_meta(response),
        },
    })
    logger.info(
        "VERIFICATION Complete (route=%s, goal_met=%s)",
        outcome["route"],
        outcome["goal_met"],
    )

    return {
        "knowledge": {
            "facts": all_facts,
            "conclusions": conclusions,
            "verification_history": verification_history,
        },
        "execution_state": {
            **es,
            "budget_remaining": new_budget,
            "verification_attempts": verification_attempts,
            "tool_call_counts": call_counts,
            "total_tool_cost_consumed": total_tool_cost,
        },
    }
