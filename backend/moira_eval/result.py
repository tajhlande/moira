"""Result file storage for evaluation runs.

Result files are written to ``moira_eval/results/<commit-sha-short>/\
<question-id>.json`` and contain the full scored result: question, run_id,
metrics, judge scores, timestamp, and model info.

The ``results/`` directory is gitignored — it's local working data. The
tracked history lives in ``EVAL_LOG.md`` (Iteration 4).

Commit SHA is used as the directory key so that results from different
code states are naturally separated and diffable.
"""

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from moira_eval.judge import JudgeResult


@dataclass
class ResultData:
    """The full result record written to disk.

    This is the canonical evaluation result format. It's JSON-serializable
    and stable across iterations (new fields can be added, existing fields
    keep their names).
    """

    question_id: str
    question_text: str
    run_id: str
    timestamp: str
    commit_sha: str
    rubric: str  # "pokemon" or "general"
    metrics: dict
    judge: dict | None  # None when metrics-only fallback
    model: str  # the agent model (from run), not the judge model


def get_commit_sha(short: bool = True) -> str:
    """Get the current git commit SHA.

    Returns ``"unknown"`` if git is unavailable or the directory is not
    a repository. When ``short`` is True, returns the first 8 characters.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        sha = result.stdout.strip()
        return sha[:8] if short else sha
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"


def build_result(
    question_id: str,
    question_text: str,
    artifacts: dict,
    metrics: dict,
    judge_result: JudgeResult | None = None,
    commit_sha: str | None = None,
    rubric: str = "pokemon",
) -> ResultData:
    """Build a ``ResultData`` from evaluation outputs.

    Args:
        question_id: Benchmark question identifier (e.g. ``tyranitar-ou``).
        question_text: The full question text.
        artifacts: The artifacts dict from ``capture_artifacts``.
        metrics: The metrics dict from ``compute_metrics``.
        judge_result: The judge's parsed result, or ``None`` for
            metrics-only mode.
        commit_sha: Override the commit SHA (for testing). If ``None``,
            reads from git.
        rubric: Rubric identifier (``"pokemon"`` or ``"general"``).
    """
    sha = commit_sha or get_commit_sha()

    judge_dict: dict | None = None
    model = ""
    if judge_result is not None:
        judge_dict = {
            "categories": [
                {"name": c.name, "score": c.score, "rationale": c.rationale}
                for c in judge_result.categories
            ],
            "overall_notes": judge_result.overall_notes,
            "total": judge_result.total,
            "max_total": judge_result.max_total,
            "passed": judge_result.passed,
            "hard_fail_categories_failed": judge_result.hard_fail_categories_failed,
        }
        model = judge_result.model

    return ResultData(
        question_id=question_id,
        question_text=question_text,
        run_id=artifacts.get("run_id", ""),
        timestamp=datetime.now(timezone.utc).isoformat(),
        commit_sha=sha,
        rubric=rubric,
        metrics=metrics,
        judge=judge_dict,
        model=model,
    )


def save_result(
    result: ResultData,
    results_dir: Path | str | None = None,
) -> Path:
    """Write a result file to ``<results_dir>/<commit>/<question-id>.json``.

    Args:
        result: The ``ResultData`` to write.
        results_dir: Base directory for results. Defaults to
            ``backend/moira_eval/results/`` relative to the package.

    Returns:
        The path to the written file.
    """
    if results_dir is None:
        # Default: moira_eval/results/ next to the package.
        package_dir = Path(__file__).resolve().parent
        results_dir = package_dir / "results"
    else:
        results_dir = Path(results_dir)

    out_dir = results_dir / result.commit_sha
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{result.question_id}.json"
    out_path.write_text(
        json.dumps(asdict(result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path
