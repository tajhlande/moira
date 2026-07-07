"""Artifact capture for evaluation.

Extracts the full tool-call trace, knowledge snapshot, review/evaluation
outputs, and report from a completed workflow run.  The returned dict is the
single input to :mod:`moira_eval.metrics`.

The capture layer reads directly from SQLite (no async repo) so it can run
offline against any database file without spinning up the application.

Usage::

    from moira_eval.capture import capture_artifacts
    artifacts = capture_artifacts("backend/moira.db", run_id="<uuid>")
"""

import json
import sqlite3
from typing import Any

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _get_run(conn: sqlite3.Connection, run_id: str | None) -> dict | None:
    """Fetch a workflow run by ID, or the latest completed run."""
    if run_id:
        row = conn.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM workflow_runs WHERE status = 'completed' "
            "ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def _get_steps(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """Fetch all workflow steps for a run, ordered by insertion (id)."""
    rows = conn.execute(
        "SELECT * FROM workflow_steps WHERE workflow_run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Detail parsing
# ---------------------------------------------------------------------------


def _parse_detail(step: dict) -> dict | None:
    """Parse the ``detail`` JSON column from a step row."""
    detail = step.get("detail")
    if not detail:
        return None
    if isinstance(detail, str):
        try:
            return json.loads(detail)
        except json.JSONDecodeError:
            return None
    if isinstance(detail, dict):
        return detail
    return None


# ---------------------------------------------------------------------------
# Tool extraction
# ---------------------------------------------------------------------------


def _count_tool_calls(steps: list[dict], tool_name: str) -> int:
    """Count how many times a specific tool was called across all steps."""
    count = 0
    for step in steps:
        detail = _parse_detail(step)
        if not detail:
            continue
        for tr in detail.get("tool_results", []):
            if tr.get("tool") == tool_name:
                count += 1
    return count


def _extract_tool_trace(steps: list[dict]) -> list[dict]:
    """Extract all tool calls across all steps with context."""
    trace: list[dict] = []
    for step in steps:
        detail = _parse_detail(step)
        if not detail:
            continue
        for tr in detail.get("tool_results", []):
            trace.append(
                {
                    "step_node": step.get("node_name", ""),
                    "step_label": step.get("label", ""),
                    "tool": tr.get("tool", ""),
                    "args": tr.get("args"),
                    "output_preview": (tr.get("result", "")[:500] if tr.get("result") else ""),
                    "duration_ms": tr.get("duration_ms", 0),
                    "success": tr.get("success", False),
                }
            )
    return trace


# ---------------------------------------------------------------------------
# Review / evaluation extraction
# ---------------------------------------------------------------------------


def _extract_review_attempts(steps: list[dict]) -> list[dict[str, Any]]:
    """Extract all ``research_review`` attempts from step details.

    Each attempt is the ``structured_output`` (a ``ReviewOutcome`` dict) from
    a ``research_review`` step.  Multiple attempts occur when review routes
    to ``retry``.
    """
    attempts: list[dict[str, Any]] = []
    for step in steps:
        if step.get("node_name") != "research_review":
            continue
        detail = _parse_detail(step)
        if not detail:
            continue
        structured = detail.get("structured_output")
        if not structured:
            continue
        attempts.append(structured)
    return attempts


def _extract_evaluation_attempts(steps: list[dict]) -> list[dict[str, Any]]:
    """Extract all ``evaluation`` attempts from step details.

    Each attempt is the ``structured_output`` (an ``EvaluationOutcome`` dict)
    from an ``evaluation`` step.
    """
    attempts: list[dict[str, Any]] = []
    for step in steps:
        if step.get("node_name") != "evaluation":
            continue
        detail = _parse_detail(step)
        if not detail:
            continue
        structured = detail.get("structured_output")
        if not structured:
            continue
        attempts.append(structured)
    return attempts


# ---------------------------------------------------------------------------
# Knowledge snapshot + report
# ---------------------------------------------------------------------------


def _normalize_knowledge(knowledge: dict) -> dict:
    """Normalize the knowledge snapshot into flat lists for metric computation.

    The snapshot stored in the DB is produced by ``knowledge_summary()``,
    which groups facts by subject and conclusions by status (dicts of lists,
    not flat lists).  This flattens them back so the rest of the pipeline
    works with a uniform shape.

    Summary conclusions also omit ``citation_ids``, so
    ``uncited_conclusion_count`` will not be accurate from summary data —
    see :func:`moira_eval.metrics._uncited_conclusion_count` for how that's
    handled.
    """
    facts_raw = knowledge.get("facts", [])
    if isinstance(facts_raw, dict):
        # Summary shape: {subject: [fact_dicts, ...]}
        flat_facts: list[dict] = []
        for group in facts_raw.values():
            if isinstance(group, list):
                flat_facts.extend(f for f in group if isinstance(f, dict))
        knowledge["facts"] = flat_facts

    conclusions_raw = knowledge.get("conclusions", [])
    if isinstance(conclusions_raw, dict):
        # Summary shape: {status: [conclusion_dicts, ...]}
        flat_conclusions: list[dict] = []
        for group in conclusions_raw.values():
            if isinstance(group, list):
                flat_conclusions.extend(c for c in group if isinstance(c, dict))
        knowledge["conclusions"] = flat_conclusions

    return knowledge


def _extract_knowledge(run: dict) -> dict | None:
    """Parse ``workflow_runs.knowledge_snapshot`` JSON.

    Returns the ``Knowledge`` dict (question, user_goal, topic, entities,
    concepts, facts, conclusions, citations) with facts and conclusions
    normalized to flat lists, or ``None`` if the snapshot is empty or
    unparseable.
    """
    raw = run.get("knowledge_snapshot")
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            knowledge = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    elif isinstance(raw, dict):
        knowledge = raw
    else:
        return None

    if not isinstance(knowledge, dict):
        return None
    return _normalize_knowledge(knowledge)


def _extract_report(run: dict) -> dict | None:
    """Extract the final report from the run."""
    report = run.get("report")
    if not report:
        return None
    if isinstance(report, str):
        try:
            return json.loads(report)
        except json.JSONDecodeError:
            return None
    if isinstance(report, dict):
        return report
    return None


def _extract_critiques(report: dict | None) -> list[str]:
    """Extract the ``critiques`` list from the report, if present."""
    if not report:
        return []
    critiques = report.get("critiques", [])
    if not isinstance(critiques, list):
        return []
    return [str(c) for c in critiques]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def capture_artifacts(db_path: str, run_id: str | None = None) -> dict:
    """Capture all artifacts for a workflow run.

    Reads directly from the SQLite database — no application services
    required.  The returned dict is the input to
    :func:`evaluation.metrics.compute_metrics`.

    Args:
        db_path: Path to the SQLite database file.
        run_id: Run ID to capture.  If ``None``, captures the latest
            completed run.

    Returns:
        Dict with keys: ``run_id``, ``conversation_id``, ``status``,
        ``knowledge``, ``report``, ``critiques``, ``review_attempts``,
        ``evaluation_attempts``, ``tool_trace``, ``tools_used``,
        ``web_search_calls``, ``url_content_calls``, ``total_tool_calls``,
        ``budget_limit``, ``budget_consumed``, ``steps_summary``.
        Returns ``{"error": ...}`` if the run is not found.
    """
    conn = _get_connection(db_path)
    try:
        run = _get_run(conn, run_id)
        if not run:
            return {"error": f"No run found for id={run_id}"}

        actual_run_id = run["id"]
        steps = _get_steps(conn, actual_run_id)

        tool_trace = _extract_tool_trace(steps)
        report = _extract_report(run)
        knowledge = _extract_knowledge(run)

        return {
            "run_id": actual_run_id,
            "conversation_id": run.get("conversation_id", ""),
            "status": run.get("status", ""),
            "started_at": run.get("started_at", ""),
            "completed_at": run.get("completed_at", ""),
            "total_elapsed_ms": run.get("total_elapsed_ms", 0),
            "budget_limit": run.get("budget_limit", 0),
            # total_cost column holds the budget consumed (total inference +
            # tool cost), not just the model cost.  See active_run.py:748
            # where budget_consumed is persisted as total_cost.
            "budget_consumed": run.get("total_cost", 0),
            "generation_reason": (report or {}).get("generation_reason", ""),
            "total_tool_calls": len(tool_trace),
            "web_search_calls": _count_tool_calls(steps, "web_search"),
            "url_content_calls": _count_tool_calls(steps, "url_content"),
            "tools_used": sorted({t["tool"] for t in tool_trace}),
            "tool_trace": tool_trace,
            "knowledge": knowledge,
            "review_attempts": _extract_review_attempts(steps),
            "evaluation_attempts": _extract_evaluation_attempts(steps),
            "report": report,
            "critiques": _extract_critiques(report),
            "steps_summary": [
                {
                    "node_name": s.get("node_name", ""),
                    "label": s.get("label", ""),
                    "status": s.get("status", ""),
                    "cost": s.get("cost", 0),
                    "tool_call_count": s.get("tool_call_count", 0),
                    "elapsed_ms": s.get("elapsed_ms", 0),
                    "model": s.get("model", ""),
                    "error": s.get("error", ""),
                }
                for s in steps
            ],
        }
    finally:
        conn.close()
