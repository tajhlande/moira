"""Evaluation orchestrator CLI.

Wires capture -> metrics -> judge (optional) -> result storage.

- ``--question-id <id>`` resolves a predefined question and its rubric.
- Without ``--question-id``, the question text is derived from the
  knowledge snapshot and the general rubric is used (ad hoc mode).
- Judge scoring requires env vars; without them, falls back to metrics-only.

Usage::

    # predefined question (pokemon rubric)
    uv run python -m moira_eval.run --db backend/moira.db --run-id <uuid> \\
        --question-id tyranitar-ou

    # ad hoc run (general rubric, question from knowledge snapshot)
    uv run python -m moira_eval.run --db backend/moira.db --run-id <uuid>

    # metrics-only (no judge env vars)
    uv run python -m moira_eval.run --db backend/moira.db --run-id <uuid>
"""

import argparse
import asyncio
import sys
from pathlib import Path

from moira_eval.capture import capture_artifacts
from moira_eval.judge import (
    Judge,
    JudgeConfig,
    JudgeError,
    JudgeResult,
    judge_config_from_env,
)
from moira_eval.metrics import compute_metrics
from moira_eval.questions import get_question
from moira_eval.result import build_meta, build_result, save_meta, save_result


def _format_metrics_summary(run_id: str, metrics: dict) -> str:
    """Format the metrics-only one-line summary."""
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


def _format_judged_summary(
    question_id: str,
    judge_result: JudgeResult,
    metrics: dict,
) -> str:
    """Format the judged one-line summary with score."""
    status = "PASS" if judge_result.passed else "FAIL"
    return (
        f"{question_id}: {judge_result.total}/{judge_result.max_total} ({status}) | "
        f"web_search={metrics['web_search_calls']}, "
        f"unsupported={metrics['unsupported_conclusion_count']}, "
        f"halluc_ids={metrics['hallucinated_fact_id_count']}"
    )


async def _run_judge(
    config: JudgeConfig,
    question: str,
    artifacts: dict,
    metrics: dict,
    rubric: str,
) -> JudgeResult:
    """Stand up the judge, score the run, and shut down cleanly."""
    judge = Judge(config)
    await judge.start()
    try:
        return await judge.score_run(question, artifacts, metrics, rubric=rubric)
    finally:
        await judge.stop()


def _resolve_question(
    question_id: str | None,
    artifacts: dict,
) -> tuple[str, str, str] | None:
    """Resolve the question ID, text, and rubric type.

    Returns ``(question_id, question_text, rubric_type)`` or ``None``
    if the question can't be resolved (with an error already printed).

    - With ``--question-id``: looks up in the question set.
    - Without: derives from ``knowledge.question`` in the snapshot,
      uses general rubric, and labels as ``adhoc-<run-id-short>``.
    """
    if question_id:
        question = get_question(question_id)
        if question is None:
            available = ", ".join(sorted(q for q in _all_question_ids()))
            print(
                f"Unknown question-id '{question_id}'. Available: {available}",
                file=sys.stderr,
            )
            return None
        if question.placeholder:
            print(
                f"WARNING: question '{question_id}' is a placeholder "
                "awaiting user input (see agent-docs/QUESTIONS.md).",
                file=sys.stderr,
            )
        return (question.id, question.text, question.rubric)
    else:
        # Ad hoc mode: derive question text from knowledge snapshot.
        knowledge = artifacts.get("knowledge")
        question_text = ""
        if knowledge:
            question_text = knowledge.get("question", "")
        if not question_text:
            print(
                "WARNING: no --question-id given and knowledge snapshot has "
                "no question text. Using '(no question captured)' as the "
                "question.",
                file=sys.stderr,
            )
            question_text = "(no question captured)"
        run_id = artifacts.get("run_id", "unknown")
        adhoc_id = f"adhoc-{run_id[:8]}"
        return (adhoc_id, question_text, "general")


def _all_question_ids() -> list[str]:
    from moira_eval.questions import QUESTIONS

    return list(QUESTIONS.keys())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a MOiRA workflow run: metrics + optional judge scoring."
    )
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
    parser.add_argument(
        "--question-id",
        default=None,
        help=(
            "Benchmark question ID (e.g., tyranitar-ou). "
            "If omitted, derives question from the knowledge snapshot "
            "and uses the general rubric (ad hoc mode)."
        ),
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

    resolved = _resolve_question(args.question_id, artifacts)
    if resolved is None:
        sys.exit(1)
    question_id, question_text, rubric = resolved

    judge_config = judge_config_from_env()

    if judge_config is not None:
        try:
            judge_result = asyncio.run(
                _run_judge(judge_config, question_text, artifacts, metrics, rubric)
            )
        except JudgeError as exc:
            print(f"Judge error: {exc}", file=sys.stderr)
            print("Falling back to metrics-only.", file=sys.stderr)
            judge_result = None
        except Exception as exc:
            print(f"Unexpected judge failure: {exc}", file=sys.stderr)
            print("Falling back to metrics-only.", file=sys.stderr)
            judge_result = None

        if judge_result is not None:
            result = build_result(
                question_id=question_id,
                question_text=question_text,
                artifacts=artifacts,
                metrics=metrics,
                judge_result=judge_result,
                rubric=rubric,
            )
            result_path = save_result(result)

            # Write/update meta.json for this commit directory.
            judge_model = getattr(judge_config, "model", "")
            meta = build_meta(commit_sha=result.commit_sha, judge_model=judge_model)
            save_meta(meta)

            try:
                result_path_rel = result_path.relative_to(Path.cwd())
            except ValueError:
                result_path_rel = result_path

            print(_format_judged_summary(question_id, judge_result, metrics))

            for cat in judge_result.categories:
                marker = (
                    "!"
                    if cat.name in judge_result.hard_fail_categories
                    and cat.score < judge_result.scale_max
                    else " "
                )
                print(
                    f"  {marker} {cat.name}: {cat.score}/{judge_result.scale_max} "
                    f"— {cat.rationale}"
                )

            if judge_result.overall_notes:
                print(f"  notes: {judge_result.overall_notes}")

            print(f"  -> {result_path_rel}")
            return
    else:
        print(
            "WARNING: judge env vars not set "
            "(MOIRA_EVAL_JUDGE_ENDPOINT, MOIRA_EVAL_JUDGE_MODEL). "
            "Falling back to metrics-only.",
            file=sys.stderr,
        )

    # Metrics-only output.
    print(_format_metrics_summary(artifacts["run_id"], metrics))

    if metrics["hallucinated_fact_ids"]:
        print(f"  hallucinated IDs: {', '.join(metrics['hallucinated_fact_ids'])}")

    if metrics["review_count"] > 1 or metrics["evaluation_count"] > 1:
        print(
            f"  retries: research={metrics['research_count']} "
            f"review={metrics['review_count']} eval={metrics['evaluation_count']}"
        )


if __name__ == "__main__":
    main()
