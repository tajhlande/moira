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


def _get_attempt_group(conn: sqlite3.Connection, run: dict) -> list[dict]:
    """Fetch every workflow_runs row sharing the run's ``user_message_id``.

    A resume (UI "Retry" or checkpoint resume) creates a NEW workflow_runs
    row for the remaining nodes; the earlier attempt keeps the steps that
    already executed (e.g. all of research). The run group is therefore the
    unit that describes one logical pipeline execution — the same coalescing
    the API applies for the conversation UI (see
    ``api/routes/conversations.py::`` ``_coalesced_run_snapshot``).

    Errored siblings are included on purpose: their steps carry real work
    (research, tool calls) that the completed attempt's own step list lacks.
    Ordered by ``(started_at, id)`` so attempt chronology is preserved.
    """
    rows = conn.execute(
        "SELECT * FROM workflow_runs WHERE user_message_id = ? ORDER BY started_at, id",
        (run.get("user_message_id"),),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_steps_for_runs(conn: sqlite3.Connection, run_ids: list[str]) -> list[dict]:
    """Fetch all workflow steps for several runs, ordered by step id.

    Step ids are a global autoincrement, so ordering by id yields exact
    chronology across attempts (later attempts were inserted later).
    """
    if not run_ids:
        return []
    placeholders = ",".join("?" * len(run_ids))
    rows = conn.execute(
        f"SELECT * FROM workflow_steps WHERE workflow_run_id IN ({placeholders}) ORDER BY id",
        run_ids,
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
            # Canonical persisted key is "result" (active_run's tool_result
            # handler); round-error payloads embed the node's own log, which
            # uses "output" instead. Same contract as step_detail.schema.json.
            preview = tr.get("result") or tr.get("output") or ""
            trace.append(
                {
                    "step_node": step.get("node_name", ""),
                    "step_label": step.get("label", ""),
                    "tool": tr.get("tool", ""),
                    "args": tr.get("args"),
                    "output_preview": preview[:500],
                    "duration_ms": tr.get("duration_ms", 0),
                    "success": tr.get("success", False),
                }
            )
    return trace


def _get_tool_catalog(conn: sqlite3.Connection) -> list[dict]:
    """Read the tool catalog from the ``tools`` table.

    Returns a list of ``{"name": ..., "description": ...}`` dicts for
    every enabled tool. This is included in the judge prompt so the
    judge knows what tools the agent actually had access to.
    """
    try:
        rows = conn.execute(
            "SELECT name, description FROM tools WHERE enabled = 1 ORDER BY name"
        ).fetchall()
    except sqlite3.OperationalError:
        # Table may not exist in older databases.
        return []
    return [{"name": r["name"], "description": r["description"]} for r in rows]


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


def _extract_planning_attempts(steps: list[dict]) -> list[dict[str, Any]]:
    """Extract all ``planning`` attempts from step details.

    Each attempt is the ``structured_output`` from a ``planning`` step, whose
    ``evidence_requests`` list feeds the planner-dimension metrics (coverage,
    granularity, tool preference).  Multiple attempts occur when review
    routes to ``retry`` (planning re-runs for the remaining unknowns).
    """
    attempts: list[dict[str, Any]] = []
    for step in steps:
        if step.get("node_name") != "planning":
            continue
        detail = _parse_detail(step)
        if not detail:
            continue
        structured = detail.get("structured_output")
        if not structured:
            continue
        attempts.append(structured)
    return attempts


def _extract_research_passes(steps: list[dict]) -> list[dict[str, Any]]:
    """Extract per-pass research outcome signals from step details.

    ``stalled``/``new_facts`` (the Phase 4b progress signal) only exist on
    runs from after that change — older steps yield ``None`` fields, which
    metrics treat as "not recorded" rather than False.
    """
    passes: list[dict[str, Any]] = []
    for step in steps:
        if step.get("node_name") != "research":
            continue
        detail = _parse_detail(step) or {}
        passes.append(
            {
                "stalled": detail.get("stalled"),
                "new_facts": detail.get("new_facts"),
                "rounds": detail.get("rounds"),
                "tool_calling_mode": detail.get("tool_calling_mode"),
            }
        )
    return passes


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


def _find_report_in_related_runs(conn: sqlite3.Connection, run: dict) -> dict | None:
    """Look for a non-null report in other completed runs sharing the same
    ``user_message_id``.

    When a run fails at ``report_generation`` and is resumed via the UI's
    "Retry" button, the resume creates a new run containing only the
    retried step. ``find_run_for_question`` picks the original run (with
    all the research data), but that run's ``report`` column is NULL
    because ``report_generation`` failed. The actual report lives in the
    resumed run. This function finds it by matching on ``user_message_id``
    so the judge can score the answer.
    """
    user_message_id = run.get("user_message_id")
    if not user_message_id:
        return None
    current_run_id = run.get("id")
    rows = conn.execute(
        """
        SELECT report FROM workflow_runs
        WHERE user_message_id = ?
          AND id != ?
          AND status = 'completed'
          AND report IS NOT NULL
          AND report != ''
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (user_message_id, current_run_id),
    ).fetchall()
    for row in rows:
        extracted = _extract_report(dict(row))
        if extracted:
            return extracted
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
        Dict with keys: ``run_id``, ``attempt_ids``, ``conversation_id``,
        ``status``, ``knowledge``, ``report``, ``critiques``,
        ``review_attempts``, ``evaluation_attempts``, ``planning_attempts``,
        ``research_passes``, ``tool_trace``, ``tools_used``,
        ``web_search_calls``, ``url_content_calls``, ``total_tool_calls``,
        ``budget_limit``, ``budget_consumed``, ``steps_summary``.
        Returns ``{"error": ...}`` if the run is not found.

    Resume semantics: a resumed run only contains the steps that executed
    after the resume. Steps (and thus tool traces) are coalesced across all
    attempts sharing the run's ``user_message_id`` — the same stitching the
    conversation UI applies — so metrics reflect the logical pipeline
    execution rather than the thin tail of the latest attempt. ``run_id`` is
    the selected (latest) attempt; ``attempt_ids`` lists every contributing
    run in chronological order.
    """
    conn = _get_connection(db_path)
    try:
        run = _get_run(conn, run_id)
        if not run:
            return {"error": f"No run found for id={run_id}"}

        actual_run_id = run["id"]
        group = _get_attempt_group(conn, run)
        steps = _get_steps_for_runs(conn, [r["id"] for r in group])

        tool_trace = _extract_tool_trace(steps)
        tool_catalog = _get_tool_catalog(conn)
        report = _extract_report(run)
        if not report:
            # The selected run may be an errored run that was resumed via
            # the UI's "Retry" button. The original run has all the
            # research data but no report (report_generation failed); the
            # resumed run has the report. Look for it in related runs so
            # the judge can score the answer.
            report = _find_report_in_related_runs(conn, run)
        knowledge = _extract_knowledge(run)
        if not knowledge:
            # Mirror the report patching: the latest attempt may carry no
            # snapshot (e.g. a thin resume row persisted without one), while
            # an earlier attempt holds the full state. Take the newest
            # sibling that has one.
            for attempt in reversed(group):
                knowledge = _extract_knowledge(attempt)
                if knowledge:
                    break

        return {
            "run_id": actual_run_id,
            "attempt_ids": [r["id"] for r in group],
            "conversation_id": run.get("conversation_id", ""),
            "status": run.get("status", ""),
            "started_at": run.get("started_at", ""),
            "completed_at": run.get("completed_at", ""),
            "total_elapsed_ms": run.get("total_elapsed_ms", 0),
            "budget_limit": run.get("budget_limit", 0),
            # total_cost column holds the budget consumed (total inference +
            # tool cost), not just the model cost.  See active_run.py:748
            # where budget_consumed is persisted as total_cost. Taken from
            # the latest attempt only — the same rule the UI's coalesced
            # snapshot uses for budget fields.
            "budget_consumed": run.get("total_cost", 0),
            "generation_reason": (report or {}).get("generation_reason", ""),
            "total_tool_calls": len(tool_trace),
            "web_search_calls": _count_tool_calls(steps, "web_search"),
            "url_content_calls": _count_tool_calls(steps, "url_content"),
            "tools_used": sorted({t["tool"] for t in tool_trace}),
            "tool_trace": tool_trace,
            "tool_catalog": tool_catalog,
            "knowledge": knowledge,
            "review_attempts": _extract_review_attempts(steps),
            "evaluation_attempts": _extract_evaluation_attempts(steps),
            "planning_attempts": _extract_planning_attempts(steps),
            "research_passes": _extract_research_passes(steps),
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
