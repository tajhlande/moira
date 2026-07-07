"""Evaluation orchestrator CLI.

Wires capture → metrics → print.  Later iterations extend this with judge
scoring, result-file storage, and batch modes.

Usage::

    uv run python -m moira_eval.run --db backend/moira.db --run-id <uuid>
    uv run python -m moira_eval.run --db backend/moira.db  # latest run
"""

import argparse
import sys
from pathlib import Path

from moira_eval.capture import capture_artifacts
from moira_eval.metrics import compute_metrics


def _format_summary(run_id: str, metrics: dict) -> str:
    """Format a one-line summary for terminal output."""
    return (
        f"run {run_id}: "
        f"web_search={metrics['web_search_calls']} | "
        f"total_tools={metrics['total_tool_calls']} | "
        f"unknown_facts={metrics['unknown_fact_count']} | "
        f"unsupported={metrics['unsupported_conclusion_count']} | "
        f"halluc_ids={metrics['hallucinated_fact_id_count']} | "
        f"budget={metrics['budget_consumed']:.0f}/{metrics['budget_limit']:.0f} "
        f"({metrics['budget_consumed_ratio'] * 100:.0f}%)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Mechanical metrics for a MOiRA workflow run.")
    parser.add_argument(
        "--db",
        required=True,
        help="Path to the SQLite database (e.g., backend/moira.db)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Run ID to evaluate. If omitted, uses the latest completed run.",
    )
    args = parser.parse_args()

    db_path = args.db
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    artifacts = capture_artifacts(db_path, args.run_id)
    if "error" in artifacts:
        print(artifacts["error"], file=sys.stderr)
        sys.exit(1)

    metrics = compute_metrics(artifacts)

    print(_format_summary(artifacts["run_id"], metrics))

    # Surface hallucinated IDs explicitly — highest-signal mechanical metric.
    if metrics["hallucinated_fact_ids"]:
        print(f"  hallucinated IDs: {', '.join(metrics['hallucinated_fact_ids'])}")

    # Surface retry counts if any verification retries occurred.
    if metrics["review_count"] > 1 or metrics["evaluation_count"] > 1:
        print(
            f"  retries: research={metrics['research_count']} "
            f"review={metrics['review_count']} eval={metrics['evaluation_count']}"
        )


if __name__ == "__main__":
    main()
