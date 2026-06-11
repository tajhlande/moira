"""Artifact capture for research loop evaluation.

Extracts and saves the full tool-call trace, search counts, verification
output, citations, and report from a completed workflow run to a JSON file
for manual scoring.

Usage:
    uv run python -m evaluation.capture <db_path> <run_id> [--output artifacts/<run_id>.json]

If no run_id is provided, captures the most recent completed run.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _get_run(conn: sqlite3.Connection, run_id: str | None) -> dict | None:
    """Fetch a workflow run by ID, or the latest completed run."""
    if run_id:
        row = conn.execute(
            "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM workflow_runs WHERE status = 'completed' "
            "ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def _get_steps(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """Fetch all workflow steps for a run."""
    rows = conn.execute(
        "SELECT * FROM workflow_steps WHERE workflow_run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _count_tool_calls(steps: list[dict], tool_name: str) -> int:
    """Count how many times a specific tool was called across all steps."""
    count = 0
    for step in steps:
        detail = step.get("detail")
        if not detail:
            continue
        if isinstance(detail, str):
            detail = json.loads(detail)
        tool_results = detail.get("tool_results", [])
        for tr in tool_results:
            if tr.get("tool") == tool_name:
                count += 1
    return count


def _extract_tool_trace(steps: list[dict]) -> list[dict]:
    """Extract all tool calls across all steps with context."""
    trace = []
    for step in steps:
        detail = step.get("detail")
        if not detail:
            continue
        if isinstance(detail, str):
            detail = json.loads(detail)
        tool_results = detail.get("tool_results", [])
        for tr in tool_results:
            trace.append(
                {
                    "step_node": step.get("node_name", ""),
                    "step_label": step.get("label", ""),
                    "tool": tr.get("tool", ""),
                    "args": tr.get("args"),
                    "output_preview": (
                        tr.get("result", "")[:500] if tr.get("result") else ""
                    ),
                    "duration_ms": tr.get("duration_ms", 0),
                    "success": tr.get("success", False),
                }
            )
    return trace


def _extract_verification_output(steps: list[dict]) -> dict:
    """Extract verification step details."""
    for step in steps:
        if step.get("node_name") != "verification":
            continue
        detail = step.get("detail")
        if not detail:
            continue
        if isinstance(detail, str):
            detail = json.loads(detail)
        return {
            "step_status": step.get("status", ""),
            "cost": step.get("cost", 0),
            "tool_call_count": step.get("tool_call_count", 0),
            "detail": detail,
        }
    return {}


def _extract_report(run: dict) -> dict | None:
    """Extract the final report from the run."""
    report = run.get("report")
    if not report:
        return None
    if isinstance(report, str):
        report = json.loads(report)
    return report


def capture_artifacts(db_path: str, run_id: str | None = None) -> dict:
    """Capture all artifacts for a workflow run and return as dict."""
    conn = _get_connection(db_path)
    try:
        run = _get_run(conn, run_id)
        if not run:
            return {"error": f"No run found for id={run_id}"}

        actual_run_id = run["id"]
        steps = _get_steps(conn, actual_run_id)

        tool_trace = _extract_tool_trace(steps)
        total_tool_calls = len(tool_trace)
        web_search_calls = _count_tool_calls(steps, "web_search")
        url_content_calls = _count_tool_calls(steps, "url_content")

        all_tools_used = sorted({t["tool"] for t in tool_trace})

        report = _extract_report(run)

        return {
            "run_id": actual_run_id,
            "conversation_id": run.get("conversation_id", ""),
            "status": run.get("status", ""),
            "started_at": run.get("started_at", ""),
            "completed_at": run.get("completed_at", ""),
            "total_elapsed_ms": run.get("total_elapsed_ms", 0),
            "budget_limit": run.get("budget_limit", 0),
            "budget_consumed": run.get("budget_consumed", 0),
            "generation_path": (report or {}).get("generation_path", ""),
            "total_tool_calls": total_tool_calls,
            "web_search_calls": web_search_calls,
            "url_content_calls": url_content_calls,
            "tools_used": all_tools_used,
            "tool_trace": tool_trace,
            "verification": _extract_verification_output(steps),
            "report": report,
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


def main():
    parser = argparse.ArgumentParser(
        description="Capture artifacts from a MOiRA workflow run for evaluation."
    )
    parser.add_argument(
        "db_path",
        help="Path to the SQLite database (e.g., data/moira.db)",
    )
    parser.add_argument(
        "run_id",
        nargs="?",
        default=None,
        help="Run ID to capture. If omitted, captures the latest completed run.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output file path. Defaults to artifacts/<run_id>.json",
    )
    args = parser.parse_args()

    db_path = args.db_path
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    artifacts = capture_artifacts(db_path, args.run_id)

    if "error" in artifacts:
        print(artifacts["error"], file=sys.stderr)
        sys.exit(1)

    run_id = artifacts["run_id"]
    output_path = args.output or f"artifacts/{run_id}.json"
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w") as f:
        json.dump(artifacts, f, indent=2, default=str)

    print(f"Artifacts captured for run {run_id}")
    print(f"  Total tool calls: {artifacts['total_tool_calls']}")
    print(f"  web_search calls: {artifacts['web_search_calls']}")
    print(f"  Tools used: {', '.join(artifacts['tools_used'])}")
    print(f"  Budget consumed: {artifacts['budget_consumed']:.1f}/{artifacts['budget_limit']:.1f}")
    print(f"  Generation path: {artifacts['generation_path']}")
    print(f"  Saved to: {output_file}")


if __name__ == "__main__":
    main()
