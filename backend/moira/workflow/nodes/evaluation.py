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
    _format_citation_content,
    _format_prior_evaluations,
    _format_prior_reviews,
    _now,
    _response_meta,
)
from moira.workflow.nodes._helpers_deps import (
    _call_for_json,
    _check_stop,
    _resolve_intelligence,
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
        derivation = c.get("derivation", "direct")
        lines.append(
            f"{c['id']} | {c['conclusion']} | [{facts_str}] | "
            f"{c.get('reasoning', '')} | {c['status']} | {derivation}"
        )
    return "\n".join(lines)


def _apply_conclusion_results(
    conclusions: list, conclusion_results: list, logger_obj=None
) -> None:
    """Apply evaluator verdicts to conclusions in place.

    - Skip conclusions already marked "unsupported" (terminal verdict from
      structural sanity check).
    - Fact-status gate: a conclusion with fact_gate="blocked" cannot be
      upgraded to "verified" — it's downgraded to "unverified" instead.
    - Apply derivation re-labeling: the evaluator confirms or corrects the
      derivation label set by synthesis. Purely metadata; does not affect
      the status verdict.
    """
    for cr in conclusion_results:
        cid = cr.get("conclusion_id", "")
        result = cr.get("result") or cr.get("status") or "unverified"
        reason = cr.get("reason") or ""
        eval_derivation = cr.get("derivation", "")
        for conclusion in conclusions:
            if conclusion["id"] == cid:
                if conclusion.get("status") == "unsupported":
                    break
                if result == "verified" and conclusion.get("fact_gate") == "blocked":
                    conclusion["status"] = "unverified"
                    conclusion["verification_note"] = (
                        "Cannot verify — supporting facts not yet verified."
                    )
                    if logger_obj:
                        logger_obj.warning(
                            "EVALUATION: fact-status gate blocked conclusion %s "
                            "from 'verified' (unverified supporting facts)",
                            cid,
                        )
                    break
                conclusion["status"] = result
                if reason:
                    conclusion["verification_note"] = reason
                if eval_derivation in ("direct", "inferred"):
                    old_derivation = conclusion.get("derivation", "direct")
                    if eval_derivation != old_derivation:
                        if logger_obj:
                            logger_obj.info(
                                "EVALUATION: re-labeled conclusion %s derivation %s→%s",
                                cid,
                                old_derivation,
                                eval_derivation,
                            )
                        conclusion["derivation"] = eval_derivation
                break


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
        prior_reviews=_format_prior_reviews(
            knowledge.get("review_history", []),
            instruction=(
                "Prior review verdicts (fact verification from "
                "research_review). These show how the reviewer assessed each "
                "fact's evidence quality and whether cited sources support the "
                "claims. Use this context when evaluating whether conclusions "
                "are well-grounded."
            ),
        ),
        prior_evaluations=_format_prior_evaluations(
            knowledge.get("evaluation_history", []),
            instruction=(
                "Prior evaluation verdicts (your previous assessments of "
                "these conclusions). Maintain consistency with your earlier "
                "verdicts. You may change a result only if new evidence, new "
                "conclusions, or new facts have been added since the prior "
                "evaluation."
            ),
        ),
    )

    resolved = await _resolve_intelligence(config)
    messages = [
        {"role": "system", "content": render_prompt("evaluation.system")},
        {"role": "user", "content": user_prompt},
    ]

    evaluation_count = es.get("evaluation_count", 0) + 1
    logger.info("EVALUATION Start (count=%d)", evaluation_count)

    parsed, response, call_count = await _call_for_json(
        resolved.client,
        resolved.model_id,
        messages,
        required_key="route",
        node_name=NODE_NAME,
        temperature=DEFAULT_TEMPERATURE,
    )

    raw = (response.content or "") if response else ""
    thinking = getattr(response, "thinking", "") or ""
    new_budget = deduct_cost(es["step_costs"], NODE_NAME, es["budget_remaining"])
    # Charge for retry calls too
    if call_count > 1:
        new_budget = deduct_cost(es["step_costs"], NODE_NAME, new_budget)

    detail: dict[str, object] = {
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
                    "call_count": call_count,
                },
            }
        )
        raise RuntimeError(
            f"Model returned empty content for {NODE_NAME} (thinking={len(thinking)} chars)"
        )

    if not parsed or "route" not in parsed:
        logger.error(
            "EVALUATION: JSON parse failed after %d attempts (parsed=%d keys, response=%d chars).",
            call_count,
            len(parsed),
            len(raw),
        )
        writer(
            {
                "event": "run_error",
                "payload": {
                    "error": "Evaluation model returned unparseable JSON "
                    f"after {call_count} attempts",
                    "budget_remaining": new_budget,
                    "detail": detail,
                    "purpose": NODE_NAME,
                    "model": resolved.model_id,
                    "call_count": call_count,
                },
            }
        )
        raise RuntimeError(
            f"Evaluation model returned unparseable JSON "
            f"after {call_count} attempts "
            f"(response={len(raw)} chars)"
        )

    # Apply conclusion results (verdicts + derivation re-labeling).
    # See _apply_conclusion_results for the full logic.
    _apply_conclusion_results(conclusions, parsed.get("conclusion_results", []), logger)

    route = parsed.get("route", "accept")

    # Compute verified counts for progress tracking. These are stored in
    # the EvaluationOutcome so the next evaluation cycle can compare.
    verified_fact_count = sum(1 for f in facts if f.get("status") == "verified")
    verified_conclusion_count = sum(1 for c in conclusions if c.get("status") == "verified")

    # Progress-based retry cutoff: if this is the 2nd+ evaluation and
    # neither verified facts nor verified conclusions increased from
    # the previous cycle, accept instead of retrying. More research
    # cycles won't help if the model isn't finding new evidence.
    existing_eval_history = knowledge.get("evaluation_history", [])
    if evaluation_count >= 2 and route == "retry" and existing_eval_history:
        prev = existing_eval_history[-1]
        prev_facts = prev.get("verified_fact_count", 0)
        prev_conclusions = prev.get("verified_conclusion_count", 0)
        if verified_fact_count <= prev_facts and verified_conclusion_count <= prev_conclusions:
            route = "accept"
            logger.info(
                "EVALUATION: overriding retry→accept (no progress: "
                "facts %d→%d, conclusions %d→%d)",
                prev_facts,
                verified_fact_count,
                prev_conclusions,
                verified_conclusion_count,
            )

    outcome = EvaluationOutcome(
        conclusion_results=parsed.get("conclusion_results", []),
        goal_met=parsed.get("goal_met", False),
        goal_assessment=parsed.get("goal_assessment", ""),
        route=route,
        verified_fact_count=verified_fact_count,
        verified_conclusion_count=verified_conclusion_count,
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
                "call_count": call_count,
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
