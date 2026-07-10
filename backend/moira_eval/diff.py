"""Side-by-side comparison of scored results across two commits.

Loads result JSON files from two commit directories, matches them by
question ID, and prints a table showing score deltas, per-category
changes, and key metric shifts.

Usage::

    uv run python -m moira_eval.diff \\
        --a moira_eval/results/<commit-1> \\
        --b moira_eval/results/<commit-2>

Only questions that exist in both directories are compared. Questions
unique to either side are listed at the end.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Metrics shown in the diff table. These are the ones that move visibly
# when something changes in the agent's behaviour.
_DIFF_METRICS = [
    ("web_search_calls", "web_search"),
    ("total_tool_calls", "total_tools"),
    ("unsupported_conclusion_count", "unsupported"),
    ("hallucinated_fact_id_count", "halluc_ids"),
    ("budget_consumed", "budget"),
]


@dataclass
class CategoryDelta:
    """Score change for a single rubric category."""

    name: str
    score_a: int | None
    score_b: int | None
    scale_max: int

    @property
    def delta(self) -> int | None:
        if self.score_a is None or self.score_b is None:
            return None
        return self.score_b - self.score_a


@dataclass
class QuestionDiff:
    """Full diff for one question across two commits.

    ``only_in_a`` / ``only_in_b`` are True when the question exists in
    only one of the two directories. In that case the score/category/
    metric fields are ``None`` on the missing side.
    """

    question_id: str
    rubric: str
    only_in_a: bool = False
    only_in_b: bool = False
    score_a: int | None = None
    score_b: int | None = None
    max_total: int | None = None
    passed_a: bool | None = None
    passed_b: bool | None = None
    category_deltas: list[CategoryDelta] = field(default_factory=list)
    metric_deltas: list[tuple[str, float | int | None, float | int | None]] = field(
        default_factory=list
    )

    @property
    def score_delta(self) -> int | None:
        if self.score_a is None or self.score_b is None:
            return None
        return self.score_b - self.score_a


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_results(dir_path: Path | str) -> dict[str, dict]:
    """Load all ``*.json`` result files from a commit directory.

    Returns a dict keyed by question ID. Non-result JSON files
    (e.g. ``meta.json``) are skipped.
    """
    dir_path = Path(dir_path)
    if not dir_path.is_dir():
        raise FileNotFoundError(f"Result directory not found: {dir_path}")

    results: dict[str, dict] = {}
    for f in sorted(dir_path.glob("*.json")):
        if f.name == "meta.json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        qid = data.get("question_id")
        if qid:
            results[qid] = data
    return results


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------


def _extract_categories(
    result: dict,
) -> list[tuple[str, int, int]]:
    """Return ``(name, score, scale_max)`` tuples from a result dict.

    ``scale_max`` is inferred from ``judge.max_total / len(categories)``
    when possible (pokemon: 16/8 = 2, general: 25/5 = 5), falling back
    to a per-category max derived from the highest score seen.
    """
    judge = result.get("judge")
    if not judge:
        return []
    cats = judge.get("categories", [])
    max_total = judge.get("max_total")
    if max_total and cats:
        scale = max_total // len(cats) if len(cats) else 0
    else:
        scale = max((c.get("score", 0) for c in cats), default=0)
    return [(c["name"], c.get("score"), scale) for c in cats]


def diff_pair(a: dict, b: dict) -> QuestionDiff:
    """Compute the diff between two result dicts with the same question ID."""
    cats_a = _extract_categories(a)
    cats_b = _extract_categories(b)
    cat_map_a = {name: (score, scale) for name, score, scale in cats_a}
    cat_map_b = {name: (score, scale) for name, score, scale in cats_b}

    all_cat_names = list(dict.fromkeys([c[0] for c in cats_a] + [c[0] for c in cats_b]))

    category_deltas: list[CategoryDelta] = []
    for name in all_cat_names:
        score_a, scale_a = cat_map_a.get(name, (None, 0))
        score_b, scale_b = cat_map_b.get(name, (None, 0))
        category_deltas.append(
            CategoryDelta(
                name=name,
                score_a=score_a,
                score_b=score_b,
                scale_max=max(scale_a, scale_b) or 0,
            )
        )

    metric_deltas: list[tuple[str, float | int | None, float | int | None]] = []
    ma = a.get("metrics", {})
    mb = b.get("metrics", {})
    for key, _label in _DIFF_METRICS:
        va = ma.get(key)
        vb = mb.get(key)
        metric_deltas.append((key, va, vb))

    judge_a = a.get("judge") or {}
    judge_b = b.get("judge") or {}

    return QuestionDiff(
        question_id=a.get("question_id", "?"),
        rubric=a.get("rubric", b.get("rubric", "?")),
        score_a=judge_a.get("total"),
        score_b=judge_b.get("total"),
        max_total=judge_a.get("max_total") or judge_b.get("max_total"),
        passed_a=judge_a.get("passed"),
        passed_b=judge_b.get("passed"),
        category_deltas=category_deltas,
        metric_deltas=metric_deltas,
    )


def diff_dirs(
    a_dir: Path | str,
    b_dir: Path | str,
) -> list[QuestionDiff]:
    """Diff all results between two commit directories.

    Questions present in both are compared. Questions unique to either
    side are included with ``only_in_a`` / ``only_in_b`` set.
    """
    results_a = load_results(a_dir)
    results_b = load_results(b_dir)

    all_ids = list(dict.fromkeys(list(results_a.keys()) + list(results_b.keys())))

    diffs: list[QuestionDiff] = []
    for qid in all_ids:
        ra = results_a.get(qid)
        rb = results_b.get(qid)
        if ra and rb:
            diffs.append(diff_pair(ra, rb))
        elif ra:
            diffs.append(
                QuestionDiff(
                    question_id=qid,
                    rubric=ra.get("rubric", "?"),
                    only_in_a=True,
                )
            )
        else:
            diffs.append(
                QuestionDiff(
                    question_id=qid,
                    rubric=rb.get("rubric", "?"),
                    only_in_b=True,
                )
            )
    return diffs


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _fmt_delta(delta: int | float | None, suffix: str = "") -> str:
    """Format a numeric delta with arrow indicators."""
    if delta is None:
        return "  —"
    if delta > 0:
        return f"  +{delta}{suffix}"
    if delta < 0:
        return f"  {delta}{suffix}"
    return "  —"


def _fmt_value(val: int | float | None) -> str:
    if val is None:
        return "  N/A"
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val)


def format_diff(
    diffs: list[QuestionDiff],
    a_label: str,
    b_label: str,
) -> str:
    """Format a list of question diffs as a terminal-friendly table."""
    lines: list[str] = []
    lines.append(f"Comparing {a_label} -> {b_label}")
    lines.append("=" * 68)
    lines.append("")

    matched = [d for d in diffs if not d.only_in_a and not d.only_in_b]
    only_a = [d for d in diffs if d.only_in_a]
    only_b = [d for d in diffs if d.only_in_b]

    for d in matched:
        header = f"{d.question_id} ({d.rubric})"
        if d.score_delta is not None:
            header += _fmt_delta(d.score_delta)
        if d.passed_a is not None and d.passed_b is not None:
            if d.passed_a != d.passed_b:
                header += f"  [{'PASS' if d.passed_b else 'FAIL'}!]"
        lines.append(header)

        if d.score_a is not None and d.score_b is not None:
            max_str = f"/{d.max_total}" if d.max_total else ""
            lines.append(
                f"  Score:          {_fmt_value(d.score_a)}{max_str}"
                f"  ->  {_fmt_value(d.score_b)}{max_str}"
            )

        for cd in d.category_deltas:
            sa = _fmt_value(cd.score_a)
            sb = _fmt_value(cd.score_b)
            delta = _fmt_delta(cd.delta)
            lines.append(f"    {cd.name}: {sa}/{cd.scale_max} -> {sb}/{cd.scale_max} ({delta})")

        for key, va, vb in d.metric_deltas:
            label = dict(_DIFF_METRICS).get(key, key)
            if (
                va is not None
                and vb is not None
                and isinstance(va, (int, float))
                and isinstance(vb, (int, float))
            ):
                delta = vb - va
            else:
                delta = None
            lines.append(
                f"    {label}: {_fmt_value(va)} -> {_fmt_value(vb)} ({_fmt_delta(delta)})"
            )
        lines.append("")

    if only_a:
        lines.append(f"Only in {a_label}:")
        for d in only_a:
            lines.append(f"  {d.question_id} ({d.rubric})")
        lines.append("")

    if only_b:
        lines.append(f"Only in {b_label}:")
        for d in only_b:
            lines.append(f"  {d.question_id} ({d.rubric})")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare evaluation results across two commits.")
    parser.add_argument(
        "--a",
        required=True,
        help="Directory A (e.g., moira_eval/results/<commit-1>)",
    )
    parser.add_argument(
        "--b",
        required=True,
        help="Directory B (e.g., moira_eval/results/<commit-2>)",
    )
    args = parser.parse_args()

    a_dir = Path(args.a)
    b_dir = Path(args.b)

    for d, label in [(a_dir, "A"), (b_dir, "B")]:
        if not d.is_dir():
            print(f"Directory {label} not found: {d}", file=sys.stderr)
            sys.exit(1)

    diffs = diff_dirs(a_dir, b_dir)
    if not diffs:
        print("No results found in either directory.", file=sys.stderr)
        sys.exit(1)

    output = format_diff(diffs, a_dir.name, b_dir.name)
    print(output)


if __name__ == "__main__":
    main()
