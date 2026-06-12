"""Report generation node: produces the final research report.

Terminal node that always runs (budget-exempt). Produces a ResearchReport
from the knowledge model. Does NOT use tools.

Phase A: uses stub prompts. Full prompts added in Phase B."""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import ResearchReport, ResearchState
from moira.workflow.budget import deduct_cost
from moira.workflow.nodes._helpers import _check_stop, _get_model, _now, _parse_json_object, _response_meta

logger = logging.getLogger(__name__)

NODE_NAME = "report_generation"

_STUB_SYSTEM = (
    "Write a research report using ONLY the provided facts and conclusions. "
    "Respond with JSON: {answer: string, citations: [], verified_facts: [], "
    "verified_conclusions: [], contradicted: [], unknown_facts: [], "
    "critiques: [], total_cost: 0, tool_call_total_cost: 0, "
    "generation_path: string}. "
    "generation_path should be one of: 'verified', 'budget_exhausted', 'error'."
)

_STUB_USER = (
    "Question: {question}\n"
    "User goal: {user_goal}\n"
    "Facts:\n{facts}\n"
    "Conclusions:\n{conclusions}\n"
    "Citations:\n{citations}\n"
    "Generation path: {generation_path}"
)


def _format_facts(facts: list) -> str:
    lines = []
    for f in facts:
        lines.append(
            f"{f['id']} | {f['subject']} | {f.get('claim', '')} | {f['status']}"
        )
    return "\n".join(lines)


def _format_conclusions(conclusions: list) -> str:
    lines = []
    for c in conclusions:
        lines.append(
            f"{c['id']} | {c['conclusion']} | {c['status']}"
        )
    return "\n".join(lines)


def _format_citations(citations: list) -> str:
    lines = []
    for c in citations:
        lines.append(
            f"{c['id']} | {c['source']} | {c.get('url', '')} | {c.get('excerpt', '')[:100]}"
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

    # Determine generation path
    error = es.get("error", "")
    if error:
        generation_path = "error"
    elif knowledge.get("verification_history"):
        latest = knowledge["verification_history"][-1]
        if latest.get("goal_met") and not any(
            c["status"] == "contradicted"
            for c in knowledge.get("conclusions", [])
        ):
            generation_path = "verified"
        else:
            generation_path = "budget_exhausted"
    else:
        generation_path = "budget_exhausted"

    # Try model-based report generation
        report: ResearchReport | None = None
        resolved = None
        response = None
    try:
        user_prompt = _STUB_USER.format(
            question=knowledge["question"],
            user_goal=knowledge.get("user_goal", knowledge["question"]),
            facts=_format_facts(knowledge.get("facts", [])),
            conclusions=_format_conclusions(knowledge.get("conclusions", [])),
            citations=_format_citations(knowledge.get("citations", [])),
            generation_path=generation_path,
        )

        registry = _get_model(config)
        resolved = await registry.resolve("intelligence")
        messages = [
            {"role": "system", "content": _STUB_SYSTEM},
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
