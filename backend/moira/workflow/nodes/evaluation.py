"""Evaluation node: evaluates conclusion logic and goal attainment.

Examines conclusions against fact statuses (set by research_review). Decides
whether the verified set is sufficient to answer the user's question. Routes
to report_generation (accept) or back to tool_identification (retry) for a
full pipeline reset.

This node is tool-free — pure reasoning over already-verified facts.
When route is "retry", review_count is reset to 0 so the new evaluation
cycle gets a fresh set of research retries.
"""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import EvaluationOutcome, ResearchState
from moira.prompts import render_prompt
from moira.workflow.budget import can_execute, deduct_cost, get_node_cost
from moira.workflow.nodes._helpers import (
    _check_stop,
    _format_citation_content,
    _get_model,
    _now,
    _parse_json_object,
    _response_meta,
)

logger = logging.getLogger(__name__)

NODE_NAME = "evaluation"


def _format_facts_for_evaluation(facts: list) -> str:
    lines = []
    for f in facts:
        cit = ", ".join(f.get("citation_ids", []))
        lines.append(
            f"{f['id']} | {f.get('subject', '')} | {f.get('claim', '')} | {f['status']} | [{cit}]"
        )
    return "\n".join(lines)


def _format_conclusions_for_evaluation(conclusions: list) -> str:
    lines = []
    for c in conclusions:
        facts_str = ", ".join(c.get("supporting_fact_ids", []))
        lines.append(
            f"{c['id']} | {c['conclusion']} | [{facts_str}] | "
            f"{c.get('reasoning', '')} | {c['status']}"
        )
    return "\n".join(lines)


async def evaluation(state: ResearchState, config: RunnableConfig) -> dict:
    """Evaluate whether conclusions follow from verified facts and the goal is met."""
    _check_stop(NODE_NAME, config)
    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": NODE_NAME, "timestamp": _now()}})

    es = state["execution_state"]
    knowledge = state["knowledge"]
    if not can_execute(es["step_costs"], NODE_NAME, es["budget_remaining"]):
        logger.info(
            "EVALUATION: insufficient budget (%.1f remaining, need %.1f)",
            es["budget_remaining"],
            get_node_cost(es["step_costs"], NODE_NAME),
        )
        writer(
            {
                "event": "node_end",
                "payload": {
                    "node": NODE_NAME,
                    "budget_remaining": es["budget_remaining"],
                },
            }
        )
        # Do not set error — let report_generation use
        # generation_reason="budget_exhausted" instead of "error".
        return {
            "execution_state": {
                **es,
            },
        }

    facts = list(knowledge["facts"])
    conclusions = list(knowledge.get("conclusions", []))

    user_prompt = render_prompt(
        "evaluation.user",
        user_goal=knowledge.get("user_goal", knowledge["question"]),
        question=knowledge["question"],
        facts_with_statuses=_format_facts_for_evaluation(facts),
        conclusions_with_supporting_facts=_format_conclusions_for_evaluation(conclusions),
        source_content=_format_citation_content(
            knowledge.get("citations", []), conclusions, facts
        ),
    )

    registry = _get_model(config)
    resolved = await registry.resolve("intelligence")
    messages = [
        {"role": "system", "content": render_prompt("evaluation.system")},
        {"role": "user", "content": user_prompt},
    ]

    evaluation_count = es.get("evaluation_count", 0) + 1
    logger.info("EVALUATION Start (count=%d)", evaluation_count)

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
            "EVALUATION: model returned empty content (thinking=%d chars)",
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

    if not parsed or "route" not in parsed:
        logger.error(
            "EVALUATION: JSON parse failed (parsed=%d keys, response=%d chars)",
            len(parsed),
            len(raw),
        )
        writer(
            {
                "event": "run_error",
                "payload": {
                    "error": "Evaluation model returned unparseable JSON",
                    "budget_remaining": new_budget,
                    "detail": detail,
                    "purpose": NODE_NAME,
                    "model": resolved.model_id,
                    "call_count": 1,
                },
            }
        )
        raise RuntimeError(
            f"Evaluation model returned unparseable JSON "
            f"(response={len(raw)} chars, parsed_keys={list(parsed.keys())})"
        )

    # Apply conclusion results. Accept "status" as a fallback for "result"
    # since models sometimes use the wrong key — the verdict is critical.
    # Only "reason" is used for the note field.
    #
    # Skip conclusions already marked "unsupported" by the Phase 3 structural
    # sanity check — their verdict is terminal (no model tokens were spent on
    # them) and must not be overwritten by model output. The evaluator still
    # sees them in the prompt context to inform the holistic goal_met call.
    for cr in parsed.get("conclusion_results", []):
        cid = cr.get("conclusion_id", "")
        result = cr.get("result") or cr.get("status") or "unverified"
        reason = cr.get("reason") or ""
        for conclusion in conclusions:
            if conclusion["id"] == cid:
                if conclusion.get("status") == "unsupported":
                    break
                conclusion["status"] = result
                if reason:
                    conclusion["verification_note"] = reason
                break

    route = parsed.get("route", "accept")
    outcome = EvaluationOutcome(
        conclusion_results=parsed.get("conclusion_results", []),
        goal_met=parsed.get("goal_met", False),
        goal_assessment=parsed.get("goal_assessment", ""),
        route=route,
    )

    evaluation_history = list(knowledge.get("evaluation_history", []))
    evaluation_history.append(outcome)

    detail["structured_output"] = {
        "conclusion_results": parsed.get("conclusion_results", []),
        "goal_met": parsed.get("goal_met", False),
        "goal_assessment": parsed.get("goal_assessment", ""),
        "route": route,
    }

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
    logger.info(
        "EVALUATION Complete (route=%s, goal_met=%s)",
        route,
        outcome["goal_met"],
    )

    exec_update = {
        **es,
        "budget_remaining": new_budget,
        "evaluation_count": evaluation_count,
    }

    # Reset review_count when evaluation triggers a retry, so the new
    # evaluation cycle gets a fresh set of research retries.
    if route == "retry":
        exec_update["review_count"] = 0
        logger.info("EVALUATION: resetting review_count for new evaluation cycle")

    return {
        "knowledge": {
            "conclusions": conclusions,
            "evaluation_history": evaluation_history,
        },
        "execution_state": exec_update,
    }
