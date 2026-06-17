"""Report generation node: produces the final research report.

Terminal node that always runs (budget-exempt). Produces a ResearchReport
from the knowledge model. Does NOT use tools.
"""

import logging
import re

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

_CITE_PATTERN = re.compile(r"\[(\d+)\]")


def _format_facts(facts: list) -> str:
    lines = []
    for f in facts:
        cit = ", ".join(f.get("citation_ids") or [])
        lines.append(
            f"{f['id']} | {f.get('subject') or ''} | {f.get('claim') or ''} | "
            f"{f['status']} | [{cit}]"
        )
    return "\n".join(lines)


def _format_conclusions(conclusions: list) -> str:
    lines = []
    for c in conclusions:
        facts_str = ", ".join(c.get("supporting_fact_ids") or [])
        lines.append(
            f"{c['id']} | {c['conclusion']} | [{facts_str}] | "
            f"{c.get('reasoning', '')} | {c['status']}"
        )
    return "\n".join(lines)


def _format_citations(citations: list) -> str:
    """Format citations for the system prompt given to the model.

    When a citation has multiple snippets (from recurring searches), all
    snippets are listed so the model sees the full context per source.
    """
    lines = []
    for i, c in enumerate(citations, 1):
        snippets = c.get("snippets")
        if snippets:
            snippet_parts = []
            for j, s in enumerate(snippets, 1):
                snippet_parts.append(f"Snippet {j}: {(s or '')[:100]}")
            snippet_str = "; ".join(snippet_parts)
        else:
            snippet_str = (c.get("excerpt") or "")[:100]
        lines.append(
            f"[{i}] {c['id']} | {c['source']} | {c.get('url') or ''} | "
            f"{c.get('title') or ''} | {snippet_str}"
        )
    return "\n".join(lines)


def _prune_and_renumber_citations(
    answer: str,
    all_citations: list[dict],
) -> tuple[str, list[dict], list[dict]]:
    """Split citations into cited and uncited, renumber inline markers.

    Parses ``[n]`` markers from the answer text, keeps only the citations
    the model actually referenced, and renumbers them sequentially (1, 2,
    3, …) in order of first appearance.  Citations not referenced are
    returned separately as uncited sources.

    If the answer contains no valid markers, all citations are returned as
    uncited and the answer is left unchanged.

    Returns ``(rewritten_answer, cited_citations, uncited_citations)``.
    """
    seen_order: list[int] = []
    seen_set: set[int] = set()
    for match in _CITE_PATTERN.finditer(answer):
        n = int(match.group(1))
        if 1 <= n <= len(all_citations) and n not in seen_set:
            seen_order.append(n)
            seen_set.add(n)

    if not seen_order:
        return answer, [], [dict(c) for c in all_citations]

    old_to_new = {old: new for new, old in enumerate(seen_order, 1)}

    def _replace(m: re.Match) -> str:
        n = int(m.group(1))
        return f"[{old_to_new[n]}]" if n in old_to_new else m.group(0)

    new_answer = _CITE_PATTERN.sub(_replace, answer)

    cited = [dict(all_citations[old - 1]) for old in seen_order]
    uncited = [dict(c) for i, c in enumerate(all_citations, 1) if i not in seen_set]
    return new_answer, cited, uncited


async def report_generation(state: ResearchState, config: RunnableConfig) -> dict:
    """Generate the final research report from the knowledge model."""
    _check_stop(NODE_NAME, config)
    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": NODE_NAME, "timestamp": _now()}})

    es = state["execution_state"]
    knowledge = state["knowledge"]

    # Report generation is budget-exempt: always runs
    new_budget = deduct_cost(es["step_costs"], NODE_NAME, es["budget_remaining"])

    # Determine the generation reason and corresponding prompt instruction.
    # The reason explains *why* the research loop ended and a report is being
    # generated now, rather than continuing the loop.
    error = es.get("error", "")
    if error:
        generation_reason = "error"
        path_instruction = get_prompt("report_generation.reason_error").format(
            error=error,
        )
    elif knowledge.get("evaluation_history"):
        latest_eval = knowledge["evaluation_history"][-1]
        eval_route = latest_eval.get("route", "accept")
        if latest_eval.get("goal_met") and not any(
            c["status"] == "contradicted" for c in knowledge.get("conclusions", [])
        ):
            generation_reason = "verified"
            path_instruction = get_prompt("report_generation.reason_verified")
        elif eval_route == "retry":
            # Evaluation wanted another cycle but the router sent us here.
            # Determine whether it was retry limits or budget that prevented it.
            rl = es.get("retry_limits", {})
            max_eval = rl.get("max_evaluation", 2)
            eval_count = es.get("evaluation_count", 0)
            if eval_count >= max_eval:
                generation_reason = "retries_exhausted"
                path_instruction = get_prompt(
                    "report_generation.reason_retries_exhausted",
                )
            else:
                generation_reason = "budget_exhausted"
                path_instruction = get_prompt(
                    "report_generation.reason_budget_exhausted",
                )
        else:
            # Evaluation accepted but goal not met — research is incomplete.
            generation_reason = "incomplete"
            path_instruction = get_prompt(
                "report_generation.reason_incomplete",
            )
    else:
        # No evaluation history — likely budget ran out before reaching evaluation.
        generation_reason = "budget_exhausted"
        path_instruction = get_prompt("report_generation.reason_budget_exhausted")

    # Separate verified, contradicted, and unknown items for the prompt
    all_facts = knowledge.get("facts", [])
    all_conclusions = knowledge.get("conclusions", [])
    all_citations = knowledge.get("citations", [])

    verified_facts = [f for f in all_facts if f.get("status") == "verified"]
    verified_conclusions = [c for c in all_conclusions if c.get("status") == "verified"]
    contradicted_items = [f for f in all_facts if f.get("status") == "contradicted"] + [
        c for c in all_conclusions if c.get("status") == "contradicted"
    ]
    unknown_facts_list = [f for f in all_facts if f.get("status") == "unknown"]

    # Build the prompt.
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

    # ---- Model call ----
    registry = _get_model(config)
    resolved = await registry.resolve("intelligence")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    logger.info("REPORT GENERATION Start (reason=%s)", generation_reason)
    response = await resolved.client.chat_completion(
        messages=messages,
        model=resolved.model_id,
        temperature=DEFAULT_TEMPERATURE,
    )

    raw = response.content or ""
    thinking = getattr(response, "thinking", "") or ""
    detail: dict = {}
    if thinking:
        detail["thinking"] = thinking[:2000]

    # ---- Empty response → error ----
    if not raw.strip():
        logger.error(
            "REPORT GENERATION: model returned empty content (thinking=%d chars)",
            len(thinking),
        )
        writer(
            {
                "event": "run_error",
                "payload": {
                    "error": "Model returned empty content for report generation",
                    "budget_remaining": new_budget,
                    "detail": detail,
                    "purpose": NODE_NAME,
                    "model": resolved.model_id,
                    "call_count": 1,
                },
            }
        )
        raise RuntimeError(
            f"Model returned empty content for report generation (thinking={len(thinking)} chars)"
        )

    # ---- Parse response ----
    parsed = _parse_json_object(raw)

    if not parsed or "answer" not in parsed:
        logger.error(
            "REPORT GENERATION: JSON parse failed (parsed=%d keys, response=%d chars)",
            len(parsed),
            len(raw),
        )
        writer(
            {
                "event": "run_error",
                "payload": {
                    "error": (
                        "Report generation model returned unparseable JSON "
                        f"(response={len(raw)} chars, parsed={len(parsed)} keys)"
                    ),
                    "budget_remaining": new_budget,
                    "detail": detail,
                    "purpose": NODE_NAME,
                    "model": resolved.model_id,
                    "call_count": 1,
                },
            }
        )
        raise RuntimeError(
            "Report generation model returned unparseable JSON "
            f"(response={len(raw)} chars, parsed={len(parsed)} keys)"
        )

    raw_answer = parsed.get("answer", "")
    cited_answer, cited_citations, uncited_citations = _prune_and_renumber_citations(
        raw_answer, all_citations
    )
    report = ResearchReport(
        answer=cited_answer,
        citations=cited_citations,
        uncited_sources=uncited_citations,
        verified_facts=[
            dict(f) for f in knowledge.get("facts", []) if f.get("status") == "verified"
        ],
        verified_conclusions=[
            dict(c) for c in knowledge.get("conclusions", []) if c.get("status") == "verified"
        ],
        contradicted=[
            dict(f) for f in knowledge.get("facts", []) if f.get("status") == "contradicted"
        ]
        + [dict(c) for c in knowledge.get("conclusions", []) if c.get("status") == "contradicted"],
        unknown_facts=[
            dict(f) for f in knowledge.get("facts", []) if f.get("status") == "unknown"
        ],
        critiques=parsed.get("critiques", []),
        total_cost=es["budget_limit"] - new_budget,
        tool_call_total_cost=es.get("total_tool_cost_consumed", 0.0),
        generation_reason=generation_reason,
    )

    writer(
        {
            "event": "run_complete",
            "payload": {
                "report": dict(report) if report else None,
                "knowledge": knowledge_summary(knowledge),
                "generation_reason": generation_reason,
            },
        }
    )
    writer(
        {
            "event": "node_end",
            "payload": {
                "node": NODE_NAME,
                "budget_remaining": new_budget,
                "purpose": NODE_NAME,
                "model": resolved.model_id,
                "call_count": 1,
                "detail": {"generation_reason": generation_reason},
                **_response_meta(response),
            },
        }
    )
    logger.info("REPORT GENERATION Complete (reason=%s)", generation_reason)

    return {
        "knowledge": {
            "report": report,
            "generation_reason": generation_reason,
        },
        "execution_state": {
            **es,
            "budget_remaining": new_budget,
        },
    }
