"""Deterministic metrics for evaluation.

Pure functions over the artifacts dict produced by
:mod:`moira_eval.capture`.  No LLM calls — every metric is mechanically
derivable from the run data.

The most important metric is ``hallucinated_fact_id_count`` — it cross-
references ``conclusion.supporting_fact_ids`` against the known ``facts``
list, the same integrity check that ``research_review`` performs at runtime.

Usage::

    from moira_eval.capture import capture_artifacts
    from moira_eval.metrics import compute_metrics

    artifacts = capture_artifacts("moira.db", run_id="<uuid>")
    metrics = compute_metrics(artifacts)
"""

from typing import Any

# Tools considered "generic" — not domain-specific.  The ratio of specialized
# tool usage to total tool usage is a health signal for tool routing.
_GENERIC_TOOLS = frozenset({"web_search", "url_content"})


def _safe_div(numerator: float, denominator: float) -> float:
    """Division that returns 0.0 when denominator is zero."""
    if denominator == 0:
        return 0.0
    return numerator / denominator


# ---------------------------------------------------------------------------
# Sub-metrics
# ---------------------------------------------------------------------------


def _tool_metrics(artifacts: dict[str, Any]) -> dict[str, Any]:
    """Compute tool-usage metrics from the tool trace."""
    tool_trace = artifacts.get("tool_trace", [])
    total = len(tool_trace)

    generic_calls = sum(1 for t in tool_trace if t.get("tool") in _GENERIC_TOOLS)
    specialized_calls = total - generic_calls

    return {
        "web_search_calls": artifacts.get("web_search_calls", 0),
        "url_content_calls": artifacts.get("url_content_calls", 0),
        "total_tool_calls": total,
        "tools_used": artifacts.get("tools_used", []),
        "specialized_tool_use_ratio": round(_safe_div(specialized_calls, total), 4),
    }


def _fact_status_counts(knowledge: dict | None) -> dict[str, int]:
    """Count facts grouped by status."""
    counts = {"unknown": 0, "unverified": 0, "verified": 0, "contradicted": 0}
    if not knowledge:
        return counts
    for fact in knowledge.get("facts", []):
        status = fact.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _conclusion_status_counts(knowledge: dict | None) -> dict[str, int]:
    """Count conclusions grouped by status."""
    counts: dict[str, int] = {}
    if not knowledge:
        return counts
    for conc in knowledge.get("conclusions", []):
        status = conc.get("status", "unverified")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _hallucinated_fact_ids(knowledge: dict | None) -> list[str]:
    """Find conclusion supporting_fact_ids that don't exist in the facts list.

    This is the same graph-integrity check that ``research_review`` performs
    at runtime — a non-zero result means the synthesis model fabricated a
    reference to a fact that was never established.
    """
    if not knowledge:
        return []
    valid_ids = {f["id"] for f in knowledge.get("facts", []) if "id" in f}
    hallucinated: list[str] = []
    for conc in knowledge.get("conclusions", []):
        for fid in conc.get("supporting_fact_ids", []):
            if fid not in valid_ids:
                hallucinated.append(fid)
    return hallucinated


def _uncited_conclusion_count(knowledge: dict | None) -> int:
    """Count conclusions with an explicitly empty ``citation_ids`` list.

    Only counts when the ``citation_ids`` key is present but empty.  When the
    key is absent entirely (as in ``knowledge_summary`` output, which omits
    citation info from conclusions), we can't determine uncited status, so we
    skip rather than overcount.
    """
    if not knowledge:
        return 0
    count = 0
    for conc in knowledge.get("conclusions", []):
        if "citation_ids" in conc and not conc["citation_ids"]:
            count += 1
    return count


def _budget_metrics(artifacts: dict[str, Any]) -> dict[str, Any]:
    """Compute budget metrics."""
    consumed = artifacts.get("budget_consumed", 0) or 0
    limit = artifacts.get("budget_limit", 0) or 0
    return {
        "budget_consumed": round(consumed, 2),
        "budget_limit": round(limit, 2),
        "budget_consumed_ratio": round(_safe_div(consumed, limit), 4),
    }


def _retry_metrics(artifacts: dict[str, Any]) -> dict[str, Any]:
    """Compute retry counts from review/evaluation attempts and research steps.

    ``review_count`` and ``evaluation_count`` come from the structured
    outputs captured in step details (each retry appends a new outcome).
    ``research_count`` is derived from the steps_summary.
    """
    review_attempts = artifacts.get("review_attempts", [])
    evaluation_attempts = artifacts.get("evaluation_attempts", [])

    steps_summary = artifacts.get("steps_summary", [])
    research_count = sum(1 for s in steps_summary if s.get("node_name") == "research")

    return {
        "research_count": research_count,
        "review_count": len(review_attempts),
        "evaluation_count": len(evaluation_attempts),
    }


# ---------------------------------------------------------------------------
# Planner / researcher dimensions (Phase 5)
# ---------------------------------------------------------------------------

# Marker prefix of the synthetic tool result emitted when the Phase 4a query
# dedup guard intercepts a near-duplicate web_search call.  Rejected calls
# still appear in the tool trace (tool=web_search, success=False).
_DUPE_REJECT_PREFIX = "Query rejected as a duplicate"


def _targeted_fact_ids(planning_attempts: list[dict[str, Any]]) -> set[str]:
    """Union of fact IDs referenced by any evidence request across all
    planning passes."""
    targeted: set[str] = set()
    for attempt in planning_attempts:
        for request in attempt.get("evidence_requests", []):
            targeted.update(fid for fid in request.get("target_fact_ids", []) if fid)
    return targeted


def _planner_metrics(
    planning_attempts: list[dict[str, Any]], knowledge: dict | None
) -> dict[str, Any]:
    """Planner-dimension metrics: did planning produce good evidence requests?

    Coverage (every unknown fact targeted by >=1 request), granularity
    (facts-per-request distribution; the Phase 2 guard splits bundles so
    >1 should be rare), and preference quality (heuristic: a domain tool
    listed before web_search signals a real source hypothesis rather than
    defaulting to search).
    """
    requests = [
        request
        for attempt in planning_attempts
        for request in attempt.get("evidence_requests", [])
    ]
    fact_counts = [len(r.get("target_fact_ids", [])) for r in requests]
    targeted = _targeted_fact_ids(planning_attempts)

    unknown_ids = set()
    if knowledge:
        unknown_ids = {f["id"] for f in knowledge.get("facts", []) if f.get("status") == "unknown"}

    domain_first = sum(
        1
        for r in requests
        if (r.get("candidate_tools") or [None])[0] not in _GENERIC_TOOLS
        and (r.get("candidate_tools") or [None])[0] is not None
    )

    return {
        "evidence_request_count": len(requests),
        "avg_facts_per_request": round(_safe_div(sum(fact_counts), len(fact_counts)), 3),
        "max_facts_per_request": max(fact_counts, default=0),
        "multi_fact_request_count": sum(1 for n in fact_counts if n > 1),
        "distinct_targeted_fact_count": len(targeted),
        "domain_first_request_share": round(_safe_div(domain_first, len(requests)), 4),
        "unknown_facts_total": len(unknown_ids),
        "unknown_facts_targeted": len(unknown_ids & targeted),
        "unknown_facts_never_targeted": len(unknown_ids - targeted),
    }


def _researcher_metrics(artifacts: dict[str, Any], knowledge: dict | None) -> dict[str, Any]:
    """Researcher-dimension metrics: did research execute the requests well?

    Resolution rate is over *targeted* facts (planner reachability), not all
    facts — untargeted unknowns are planner misses, counted on the planner
    dimension.  ``verified_facts_per_search`` divides by EXECUTED web_search
    calls only: duplicate-intercepted calls did no retrieval work, so
    counting them would understate efficiency.
    """
    tool_trace = artifacts.get("tool_trace", [])
    executed_searches = sum(
        1 for t in tool_trace if t.get("tool") == "web_search" and t.get("success")
    )
    rejected_dupes = sum(
        1
        for t in tool_trace
        if t.get("tool") == "web_search"
        and not t.get("success")
        and (t.get("output_preview") or "").startswith(_DUPE_REJECT_PREFIX)
    )

    verified_ids = set()
    if knowledge:
        verified_ids = {
            f["id"] for f in knowledge.get("facts", []) if f.get("status") == "verified"
        }
    targeted = _targeted_fact_ids(artifacts.get("planning_attempts", []))

    research_passes = artifacts.get("research_passes", [])
    stalled = [p for p in research_passes if p.get("stalled") is True]

    return {
        "verified_facts_per_search": round(_safe_div(len(verified_ids), executed_searches), 4),
        "executed_web_search_calls": executed_searches,
        "duplicate_queries_intercepted": rejected_dupes,
        "targeted_fact_resolution_rate": round(
            _safe_div(len(verified_ids & targeted), len(targeted)), 4
        ),
        "stalled_research_pass_count": len(stalled),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_metrics(artifacts: dict) -> dict:
    """Compute all deterministic metrics from captured artifacts.

    Args:
        artifacts: The dict returned by
            :func:`evaluation.capture.capture_artifacts`.

    Returns:
        Dict of metrics.  All values are JSON-serializable (ints, floats,
        strings, lists).
    """
    knowledge = artifacts.get("knowledge")

    fact_counts = _fact_status_counts(knowledge)
    conclusion_counts = _conclusion_status_counts(knowledge)
    hallucinated_ids = _hallucinated_fact_ids(knowledge)

    metrics: dict[str, Any] = {}
    metrics.update(_tool_metrics(artifacts))
    metrics.update(
        {
            "unknown_fact_count": fact_counts["unknown"],
            "unverified_fact_count": fact_counts["unverified"],
            "verified_fact_count": fact_counts["verified"],
            "contradicted_fact_count": fact_counts["contradicted"],
            "unsupported_conclusion_count": conclusion_counts.get("unsupported", 0),
            "verified_conclusion_count": conclusion_counts.get("verified", 0),
            "contradicted_conclusion_count": conclusion_counts.get("contradicted", 0),
            "hallucinated_fact_id_count": len(hallucinated_ids),
            "hallucinated_fact_ids": hallucinated_ids,
            "uncited_conclusion_count": _uncited_conclusion_count(knowledge),
        }
    )
    metrics.update(_budget_metrics(artifacts))
    metrics.update(_retry_metrics(artifacts))
    metrics.update(_planner_metrics(artifacts.get("planning_attempts", []), knowledge))
    metrics.update(_researcher_metrics(artifacts, knowledge))
    return metrics
