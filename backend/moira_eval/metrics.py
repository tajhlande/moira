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

from statistics import mean, stdev
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


# ---------------------------------------------------------------------------
# Retrieval-harness metrics (Phase 1 of retrieval-quality-plan.md)
#
# These operate on harness repeat artifacts — a different input shape than
# capture_artifacts output. Each repeat carries "fact_scores"
# ({fact_id: {present, found_at_k, with_pages, queries, ...}}) plus the
# per-run counts, produced by moira_eval.retrieval_harness.
# ---------------------------------------------------------------------------


def harness_per_fact_recall(repeat: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-fact recall rows for one harness repeat.

    Each row joins the fact's identity with its score: recall@k is the
    indicator that found_at_k <= k. Facts that were never queried are
    included — they are retrieval failures too.

    "original" marks facts produced by decomposition (the planning
    targets). Research's overflow-split path spawns additional facts that
    are never queried; they are kept in the rows but must be separated
    before reading recall — spawn deflates the denominator. When the
    repeat lacks "original_fact_ids" (older artifacts), every fact is
    treated as original.

    "origin" carries the fact's creation provenance when present
    ("decomposition" / "overflow" / "discovered"; "" on pre-field
    artifacts) — the discovered population is the research-agency
    signal. "present_any"/"found_at_k_any"/"recall_at_{k}_any" score the
    union of attributed AND unattributed query material (coverage_any);
    the un-suffixed fields stay attributed-only for comparability.
    """
    facts = repeat.get("facts", [])
    scores = repeat.get("fact_scores", {})
    original_ids = set(repeat.get("original_fact_ids") or []) or {f["id"] for f in facts}
    rows = []
    for fact in facts:
        score = scores.get(fact["id"], {})
        found_at_k = score.get("found_at_k")
        found_at_k_any = score.get("found_at_k_any")
        rows.append(
            {
                "id": fact["id"],
                "subject": fact.get("subject", ""),
                "fact_needed": fact.get("fact_needed", ""),
                "original": fact["id"] in original_ids,
                "origin": fact.get("origin", ""),
                "queries": score.get("queries", 0),
                "present": bool(score.get("present")),
                "present_any": bool(score.get("present_any")),
                "found_at_k": found_at_k,
                "found_at_k_any": found_at_k_any,
                **{
                    f"recall_at_{k}": found_at_k is not None and found_at_k <= k for k in (1, 3, 5)
                },
                **{
                    f"recall_at_{k}_any": found_at_k_any is not None and found_at_k_any <= k
                    for k in (1, 3, 5)
                },
                "with_pages": bool(score.get("with_pages")),
                "never_queried": bool(score.get("never_queried")),
                "quote": score.get("quote", ""),
            }
        )
    return rows


def _mean_sd(values: list[float]) -> dict[str, float]:
    """Mean and sample stdev (0.0 when fewer than two values)."""
    if not values:
        return {"mean": 0.0, "sd": 0.0}
    sd = stdev(values) if len(values) > 1 else 0.0
    return {"mean": round(mean(values), 4), "sd": round(sd, 4)}


def harness_recall_summary(repeats: list[dict[str, Any]], ks: tuple[int, ...] = (1, 3, 5)) -> dict:
    """Aggregate recall metrics across harness repeats.

    THE DEFINITIVE LIST OF SUMMARY FIELDS (each a {mean, sd} over repeats):

    - recall_at_{k}: fraction of ALL facts whose needed information was
      found at rank <= k (spawn included) — the honest bottom line.
    - recall_at_{k}_original: same, restricted to decomposition-produced
      facts (the planning targets) — the headline number.
    - recall_at_{k}_queried: same, restricted to facts that received at
      least one attributed query — retrieval effectiveness isolated from
      planning-coverage failures.
    - recall_with_pages: fraction of original facts found only in fetched
      page bodies (url_content), not in search snippets — the page-rescue
      rate.
    - queries_per_resolved_fact: mean attributed queries among facts that
      resolved (present) — measures fan-out (1.0 = single-shot lottery).
    - coverage: fraction of original facts that received >= 1 query —
      planning's fact-coverage discipline.
    - coverage_any / coverage_any_at_{k}: fraction of original facts
      whose needed information appeared in ANY executed query's material
      (attributed or not) — the attribution-independent ceiling on
      coverage. The gap to `coverage`/recall is loss to attribution
      discipline, not to retrieval.
    - unresolved_fact_count: facts (all) whose needed information was not
      found.
    - unresolved_original_fact_count: same, original facts only.
    - never_queried_fact_count: facts (all) with zero attributed queries —
      a separate failure mode from searched-and-missed.
    - spawned_fact_count: facts not produced by decomposition (overflow
      splits + model-discovered; the pre-origin lumped definition).
    - discovered_fact_count / overflow_fact_count: origin-split counts
      (""-origin facts from legacy artifacts count in neither).
    - recall_at_{k}_discovered: recall among model-discovered facts,
      scored on union material (present_any) — the research-agency
      retrieval signal. None when no repeat produced discovered facts.
    - discovered_resolution: fraction of discovered facts resolved
      (present_any). None when no discovered facts.
    - resolved_share_beyond_decomposition: among all facts resolved on
      union material, the share NOT produced by decomposition — the
      headline agency metric. None when nothing resolved.
    - research_rounds / research_exhausted_rate / research_stalled_rate:
      final research pass loop outcome — turns used, share of repeats
      stopped by the round cap, share whose last pass produced no new
      claims. Missing research_loop → repeat skipped.
    - budget_unspent_share: unspent budget fraction at run end — early
      stop discipline.
    - unattributed_web_search_calls / unattributed_web_search_share:
      searches that served no evidence request, count and share of all
      web_search calls.
    - facts_per_run: total facts in the final state.
    - web_search_calls / url_content_calls: recorded tool calls per run.
    - url_content_failures: fetches per run that failed outright (blocked
      hosts, timeouts, oversized responses) — these consume call budget,
      produce no content, and structurally cap the page-rescue rate.

    Three populations per repeat, because research's overflow-split path
    spawns facts that are never queried: all / original / queried as
    described above. Plus the companion metrics the plan gates on.
    Discovered-population fields skip repeats with empty denominators
    (mean over the repeats that produced the population, None when none
    did) so an empty population never reads as a zero rate.
    """
    summary: dict[str, Any] = {}
    all_rows = [harness_per_fact_recall(repeat) for repeat in repeats]

    def frac(rows: list[dict[str, Any]], pred, denominator_pred=None) -> float:
        """Fraction of rows matching pred. Denominator defaults to all rows;
        pass denominator_pred to restrict the population."""
        denom = rows if denominator_pred is None else [r for r in rows if denominator_pred(r)]
        if not denom:
            return 0.0
        return _safe_div(sum(1 for r in denom if pred(r)), len(denom))

    def frac_nonempty(rows, pred, denominator_pred) -> float | None:
        """frac() over a restricted population, but None when the population
        is empty — an absent denominator is "no such facts this repeat",
        not a zero rate."""
        denom = [r for r in rows if denominator_pred(r)]
        if not denom:
            return None
        return _safe_div(sum(1 for r in denom if pred(r)), len(denom))

    def _mean_sd_optional(values: list[float | None]) -> dict[str, float | None]:
        """_mean_sd over the non-None values; {None, None} when all were
        None (population never occurred)."""
        present = [v for v in values if v is not None]
        if not present:
            return {"mean": None, "sd": None}
        return _mean_sd(present)

    for k in ks:
        key = f"recall_at_{k}"
        summary[key] = _mean_sd([frac(rows, lambda r: r[key]) for rows in all_rows])
        summary[f"{key}_original"] = _mean_sd(
            [frac(rows, lambda r: r[key], lambda r: r["original"]) for rows in all_rows]
        )
        summary[f"{key}_queried"] = _mean_sd(
            [frac(rows, lambda r: r[key], lambda r: r["queries"] > 0) for rows in all_rows]
        )

    pages_per_repeat = [
        frac(rows, lambda r: r["with_pages"], lambda r: r["original"]) for rows in all_rows
    ]
    summary["recall_with_pages"] = _mean_sd(pages_per_repeat)

    queries_resolved: list[float] = []
    for rows in all_rows:
        resolved_queries = [r["queries"] for r in rows if r["present"]]
        queries_resolved.append(mean(resolved_queries) if resolved_queries else 0.0)
    summary["queries_per_resolved_fact"] = _mean_sd(queries_resolved)

    summary["coverage"] = _mean_sd(
        [frac(rows, lambda r: r["queries"] > 0, lambda r: r["original"]) for rows in all_rows]
    )
    # coverage_any: union-material presence, original population.
    summary["coverage_any"] = _mean_sd(
        [frac(rows, lambda r: r["present_any"], lambda r: r["original"]) for rows in all_rows]
    )
    for k in ks:
        summary[f"coverage_any_at_{k}"] = _mean_sd(
            [
                frac(rows, lambda r: r[f"recall_at_{k}_any"], lambda r: r["original"])
                for rows in all_rows
            ]
        )
    summary["unresolved_fact_count"] = _mean_sd(
        [sum(1 for r in rows if not r["present"]) for rows in all_rows]
    )
    summary["unresolved_original_fact_count"] = _mean_sd(
        [sum(1 for r in rows if r["original"] and not r["present"]) for rows in all_rows]
    )
    summary["never_queried_fact_count"] = _mean_sd(
        [sum(1 for r in rows if r["never_queried"]) for rows in all_rows]
    )
    summary["spawned_fact_count"] = _mean_sd(
        [sum(1 for r in rows if not r["original"]) for rows in all_rows]
    )
    summary["discovered_fact_count"] = _mean_sd(
        [sum(1 for r in rows if r["origin"] == "discovered") for rows in all_rows]
    )
    summary["overflow_fact_count"] = _mean_sd(
        [sum(1 for r in rows if r["origin"] == "overflow") for rows in all_rows]
    )
    for k in ks:
        summary[f"recall_at_{k}_discovered"] = _mean_sd_optional(
            [
                frac_nonempty(
                    rows, lambda r: r[f"recall_at_{k}_any"], lambda r: r["origin"] == "discovered"
                )
                for rows in all_rows
            ]
        )
    summary["discovered_resolution"] = _mean_sd_optional(
        [
            frac_nonempty(rows, lambda r: r["present_any"], lambda r: r["origin"] == "discovered")
            for rows in all_rows
        ]
    )
    summary["resolved_share_beyond_decomposition"] = _mean_sd_optional(
        [
            frac_nonempty(rows, lambda r: not r["original"], lambda r: r["present_any"])
            for rows in all_rows
        ]
    )
    # Loop outcome of the final research pass (None on artifacts whose
    # graph never surfaced research_loop — legacy canned artifacts).
    loops = [rep.get("research_loop") or {} for rep in repeats]
    summary["research_rounds"] = _mean_sd_optional(
        [float(loop["rounds"]) for loop in loops if loop.get("rounds") is not None]
    )
    summary["research_exhausted_rate"] = _mean_sd_optional(
        [1.0 if loop.get("exhausted_rounds") else 0.0 for loop in loops if loop]
    )
    summary["research_stalled_rate"] = _mean_sd_optional(
        [1.0 if loop.get("stalled") else 0.0 for loop in loops if loop]
    )
    unspent = []
    for rep in repeats:
        limit = rep.get("budget_limit", 0.0)
        if limit and limit > 0:
            unspent.append((limit - rep.get("budget_consumed", 0.0)) / limit)
    summary["budget_unspent_share"] = _mean_sd_optional(unspent)
    summary["unattributed_web_search_calls"] = _mean_sd(
        [rep.get("counts", {}).get("unattributed_web_search_calls", 0) for rep in repeats]
    )
    summary["unattributed_web_search_share"] = _mean_sd(
        [
            _safe_div(
                rep.get("counts", {}).get("unattributed_web_search_calls", 0),
                rep.get("counts", {}).get("web_search_calls", 0),
            )
            for rep in repeats
        ]
    )
    summary["facts_per_run"] = _mean_sd([len(rep.get("facts", [])) for rep in repeats])
    summary["web_search_calls"] = _mean_sd(
        [rep.get("counts", {}).get("web_search_calls", 0) for rep in repeats]
    )
    summary["url_content_calls"] = _mean_sd(
        [rep.get("counts", {}).get("url_content_calls", 0) for rep in repeats]
    )
    summary["url_content_failures"] = _mean_sd(
        [rep.get("counts", {}).get("url_content_failures", 0) for rep in repeats]
    )
    return summary
