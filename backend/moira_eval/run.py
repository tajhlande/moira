"""Evaluation orchestrator CLI.

Wires capture -> metrics -> judge (optional) -> result storage.

In Iteration 2:
- ``--question-id tyranitar-ou`` triggers judge scoring if env vars are set.
- Without ``--question-id``, or without env vars, falls back to metrics-only.

Usage::

    # judged run (requires MOIRA_EVAL_JUDGE_* env vars)
    uv run python -m moira_eval.run --db backend/moira.db --run-id <uuid> \\
        --question-id tyranitar-ou

    # metrics-only (no judge)
    uv run python -m moira_eval.run --db backend/moira.db --run-id <uuid>
"""

import argparse
import asyncio
import sys
from pathlib import Path

from moira_eval.capture import capture_artifacts
from moira_eval.judge import Judge, JudgeConfig, JudgeError, JudgeResult, judge_config_from_env
from moira_eval.metrics import compute_metrics
from moira_eval.result import build_result, save_result
from moira_eval.rubric_pokemon import CANARY_QUESTION, HARD_FAIL_CATEGORIES

# Iteration 2: only the Pokemon canary question is defined.
# Iteration 3 adds questions.py with the full question set.
_QUESTIONS: dict[str, str] = {
    "tyranitar-ou": CANARY_QUESTION,
}


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
) -> JudgeResult:
    """Stand up the judge, score the run, and shut down cleanly."""
    judge = Judge(config)
    await judge.start()
    try:
        return await judge.score_run(question, artifacts, metrics)
    finally:
        await judge.stop()


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
            "When provided and judge env vars are set, triggers judge scoring. "
            "Without it, falls back to metrics-only."
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

    # Determine whether we can run the judge.
    question_id = args.question_id
    question_text: str | None = None
    if question_id:
        question_text = _QUESTIONS.get(question_id)
        if question_text is None:
            print(
                f"Unknown question-id '{question_id}'. Available: {', '.join(sorted(_QUESTIONS))}",
                file=sys.stderr,
            )
            sys.exit(1)

    judge_config = judge_config_from_env()

    # Judge scoring requires both a question-id and env vars.
    # Without either, fall back to metrics-only.
    if question_id and judge_config is not None:
        try:
            judge_result = asyncio.run(_run_judge(judge_config, question_text, artifacts, metrics))
        except JudgeError as exc:
            print(f"Judge error: {exc}", file=sys.stderr)
            print("Falling back to metrics-only.", file=sys.stderr)
            judge_result = None
        except Exception as exc:
            print(f"Unexpected judge failure: {exc}", file=sys.stderr)
            print("Falling back to metrics-only.", file=sys.stderr)
            judge_result = None

        if judge_result is not None:
            # Build and save the result file.
            result = build_result(
                question_id=question_id,
                question_text=question_text,
                artifacts=artifacts,
                metrics=metrics,
                judge_result=judge_result,
                rubric="pokemon",
            )
            result_path = save_result(result)
            try:
                result_path_rel = result_path.relative_to(Path.cwd())
            except ValueError:
                result_path_rel = result_path

            print(_format_judged_summary(question_id, judge_result, metrics))

            # Surface per-category scores.
            for cat in judge_result.categories:
                marker = "!" if cat.name in HARD_FAIL_CATEGORIES and cat.score < 2 else " "
                print(f"  {marker} {cat.name}: {cat.score}/2 — {cat.rationale}")

            if judge_result.overall_notes:
                print(f"  notes: {judge_result.overall_notes}")

            print(f"  -> {result_path_rel}")
            return
    else:
        # Explain why we're falling back.
        if question_id and judge_config is None:
            print(
                "WARNING: --question-id given but judge env vars not set "
                "(MOIRA_EVAL_JUDGE_ENDPOINT, MOIRA_EVAL_JUDGE_MODEL). "
                "Falling back to metrics-only.",
                file=sys.stderr,
            )
        elif judge_config is not None and not question_id:
            print(
                "WARNING: judge env vars are set but no --question-id given. "
                "Ad hoc judge scoring arrives in Iteration 3 (general rubric). "
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
