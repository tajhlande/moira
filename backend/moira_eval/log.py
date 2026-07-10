"""Append scored results to the tracked ``EVAL_LOG.md``.

This is the **only** path by which an evaluation result enters version
control. The log file is markdown, append-only, and chronological
(newest at bottom). Result files in ``results/`` are gitignored;
``EVAL_LOG.md`` is committed.

Usage::

    uv run python -m moira_eval.log \\
        --result moira_eval/results/<commit>/<question>.json \\
        --note "tightened synthesis prompt; type-correctness 1->2"

Duplicate detection: refuses to log the same commit + question ID twice
unless ``--force`` is passed.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def _get_branch() -> str:
    """Get the current git branch name, or ``"unknown"``."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"


def format_log_entry(
    result: dict,
    branch: str = "",
    note: str | None = None,
) -> str:
    """Format a result dict as a markdown log entry.

    The entry includes: date, question ID, commit SHA, branch, rubric,
    score with pass/fail, key metrics, judge model, and optional note.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    commit = result.get("commit_sha", "unknown")
    qid = result.get("question_id", "?")
    model = result.get("model", "unknown")
    branch_str = f", {branch}" if branch and branch != "unknown" else ""

    lines: list[str] = []
    lines.append(f"## {ts} {qid} (commit {commit}{branch_str})")
    lines.append("")

    judge = result.get("judge")
    if judge:
        total = judge.get("total", "N/A")
        max_total = judge.get("max_total", "N/A")
        passed = "PASS" if judge.get("passed") else "FAIL"
        lines.append(f"- Score: {total}/{max_total} ({passed})")

        hard_fails = judge.get("hard_fail_categories_failed", [])
        if hard_fails:
            lines.append(f"- Hard-fail categories: {', '.join(hard_fails)}")
    else:
        lines.append("- Score: (metrics-only, no judge)")

    metrics = result.get("metrics", {})
    metric_parts = [
        f"web_search_calls: {metrics.get('web_search_calls', 'N/A')}",
        f"unsupported_conclusions: {metrics.get('unsupported_conclusion_count', 'N/A')}",
        f"hallucinated_ids: {metrics.get('hallucinated_fact_id_count', 'N/A')}",
    ]
    lines.append(f"- {' | '.join(metric_parts)}")
    lines.append(f"- Agent model: {model}")

    if note:
        lines.append(f"- Note: {note}")

    lines.append("")
    return "\n".join(lines)


def _extract_commit_and_question_from_entry(entry: str) -> set[tuple[str, str]]:
    """Parse a log entry header to find commit + question pairs.

    Returns a set of ``(commit_sha, question_id)`` tuples found in the
    entry text. Used for duplicate detection.
    """
    pairs: set[tuple[str, str]] = set()
    # Match: ## 2026-07-06 tyranitar-ou (commit a1b2c3d, branch)
    # or:    ## 2026-07-06 tyranitar-ou (commit a1b2c3d)
    pattern = re.compile(
        r"^## \d{4}-\d{2}-\d{2}\s+(\S+)\s+\(commit\s+([a-f0-9]+)",
        re.MULTILINE,
    )
    for match in pattern.finditer(entry):
        qid = match.group(1)
        commit = match.group(2)
        pairs.add((commit, qid))
    return pairs


def is_duplicate(
    log_path: Path,
    commit_sha: str,
    question_id: str,
) -> bool:
    """Check if a log entry already exists for this commit + question."""
    if not log_path.exists():
        return False
    content = log_path.read_text(encoding="utf-8")
    existing = _extract_commit_and_question_from_entry(content)
    return (commit_sha, question_id) in existing


def append_to_log(
    log_path: Path,
    entry: str,
) -> None:
    """Append an entry to the log file, creating it if needed."""
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if log_path.exists():
        content = log_path.read_text(encoding="utf-8")
        # Ensure exactly one blank line between entries
        if content and not content.endswith("\n\n"):
            if content.endswith("\n"):
                entry = "\n" + entry
            else:
                entry = "\n\n" + entry
    else:
        # New file — add a header
        header = "# EVAL_LOG\n\nScores are recorded manually via `moira_eval.log`.\n\n---\n\n"
        entry = header + entry

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log an evaluation result to the tracked EVAL_LOG.md."
    )
    parser.add_argument(
        "--result",
        required=True,
        help="Path to the result JSON file to log.",
    )
    parser.add_argument(
        "--note",
        default=None,
        help="Optional note explaining what changed.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help=(
            "Path to the log file. Defaults to EVAL_LOG.md at the "
            "repository root (parent of backend/)."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Log even if a duplicate entry exists.",
    )
    args = parser.parse_args()

    result_path = Path(args.result)
    if not result_path.is_file():
        print(f"Result file not found: {result_path}", file=sys.stderr)
        sys.exit(1)

    result = json.loads(result_path.read_text(encoding="utf-8"))

    # Resolve log file path.
    if args.log_file:
        log_path = Path(args.log_file)
    else:
        # Default: EVAL_LOG.md at the repository root.
        package_dir = Path(__file__).resolve().parent
        # backend/moira_eval/ -> backend/ -> repo root
        repo_root = package_dir.parent.parent
        log_path = repo_root / "EVAL_LOG.md"

    commit_sha = result.get("commit_sha", "unknown")
    question_id = result.get("question_id", "?")

    if not args.force and is_duplicate(log_path, commit_sha, question_id):
        print(
            f"Duplicate: commit '{commit_sha}' + question '{question_id}' "
            f"already in {log_path}. Use --force to override.",
            file=sys.stderr,
        )
        sys.exit(1)

    branch = _get_branch()
    entry = format_log_entry(result, branch=branch, note=args.note)
    append_to_log(log_path, entry)

    print(f"Logged {question_id} ({commit_sha}) to {log_path}")


if __name__ == "__main__":
    main()
