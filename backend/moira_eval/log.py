"""Append scored results to the tracked ``EVAL_LOG.md``.

This is the **only** path by which an evaluation result enters version
control. The log file is markdown, append-only, and chronological
(newest at bottom). Result files in ``results/`` are gitignored;
``EVAL_LOG.md`` is committed.

Three modes:

1. **Batch mode** (default): reads all result JSONs from the most
   recently modified ``results/<commit>/`` directory and formats them
   as a single batch entry with a summary table::

       uv run python -m moira_eval.log \\
           --note "searxng en-US fix baseline"

2. **Batch mode with explicit commit**: reads from a specific
   ``results/<commit>/`` directory::

       uv run python -m moira_eval.log --commit cefe112f

3. **Single-question mode**: logs one result file::

       uv run python -m moira_eval.log \\
           --result moira_eval/results/<commit>/<question>.json

Duplicate detection: in single-question mode, refuses to log the same
commit + question ID twice. In batch mode, refuses to log the same
commit twice. Use ``--force`` to override.
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


def format_batch_entry(
    results: list[dict],
    branch: str = "",
    note: str | None = None,
) -> str:
    """Format multiple result dicts as a single batch log entry.

    Produces a markdown table summarising all questions in the batch,
    followed by the agent model and optional note.
    ``commit_sha`` and ``model`` are read from the result files — not
    from git — so that uncommitted-at-log-time results are recorded
    correctly.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    commit = results[0].get("commit_sha", "unknown") if results else "unknown"
    model = results[0].get("model", "unknown") if results else "unknown"
    branch_str = f", {branch}" if branch and branch != "unknown" else ""

    lines: list[str] = []
    lines.append(f"## {ts} batch (commit {commit}{branch_str})")
    lines.append("")
    lines.append("| Question | Rubric | Score | web_search | Status |")
    lines.append("|----------|--------|-------|------------|--------|")

    for r in results:
        qid = r.get("question_id", "?")
        rubric = r.get("rubric", "?")
        metrics = r.get("metrics", {})
        ws = str(metrics.get("web_search_calls", "—"))

        judge = r.get("judge")
        if judge:
            total = judge.get("total", "N/A")
            max_total = judge.get("max_total", "N/A")
            passed = "PASS" if judge.get("passed") else "FAIL"
            score_str = f"{total}/{max_total}"
        else:
            score_str = "metrics-only"
            passed = "—"

        lines.append(f"| {qid} | {rubric} | {score_str} | {ws} | {passed} |")

    lines.append("")
    lines.append(f"- Agent model: {model}")
    if note:
        lines.append(f"- Note: {note}")
    lines.append("")

    return "\n".join(lines)


def _find_latest_results_dir(results_root: Path) -> Path | None:
    """Find the most recently modified ``results/<commit>/`` directory.

    Returns ``None`` if ``results_root`` does not exist or has no
    sub-directories.
    """
    if not results_root.exists():
        return None
    dirs = [d for d in results_root.iterdir() if d.is_dir()]
    if not dirs:
        return None
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return dirs[0]


def _load_results_from_dir(results_dir: Path) -> list[dict]:
    """Load all result JSON files from a directory (excluding ``meta.json``).

    Files are sorted alphabetically so the table order is deterministic.
    """
    results: list[dict] = []
    for f in sorted(results_dir.glob("*.json")):
        if f.name == "meta.json":
            continue
        results.append(json.loads(f.read_text(encoding="utf-8")))
    return results


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


def is_batch_duplicate(
    log_path: Path,
    commit_sha: str,
) -> bool:
    """Check if a batch entry already exists for this commit.

    Batch entries have the header pattern::

        ## YYYY-MM-DD batch (commit <sha>...)

    so we match on ``batch (commit <sha>``.
    """
    if not log_path.exists():
        return False
    content = log_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^## \d{4}-\d{2}-\d{2}\s+batch\s+\(commit\s+" + re.escape(commit_sha),
        re.MULTILINE,
    )
    return bool(pattern.search(content))


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


def _resolve_repo_root() -> Path:
    """Return the repository root (parent of backend/)."""
    package_dir = Path(__file__).resolve().parent
    return package_dir.parent.parent


def _resolve_results_root() -> Path:
    """Return the ``results/`` directory path."""
    return Path(__file__).resolve().parent / "results"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log an evaluation result to the tracked EVAL_LOG.md.",
    )
    parser.add_argument(
        "--result",
        default=None,
        help=(
            "Path to a single result JSON file. If omitted, operates in "
            "batch mode (reads all results from results/<commit>/)."
        ),
    )
    parser.add_argument(
        "--commit",
        default=None,
        help=(
            "Commit SHA whose results to log (batch mode). "
            "If omitted, uses the most recently modified results directory."
        ),
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

    # Resolve log file path.
    if args.log_file:
        log_path = Path(args.log_file)
    else:
        log_path = _resolve_repo_root() / "EVAL_LOG.md"

    branch = _get_branch()

    # ------------------------------------------------------------------
    # Single-question mode
    # ------------------------------------------------------------------
    if args.result:
        result_path = Path(args.result)
        if not result_path.is_file():
            print(f"Result file not found: {result_path}", file=sys.stderr)
            sys.exit(1)

        result = json.loads(result_path.read_text(encoding="utf-8"))
        commit_sha = result.get("commit_sha", "unknown")
        question_id = result.get("question_id", "?")

        if not args.force and is_duplicate(log_path, commit_sha, question_id):
            print(
                f"Duplicate: commit '{commit_sha}' + question '{question_id}' "
                f"already in {log_path}. Use --force to override.",
                file=sys.stderr,
            )
            sys.exit(1)

        entry = format_log_entry(result, branch=branch, note=args.note)
        append_to_log(log_path, entry)
        print(f"Logged {question_id} ({commit_sha}) to {log_path}")
        return

    # ------------------------------------------------------------------
    # Batch mode
    # ------------------------------------------------------------------
    results_root = _resolve_results_root()

    if args.commit:
        results_dir = results_root / args.commit
        if not results_dir.is_dir():
            print(
                f"Results directory not found: {results_dir}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        results_dir = _find_latest_results_dir(results_root)
        if results_dir is None:
            print(
                f"No results directories found in {results_root}",
                file=sys.stderr,
            )
            sys.exit(1)

    results = _load_results_from_dir(results_dir)
    if not results:
        print(f"No result files found in {results_dir}", file=sys.stderr)
        sys.exit(1)

    commit_sha = results[0].get("commit_sha", "unknown")

    if not args.force and is_batch_duplicate(log_path, commit_sha):
        print(
            f"Duplicate: batch for commit '{commit_sha}' "
            f"already in {log_path}. Use --force to override.",
            file=sys.stderr,
        )
        sys.exit(1)

    entry = format_batch_entry(results, branch=branch, note=args.note)
    append_to_log(log_path, entry)
    print(f"Logged batch ({len(results)} results, {commit_sha}) to {log_path}")


if __name__ == "__main__":
    main()
