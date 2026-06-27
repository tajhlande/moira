"""Research review node: evaluates evidence coverage and fact quality.

Reviews facts against cited evidence. Marks facts as verified/contradicted/
unverified. Identifies gaps. Routes to evaluation (continue) or back to
research (retry) when critical evidence is missing.

This node is tool-free — it evaluates existing evidence only.
"""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import Fact, ResearchState, ReviewOutcome
from moira.prompts import get_prompt
from moira.workflow.budget import can_execute, deduct_cost
from moira.workflow.nodes._helpers import (
    _check_stop,
    _format_citation_content,
    _get_model,
    _now,
    _parse_json_object,
    _response_meta,
)

logger = logging.getLogger(__name__)

NODE_NAME = "research_review"


def _format_facts_for_review(facts: list[Fact]) -> str:
    lines = []
    for f in facts:
        if f.get("claim"):
            cit = ", ".join(f.get("citation_ids", []))
            lines.append(
                f"{f['id']} | {f['subject']} | {f['fact_needed']} | "
                f"{f['claim']} | {f['status']} | [{cit}]"
            )
        else:
            lines.append(
                f"{f['id']} | {f['subject']} | {f['fact_needed']} | (no claim) | {f['status']}"
            )
    return "\n".join(lines)


def _format_conclusions_for_context(conclusions: list) -> str:
    """Brief listing of conclusions so the reviewer understands what the
    research needs to support."""
    if not conclusions:
        return "(no conclusions yet)"
    lines = []
    for c in conclusions:
        facts_str = ", ".join(c.get("supporting_fact_ids", []))
        lines.append(f"{c['id']} | {c['conclusion']} | [{facts_str}]")
    return "\n".join(lines)


def _flag_unsupported_conclusions(facts: list[Fact], conclusions: list) -> int:
    """Structural sanity check: flag conclusions that reference non-existent
    fact IDs as ``"unsupported"``.

    This is a zero-model-cost check — it catches hallucinated fact references
    before evaluation spends tokens on them.  ``"unsupported"`` is terminal:
    evaluation will skip these conclusions (see Phase 6) and preserve the
    structural verdict.

    Returns the count of newly-flagged conclusions (for logging).
    """
    known_fact_ids = {f["id"] for f in facts}
    flagged = 0
    for conclusion in conclusions:
        for fid in conclusion.get("supporting_fact_ids", []):
            if fid not in known_fact_ids:
                conclusion["status"] = "unsupported"
                conclusion["verification_note"] = f"References non-existent fact ID: {fid}"
                flagged += 1
                break  # one hallucinated reference is sufficient
    return flagged


async def research_review(state: ResearchState, config: RunnableConfig) -> dict:
    """Evaluate whether the research gathered sufficient evidence."""
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

    facts = list(knowledge["facts"])
    conclusions = list(knowledge.get("conclusions", []))

    user_prompt = get_prompt("research_review.user").format(
        user_goal=knowledge.get("user_goal", knowledge["question"]),
        question=knowledge["question"],
        facts_with_claims_and_sources=_format_facts_for_review(facts),
        conclusions_context=_format_conclusions_for_context(conclusions),
        source_content=_format_citation_content(
            knowledge.get("citations", []), conclusions, facts
        ),
    )

    registry = _get_model(config)
    resolved = await registry.resolve("intelligence")
    messages = [
        {"role": "system", "content": get_prompt("research_review.system")},
        {"role": "user", "content": user_prompt},
    ]

    review_count = es.get("review_count", 0) + 1
    logger.info("RESEARCH_REVIEW Start (count=%d)", review_count)

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
            "RESEARCH_REVIEW: model returned empty content (thinking=%d chars)",
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
            "RESEARCH_REVIEW: JSON parse failed (parsed=%d keys, response=%d chars)",
            len(parsed),
            len(raw),
        )
        writer(
            {
                "event": "run_error",
                "payload": {
                    "error": "Research review model returned unparseable JSON",
                    "budget_remaining": new_budget,
                    "detail": detail,
                    "purpose": NODE_NAME,
                    "model": resolved.model_id,
                    "call_count": 1,
                },
            }
        )
        raise RuntimeError(
            f"Research review model returned unparseable JSON "
            f"(response={len(raw)} chars, parsed_keys={list(parsed.keys())})"
        )

    # Apply fact results. Accept "status" as a fallback for "result" since
    # models sometimes use the wrong key — the verdict is critical for routing.
    # Only "evidence" is used for the note field (not "evaluation" or other
    # model-invented keys, which are assessments, not evidence).
    for fr in parsed.get("fact_results", []):
        fid = fr.get("fact_id", "")
        result = fr.get("result") or fr.get("status") or "unverified"
        evidence = fr.get("evidence") or ""
        for fact in facts:
            if fact["id"] == fid:
                fact["status"] = result
                if evidence:
                    fact["verification_note"] = evidence
                break

    # Structural sanity check: flag conclusions with hallucinated fact
    # references as "unsupported" (terminal — no model call needed).
    unsupported_count = _flag_unsupported_conclusions(facts, conclusions)
    if unsupported_count:
        logger.info(
            "RESEARCH_REVIEW: %d conclusion(s) marked unsupported (non-existent fact references)",
            unsupported_count,
        )

    outcome = ReviewOutcome(
        fact_results=parsed.get("fact_results", []),
        coverage_assessment=parsed.get("coverage_assessment", ""),
        missing_areas=parsed.get("missing_areas", []),
        route=parsed.get("route", "continue"),
    )

    review_history = list(knowledge.get("review_history", []))
    review_history.append(outcome)

    detail["structured_output"] = {
        "fact_results": parsed.get("fact_results", []),
        "coverage_assessment": parsed.get("coverage_assessment", ""),
        "missing_areas": parsed.get("missing_areas", []),
        "route": parsed.get("route", "continue"),
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
        "RESEARCH_REVIEW Complete (route=%s, missing_areas=%d)",
        outcome["route"],
        len(outcome["missing_areas"]),
    )

    return {
        "knowledge": {
            "facts": facts,
            "conclusions": conclusions,
            "review_history": review_history,
        },
        "execution_state": {
            **es,
            "budget_remaining": new_budget,
            "review_count": review_count,
        },
    }
