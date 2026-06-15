"""Report generation node: produces the final research report.

Terminal node that always runs (budget-exempt). Produces a ResearchReport
from the knowledge model. Does NOT use tools.
"""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import ResearchReport, ResearchState, knowledge_summary
from moira.prompts import get_prompt
from moira.workflow.budget import deduct_cost
from moira.workflow.nodes._helpers import (
    _check_stop,
    _get_model,
    _now,
    _parse_json_object,
    _response_meta,
)

logger = logging.getLogger(__name__)

NODE_NAME = "report_generation"


def _format_facts(facts: list) -> str:
    lines = []
    for f in facts:
        cit = ", ".join(f.get("citation_ids", []))
        lines.append(
            f"{f['id']} | {f['subject']} | {f.get('claim', '')} | "
            f"{f['status']} | [{cit}]"
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


def _format_citations(citations: list) -> str:
    lines = []
    for i, c in enumerate(citations, 1):
        lines.append(
            f"[{i}] {c['id']} | {c['source']} | {c.get('url', '')} | "
            f"{c.get('title', '')} | {c.get('excerpt', '')[:100]}"
        )
    return "\n".join(lines)


async def report_generation(state: ResearchState, config: RunnableConfig) -> dict:
    """Generate the final research report from the knowledge model."""
    _check_stop(NODE_NAME, config)
    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": NODE_NAME, "timestamp": _now()}})

    es = state["execution_state"]
    knowledge = state["knowledge"]

    # Report generation is budget-exempt: always runs
    new_budget = deduct_cost(es["step_costs"], NODE_NAME, es["budget_remaining"])

    # Determine generation path and path_instruction
    error = es.get("error", "")
    if error:
        generation_path = "error"
        path_instruction = get_prompt("report_generation.path_error").format(
            error=error,
        )
    elif knowledge.get("verification_history"):
        latest = knowledge["verification_history"][-1]
        route = latest.get("route", "accept")
        if latest.get("goal_met") and not any(
            c["status"] == "contradicted"
            for c in knowledge.get("conclusions", [])
        ):
            generation_path = "verified"
            path_instruction = get_prompt("report_generation.path_verified")
        elif route in ("retry_research", "retry_synthesis"):
            generation_path = "retry_overruled"
            path_instruction = get_prompt(
                "report_generation.path_retry_overruled",
            )
        else:
            generation_path = "budget_exhausted"
            path_instruction = get_prompt(
                "report_generation.path_budget_exhausted",
            )
    else:
        generation_path = "budget_exhausted"
        path_instruction = get_prompt("report_generation.path_budget_exhausted")

    # Separate verified, contradicted, and unknown items for the prompt
    all_facts = knowledge.get("facts", [])
    all_conclusions = knowledge.get("conclusions", [])
    all_citations = knowledge.get("citations", [])

    verified_facts = [f for f in all_facts if f.get("status") == "verified"]
    verified_conclusions = [
        c for c in all_conclusions if c.get("status") == "verified"
    ]
    contradicted_items = (
        [f for f in all_facts if f.get("status") == "contradicted"]
        + [c for c in all_conclusions if c.get("status") == "contradicted"]
    )
    unknown_facts_list = [
        f for f in all_facts if f.get("status") == "unknown"
    ]

    # Try model-based report generation
    report: ResearchReport | None = None
    resolved = None
    response = None
    try:
        system_prompt = get_prompt("report_generation.system").format(
            path_instruction=path_instruction,
        )
        user_prompt = get_prompt("report_generation.user").format(
            question=knowledge["question"],
            user_goal=knowledge.get("user_goal", knowledge["question"]),
            verified_facts=_format_facts(verified_facts),
            verified_conclusions=_format_conclusions(verified_conclusions),
            contradicted_items=_format_facts(contradicted_items),
            unknown_facts=_format_facts(unknown_facts_list),
            citations=_format_citations(all_citations),
        )

        registry = _get_model(config)
        resolved = await registry.resolve("intelligence")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        logger.info("REPORT GENERATION Start (path=%s)", generation_path)
        response = await resolved.client.chat_completion(
            messages=messages,
            model=resolved.model_id,
            temperature=DEFAULT_TEMPERATURE,
        )

        raw = response.content or ""
        thinking = getattr(response, "thinking", "") or ""
        if not raw.strip():
            raise RuntimeError(
                f"Model returned empty content for report generation "
                f"(thinking={len(thinking)} chars). "
                f"Thinking: {thinking}"
            )
        parsed = _parse_json_object(raw)
        report = ResearchReport(
            answer=parsed.get("answer", ""),
            citations=parsed.get("citations", []),
            verified_facts=[
                dict(f) for f in knowledge.get("facts", [])
                if f.get("status") == "verified"
            ],
            verified_conclusions=[
                dict(c) for c in knowledge.get("conclusions", [])
                if c.get("status") == "verified"
            ],
            contradicted=[
                dict(f) for f in knowledge.get("facts", [])
                if f.get("status") == "contradicted"
            ] + [
                dict(c) for c in knowledge.get("conclusions", [])
                if c.get("status") == "contradicted"
            ],
            unknown_facts=[
                dict(f) for f in knowledge.get("facts", [])
                if f.get("status") == "unknown"
            ],
            critiques=parsed.get("critiques", []),
            total_cost=es["budget_limit"] - new_budget,
            tool_call_total_cost=es.get("total_tool_cost_consumed", 0.0),
            generation_path=generation_path,
        )

    except Exception as e:
        logger.error("Report generation model call failed: %s", e, exc_info=True)
        # Fallback: produce a minimal report from whatever we have
        report = ResearchReport(
            answer=knowledge.get("user_goal", knowledge["question"]),
            citations=[],
            verified_facts=[],
            verified_conclusions=[],
            contradicted=[],
            unknown_facts=[dict(f) for f in knowledge.get("facts", [])],
            critiques=[f"Report generation failed: {e}"],
            total_cost=es["budget_limit"] - new_budget,
            tool_call_total_cost=es.get("total_tool_cost_consumed", 0.0),
            generation_path="error" if error else "budget_exhausted",
        )

    writer({
        "event": "run_complete",
        "payload": {
            "report": dict(report) if report else None,
            "knowledge": knowledge_summary(knowledge),
            "generation_path": generation_path,
        },
    })
    writer({
        "event": "node_end",
        "payload": {
            "node": NODE_NAME,
            "budget_remaining": new_budget,
            "purpose": NODE_NAME,
            "model": resolved.model_id if resolved is not None else "",
            "call_count": 1,
            "detail": {"generation_path": generation_path},
            **(_response_meta(response) if response is not None else {}),
        },
    })
    logger.info("REPORT GENERATION Complete (path=%s)", generation_path)

    return {
        "knowledge": {
            "report": report,
            "generation_path": generation_path,
        },
        "execution_state": {
            **es,
            "budget_remaining": new_budget,
        },
    }
