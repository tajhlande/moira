"""Batch-evaluate all predefined benchmark questions.

Finds the most recent completed run for each question in the predefined
question set, then runs the full evaluation pipeline (capture → metrics →
judge → save) for each.

Run-finding matches on the first 60 characters of the user message, so
minor trailing differences (typos, trailing whitespace) won't prevent a
match. If no run is found for a question, it is skipped with a warning.

Usage::

    uv run python -m moira_eval.batch \\
        --db backend/moira.db

    # Only evaluate specific questions
    uv run python -m moira_eval.batch \\
        --db backend/moira.db \\
        --only tyranitar-ou,telescope-mount-cost
"""

import argparse
import asyncio
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from moira_eval.capture import capture_artifacts
from moira_eval.judge import JudgeConfig, JudgeError, JudgeResult, judge_config_from_env
from moira_eval.metrics import compute_metrics
from moira_eval.questions import QUESTIONS, Question
from moira_eval.result import build_meta, build_result, save_meta, save_result
from moira_eval.run import _run_judge

# How many characters of the user message to match on. Enough to be
# distinctive, tolerant of trailing whitespace or minor edits.
_MATCH_PREFIX_LEN = 60


# ---------------------------------------------------------------------------
# Run finding
# ---------------------------------------------------------------------------


def find_run_for_question(db_path: str, question_text: str) -> str | None:
    """Find the most recent completed run matching the question text.

    Matches on the first ``_MATCH_PREFIX_LEN`` characters of the user
    message content to tolerate minor trailing differences. Returns the
    run ID or ``None`` if no match is found.
    """
    conn = sqlite3.connect(db_path)
    try:
        prefix = question_text[:_MATCH_PREFIX_LEN]
        row = conn.execute(
            """
            SELECT wr.id
            FROM workflow_runs wr
            JOIN messages m ON wr.user_message_id = m.id
            WHERE substr(m.content, 1, ?) = ?
              AND wr.status = 'completed'
            ORDER BY wr.started_at DESC
            LIMIT 1
            """,
            (_MATCH_PREFIX_LEN, prefix),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Single-question evaluation
# ---------------------------------------------------------------------------


@dataclass
class BatchEntry:
    """Result of evaluating one question in a batch run."""

    question_id: str
    run_id: str
    status: str  # "evaluated", "no_run", "error"
    judge_result: JudgeResult | None = None
    metrics: dict | None = None
    error: str = ""


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------


def _format_summary(entries: list[BatchEntry]) -> str:
    """Format a compact summary table of batch results."""
    lines: list[str] = []
    lines.append("")
    lines.append(
        f"{'Question':<30s}  {'Rubric':<8s}  {'Score':<12s}  "
        f"{'web_search':>10s}  {'unsupported':>11s}  Status"
    )
    lines.append("-" * 90)

    for e in entries:
        qid = e.question_id
        if e.status == "no_run":
            lines.append(
                f"{qid:<30s}  {'?':<8s}  {'—':<12s}  {'—':>10s}  {'—':>11s}  no run found"
            )
            continue

        if e.status == "error":
            lines.append(
                f"{qid:<30s}  {'?':<8s}  {'—':<12s}  {'—':>10s}  {'—':>11s}  ERROR: {e.error[:40]}"
            )
            continue

        rubric = "?"
        ws = "—"
        unsup = "—"
        if e.metrics:
            ws = str(e.metrics.get("web_search_calls", "—"))
            unsup = str(e.metrics.get("unsupported_conclusion_count", "—"))

        if e.judge_result is not None:
            jr = e.judge_result
            score_str = f"{jr.total}/{jr.max_total}"
            status = "PASS" if jr.passed else "FAIL"
            rubric = "pokemon" if jr.scale_max == 2 else "general"
        else:
            score_str = "metrics-only"
            status = "—"

        lines.append(
            f"{qid:<30s}  {rubric:<8s}  {score_str:<12s}  {ws:>10s}  {unsup:>11s}  {status}"
        )

    lines.append("-" * 90)
    evaluated = sum(1 for e in entries if e.status == "evaluated")
    no_run = sum(1 for e in entries if e.status == "no_run")
    errors = sum(1 for e in entries if e.status == "error")
    lines.append(f"Total: {evaluated} evaluated, {no_run} skipped (no run), {errors} error(s)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-evaluate all predefined benchmark questions.",
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Path to the SQLite database (e.g., backend/moira.db)",
    )
    parser.add_argument(
        "--only",
        default=None,
        help=(
            "Comma-separated question IDs to evaluate "
            "(e.g., tyranitar-ou,telescope-mount-cost). "
            "If omitted, evaluates all predefined questions."
        ),
    )
    args = parser.parse_args()

    db_path = args.db
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    # Determine which questions to evaluate.
    if args.only:
        requested = [s.strip() for s in args.only.split(",")]
        questions: list[Question] = []
        for qid in requested:
            q = QUESTIONS.get(qid)
            if q is None:
                available = ", ".join(sorted(QUESTIONS.keys()))
                print(
                    f"Unknown question ID '{qid}'. Available: {available}",
                    file=sys.stderr,
                )
                sys.exit(1)
            questions.append(q)
    else:
        questions = list(QUESTIONS.values())

    # Check judge configuration.
    judge_config = judge_config_from_env()
    if judge_config is None:
        print(
            "WARNING: judge env vars not set "
            "(MOIRA_EVAL_JUDGE_ENDPOINT, MOIRA_EVAL_JUDGE_MODEL). "
            "Will produce metrics-only results.",
            file=sys.stderr,
        )

    # Find matching runs and evaluate.
    entries: list[BatchEntry] = []
    for q in questions:
        run_id = find_run_for_question(db_path, q.text)
        if run_id is None:
            print(f"  {q.id}: no matching run found, skipping.", file=sys.stderr)
            entries.append(BatchEntry(question_id=q.id, run_id="", status="no_run"))
            continue

        print(f"  {q.id}: evaluating run {run_id[:12]}...", file=sys.stderr)

        try:
            entry = asyncio.run(_evaluate_one(db_path, run_id, q, judge_config))
        except Exception as exc:
            entry = BatchEntry(
                question_id=q.id,
                run_id=run_id,
                status="error",
                error=str(exc),
            )
        entries.append(entry)

        # Print per-question result.
        if entry.status == "evaluated" and entry.judge_result is not None:
            jr = entry.judge_result
            status = "PASS" if jr.passed else "FAIL"
            print(
                f"  {q.id}: {jr.total}/{jr.max_total} ({status})",
                file=sys.stderr,
            )
        elif entry.status == "error":
            print(f"  {q.id}: ERROR — {entry.error}", file=sys.stderr)

    # Print summary table.
    print(_format_summary(entries))

    # Exit non-zero if any errors.
    if any(e.status == "error" for e in entries):
        sys.exit(1)


async def _evaluate_one(
    db_path: str,
    run_id: str,
    question: Question,
    judge_config: JudgeConfig | None,
) -> BatchEntry:
    """Capture artifacts for a specific run_id, then evaluate.

    Runs the full pipeline: capture → metrics → judge → save.
    """
    artifacts = capture_artifacts(db_path, run_id)
    if "error" in artifacts:
        return BatchEntry(
            question_id=question.id,
            run_id=run_id,
            status="error",
            error=artifacts["error"],
        )

    metrics = compute_metrics(artifacts)

    judge_result: JudgeResult | None = None
    if judge_config is not None:
        try:
            judge_result = await _run_judge(
                judge_config,
                question.text,
                artifacts,
                metrics,
                question.rubric,
            )
        except JudgeError as exc:
            return BatchEntry(
                question_id=question.id,
                run_id=run_id,
                status="error",
                metrics=metrics,
                error=f"Judge error: {exc}",
            )

    result = build_result(
        question_id=question.id,
        question_text=question.text,
        artifacts=artifacts,
        metrics=metrics,
        judge_result=judge_result,
        rubric=question.rubric,
    )
    save_result(result)

    judge_model = getattr(judge_config, "model", "") if judge_config else ""
    meta = build_meta(commit_sha=result.commit_sha, judge_model=judge_model)
    save_meta(meta)

    return BatchEntry(
        question_id=question.id,
        run_id=run_id,
        status="evaluated",
        judge_result=judge_result,
        metrics=metrics,
    )


if __name__ == "__main__":
    main()
