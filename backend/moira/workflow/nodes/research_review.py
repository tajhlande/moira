"""Research review node: evaluates evidence coverage and fact quality.

Reviews facts against cited evidence. Marks facts as verified/contradicted/
unverified. Identifies gaps. Routes to evaluation (continue) or back to
research (retry) when critical evidence is missing.

This node is tool-free — it evaluates existing evidence only.
"""

import logging
import re

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import Fact, ResearchState, ReviewOutcome
from moira.prompts import render_prompt
from moira.workflow.budget import can_execute, deduct_cost
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

    user_prompt = render_prompt(
        "research_review.user",
        user_goal=knowledge.get("user_goal", knowledge["question"]),
        question=knowledge["question"],
        facts_with_claims_and_sources=_format_facts_for_review(facts),
        conclusions_context=_format_conclusions_for_context(conclusions),
        source_content=_format_citation_content(
            knowledge.get("citations", []), conclusions, facts
        ),
        prior_reviews=_format_prior_reviews(
            knowledge.get("review_history", []),
            instruction=(
                "Prior review verdicts (your previous assessments of these "
                "facts). Use these to maintain consistency. You may change a "
                "verdict only if a more careful reading of existing sources "
                "reveals information you missed, or if new citations have been "
                "added since the prior review. Do not rubber-stamp a claim you "
                "previously rejected without specific justification for why the "
                "evidence now supports it."
            ),
        ),
        prior_evaluations=_format_prior_evaluations(
            knowledge.get("evaluation_history", []),
            instruction=(
                "Prior evaluation feedback (concerns from the conclusion "
                "evaluator). The evaluation step flagged conclusions built on "
                "some of these facts as unsupported or overclaimed. When "
                "re-examining facts, check whether the claim needs to be "
                "narrowed to match what the evidence actually establishes, "
                "rather than verifying an overclaim."
            ),
        ),
    )

    resolved = await _resolve_intelligence(config)
    messages = [
        {"role": "system", "content": render_prompt("research_review.system")},
        {"role": "user", "content": user_prompt},
    ]

    review_count = es.get("review_count", 0) + 1
    logger.info("RESEARCH_REVIEW Start (count=%d)", review_count)

    parsed, response, call_count = await _call_for_json(
        resolved.client,
        resolved.model_id,
        messages,
        required_key="route",
        node_name=NODE_NAME,
        temperature=DEFAULT_TEMPERATURE,
    )

    raw = response.content or ""
    thinking = getattr(response, "thinking", "") or ""
    new_budget = deduct_cost(es["step_costs"], NODE_NAME, es["budget_remaining"])
    if call_count > 1:
        new_budget = deduct_cost(es["step_costs"], NODE_NAME, new_budget)

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
                    "call_count": call_count,
                },
            }
        )
        raise RuntimeError(
            f"Model returned empty content for {NODE_NAME} (thinking={len(thinking)} chars)"
        )

    if not parsed or "route" not in parsed:
        logger.error(
            "RESEARCH_REVIEW: JSON parse failed after %d attempts "
            "(parsed=%d keys, response=%d chars).",
            call_count,
            len(parsed),
            len(raw),
        )
        writer(
            {
                "event": "run_error",
                "payload": {
                    "error": "Research review model returned unparseable JSON "
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
            f"Research review model returned unparseable JSON "
            f"after {call_count} attempts "
            f"(response={len(raw)} chars)"
        )

    # Apply fact results. Accept "status" as a fallback for "result" since
    # models sometimes use the wrong key — the verdict is critical for routing.
    # Only "evidence" is used for the note field (not "evaluation" or other
    # model-invented keys, which are assessments, not evidence).
    #
    # Claim correction (Phase 1): when the reviewer marks a fact "contradicted"
    # but supplies a corrected_claim that the cited evidence clearly supports,
    # apply it and flip the fact to "verified". This avoids a full research
    # retry to re-discover what the reviewer already sees in the evidence.
    #
    # Process-metadata detection (Phase 3): when the reviewer marks a fact
    # "unknown", the claim is not a factual assertion (it describes the
    # research process — e.g. "insufficient data found"). Revert the fact to
    # unknown status and clear the claim and citations so the fact can be
    # re-researched cleanly. This replaces the brittle regex pattern layer
    # that previously caught these claims at write time in research.py.
    corrections_applied = 0
    unknown_reversions = 0
    for fr in parsed.get("fact_results", []):
        fid = fr.get("fact_id", "")
        result = fr.get("result") or fr.get("status") or "unverified"
        evidence = fr.get("evidence") or ""
        corrected_claim = (fr.get("corrected_claim") or "").strip()
        for fact in facts:
            if fact["id"] == fid:
                if result == "unknown":
                    # Reviewer recognized the claim as process-metadata or
                    # otherwise non-factual. Clear it so downstream nodes
                    # and retry context see a clean unknown fact.
                    fact["status"] = "unknown"
                    fact["claim"] = ""
                    fact["citation_ids"] = []
                    unknown_reversions += 1
                elif result == "contradicted" and corrected_claim:
                    fact["claim"] = corrected_claim
                    fact["status"] = "verified"
                    fact["corrected"] = True
                    corrected_citations = fr.get("corrected_citation_ids")
                    if corrected_citations:
                        fact["citation_ids"] = corrected_citations
                    elif fr.get("citation_ids"):
                        fact["citation_ids"] = fr["citation_ids"]
                    corrections_applied += 1
                else:
                    fact["status"] = result
                    # Populate citation_ids from the structured review field.
                    # The reviewer cross-references claims against source
                    # content — its citation linkage is authoritative.
                    if fr.get("citation_ids"):
                        fact["citation_ids"] = fr["citation_ids"]
                if evidence:
                    fact["verification_note"] = evidence
                # Fallback: if citation_ids is still empty but the evidence
                # text mentions citation references, parse them. The reviewer
                # often writes "Sources [cit011] and [cit046] confirm..."
                # in the evidence field even when it omits the structured
                # citation_ids field.
                if result != "unknown" and not fact.get("citation_ids") and evidence:
                    parsed_cites = sorted(set(re.findall(r"\[(cit\d+)\]", evidence)))
                    if parsed_cites:
                        fact["citation_ids"] = parsed_cites
                break

    if corrections_applied:
        logger.info("RESEARCH_REVIEW: applied %d claim correction(s)", corrections_applied)
    if unknown_reversions:
        logger.info(
            "RESEARCH_REVIEW: reverted %d fact(s) to unknown (process-metadata claims)",
            unknown_reversions,
        )

    # Guard: the reviewer sometimes marks empty-claim facts as "verified"
    # or "unverified" — there is nothing to verify without a claim.  Revert
    # these to "unknown" so planning and research can re-attempt them.
    for fact in facts:
        claim = (fact.get("claim") or "").strip()
        if not claim and fact.get("status") in ("verified", "unverified"):
            logger.warning(
                "RESEARCH_REVIEW: %s has no claim but reviewer marked it "
                "'%s' — reverting to 'unknown'",
                fact["id"],
                fact["status"],
            )
            fact["status"] = "unknown"

    # Structural sanity check: flag conclusions with hallucinated fact
    # references as "unsupported" (terminal — no model call needed).
    unsupported_count = _flag_unsupported_conclusions(facts, conclusions)
    if unsupported_count:
        logger.info(
            "RESEARCH_REVIEW: %d conclusion(s) marked unsupported (non-existent fact references)",
            unsupported_count,
        )

    # Fact-status gate: mark conclusions whose supporting facts are not all
    # verified. Evaluation must not upgrade these to "verified" — a conclusion
    # cannot be verified unless every fact it cites is verified. This is a
    # mechanical check (no model judgment) that catches the common failure
    # where the evaluator marks a conclusion "verified" while its supporting
    # facts are still "unverified".
    fact_status_map = {f["id"]: f.get("status", "unknown") for f in facts}
    gated_count = 0
    for conclusion in conclusions:
        if conclusion.get("status") == "unsupported":
            continue
        supporting_ids = conclusion.get("supporting_fact_ids", [])
        unverified_support = [
            fid for fid in supporting_ids if fact_status_map.get(fid, "unknown") != "verified"
        ]
        if unverified_support:
            conclusion["fact_gate"] = "blocked"
            gated_count += 1
    if gated_count:
        logger.info(
            "RESEARCH_REVIEW: %d conclusion(s) gated by unverified facts",
            gated_count,
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
                "call_count": call_count,
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
