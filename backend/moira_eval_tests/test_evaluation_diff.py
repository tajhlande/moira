"""Tests for moira_eval.diff: side-by-side result comparison."""

import json
from pathlib import Path

import pytest

from moira_eval.diff import (
    CategoryDelta,
    QuestionDiff,
    diff_dirs,
    diff_pair,
    format_diff,
    load_results,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_result(
    question_id: str = "tyranitar-ou",
    rubric: str = "pokemon",
    commit_sha: str = "abc12345",
    score: int = 12,
    max_total: int = 16,
    passed: bool = False,
    categories: list[dict] | None = None,
    metrics: dict | None = None,
) -> dict:
    """Build a minimal result dict for testing."""
    if categories is None:
        categories = [
            {"name": "Tool choice", "score": 2, "rationale": "ok"},
            {"name": "Search discipline", "score": 1, "rationale": "high"},
            {"name": "Type correctness", "score": 2, "rationale": "correct"},
        ]
    if metrics is None:
        metrics = {
            "web_search_calls": 11,
            "total_tool_calls": 29,
            "unsupported_conclusion_count": 0,
            "hallucinated_fact_id_count": 0,
            "budget_consumed": 82.0,
        }
    return {
        "question_id": question_id,
        "question_text": "test question",
        "run_id": "run-123",
        "timestamp": "2026-07-09T00:00:00+00:00",
        "commit_sha": commit_sha,
        "rubric": rubric,
        "metrics": metrics,
        "judge": {
            "categories": categories,
            "overall_notes": "",
            "total": score,
            "max_total": max_total,
            "passed": passed,
            "hard_fail_categories_failed": [],
        },
        "model": "test-model",
    }


def _write_result_dir(
    tmp_path: Path,
    commit: str,
    results: list[dict],
) -> Path:
    """Write result JSON files into a commit directory."""
    commit_dir = tmp_path / commit
    commit_dir.mkdir(parents=True)
    for r in results:
        fname = f"{r['question_id']}.json"
        (commit_dir / fname).write_text(
            json.dumps(r, indent=2),
            encoding="utf-8",
        )
    # Add a meta.json to ensure it's skipped
    (commit_dir / "meta.json").write_text(
        json.dumps({"commit": commit, "branch": "main"}),
        encoding="utf-8",
    )
    return commit_dir


# ---------------------------------------------------------------------------
# load_results
# ---------------------------------------------------------------------------


class TestLoadResults:
    def test_loads_json_files(self, tmp_path):
        d = _write_result_dir(tmp_path, "abc12345", [_make_result()])
        results = load_results(d)
        assert "tyranitar-ou" in results
        assert results["tyranitar-ou"]["question_id"] == "tyranitar-ou"

    def test_skips_meta_json(self, tmp_path):
        d = _write_result_dir(tmp_path, "abc12345", [_make_result()])
        results = load_results(d)
        assert "meta" not in results
        assert len(results) == 1

    def test_missing_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_results(tmp_path / "nonexistent")


# ---------------------------------------------------------------------------
# diff_pair
# ---------------------------------------------------------------------------


class TestDiffPair:
    def test_score_delta(self):
        a = _make_result(score=12, max_total=16)
        b = _make_result(score=14, max_total=16)
        d = diff_pair(a, b)
        assert d.score_delta == 2

    def test_negative_delta(self):
        a = _make_result(score=14, max_total=16)
        b = _make_result(score=10, max_total=16)
        d = diff_pair(a, b)
        assert d.score_delta == -4

    def test_category_deltas(self):
        cats_a = [
            {"name": "Grounding", "score": 3, "rationale": ""},
            {"name": "Citation support", "score": 4, "rationale": ""},
        ]
        cats_b = [
            {"name": "Grounding", "score": 5, "rationale": ""},
            {"name": "Citation support", "score": 4, "rationale": ""},
        ]
        a = _make_result(score=7, max_total=10, categories=cats_a, rubric="general")
        b = _make_result(score=9, max_total=10, categories=cats_b, rubric="general")
        d = diff_pair(a, b)
        assert len(d.category_deltas) == 2
        grounding = next(c for c in d.category_deltas if c.name == "Grounding")
        assert grounding.delta == 2
        citation = next(c for c in d.category_deltas if c.name == "Citation support")
        assert citation.delta == 0

    def test_metric_deltas(self):
        a = _make_result(
            metrics={
                "web_search_calls": 11,
                "total_tool_calls": 29,
                "unsupported_conclusion_count": 2,
                "hallucinated_fact_id_count": 0,
                "budget_consumed": 82.0,
            }
        )
        b = _make_result(
            metrics={
                "web_search_calls": 6,
                "total_tool_calls": 15,
                "unsupported_conclusion_count": 0,
                "hallucinated_fact_id_count": 0,
                "budget_consumed": 45.0,
            }
        )
        d = diff_pair(a, b)
        metric_map = {k: (va, vb) for k, va, vb in d.metric_deltas}
        assert metric_map["web_search_calls"] == (11, 6)
        assert metric_map["unsupported_conclusion_count"] == (2, 0)
        assert metric_map["budget_consumed"] == (82.0, 45.0)

    def test_passed_change_detected(self):
        a = _make_result(score=10, max_total=16, passed=False)
        b = _make_result(score=14, max_total=16, passed=True)
        d = diff_pair(a, b)
        assert d.passed_a is False
        assert d.passed_b is True

    def test_metrics_only_results(self):
        """When judge is None (metrics-only), scores are None."""
        a = _make_result()
        a["judge"] = None
        b = _make_result()
        b["judge"] = None
        d = diff_pair(a, b)
        assert d.score_a is None
        assert d.score_b is None
        assert d.score_delta is None


# ---------------------------------------------------------------------------
# diff_dirs
# ---------------------------------------------------------------------------


class TestDiffDirs:
    def test_matched_questions(self, tmp_path):
        a_dir = _write_result_dir(
            tmp_path,
            "commit1",
            [
                _make_result(question_id="q1", score=10),
                _make_result(question_id="q2", score=14, rubric="general"),
            ],
        )
        b_dir = _write_result_dir(
            tmp_path,
            "commit2",
            [
                _make_result(question_id="q1", score=12),
                _make_result(question_id="q2", score=14, rubric="general"),
            ],
        )
        diffs = diff_dirs(a_dir, b_dir)
        assert len(diffs) == 2
        q1 = next(d for d in diffs if d.question_id == "q1")
        assert q1.score_delta == 2
        assert not q1.only_in_a
        assert not q1.only_in_b

    def test_only_in_a(self, tmp_path):
        a_dir = _write_result_dir(
            tmp_path,
            "commit1",
            [
                _make_result(question_id="q1"),
                _make_result(question_id="q2"),
            ],
        )
        b_dir = _write_result_dir(
            tmp_path,
            "commit2",
            [
                _make_result(question_id="q1"),
            ],
        )
        diffs = diff_dirs(a_dir, b_dir)
        q2 = next(d for d in diffs if d.question_id == "q2")
        assert q2.only_in_a
        assert not q2.only_in_b

    def test_only_in_b(self, tmp_path):
        a_dir = _write_result_dir(
            tmp_path,
            "commit1",
            [
                _make_result(question_id="q1"),
            ],
        )
        b_dir = _write_result_dir(
            tmp_path,
            "commit2",
            [
                _make_result(question_id="q1"),
                _make_result(question_id="q3"),
            ],
        )
        diffs = diff_dirs(a_dir, b_dir)
        q3 = next(d for d in diffs if d.question_id == "q3")
        assert q3.only_in_b
        assert not q3.only_in_a


# ---------------------------------------------------------------------------
# format_diff
# ---------------------------------------------------------------------------


class TestFormatDiff:
    def test_includes_score_delta(self):
        diffs = [
            QuestionDiff(
                question_id="tyranitar-ou",
                rubric="pokemon",
                score_a=10,
                score_b=14,
                max_total=16,
                passed_a=False,
                passed_b=True,
                category_deltas=[],
                metric_deltas=[],
            )
        ]
        out = format_diff(diffs, "commit1", "commit2")
        assert "tyranitar-ou" in out
        assert "10" in out
        assert "14" in out
        assert "+4" in out

    def test_includes_pass_change(self):
        diffs = [
            QuestionDiff(
                question_id="q1",
                rubric="general",
                score_a=10,
                score_b=14,
                max_total=16,
                passed_a=False,
                passed_b=True,
                category_deltas=[],
                metric_deltas=[],
            )
        ]
        out = format_diff(diffs, "a", "b")
        assert "PASS" in out

    def test_includes_only_in_sections(self):
        diffs = [
            QuestionDiff(question_id="q1", rubric="general", only_in_a=True),
            QuestionDiff(question_id="q2", rubric="general", only_in_b=True),
        ]
        out = format_diff(diffs, "commitA", "commitB")
        assert "Only in commitA" in out
        assert "q1" in out
        assert "Only in commitB" in out
        assert "q2" in out

    def test_includes_category_deltas(self):
        diffs = [
            QuestionDiff(
                question_id="q1",
                rubric="general",
                score_a=10,
                score_b=12,
                max_total=15,
                passed_a=False,
                passed_b=True,
                category_deltas=[
                    CategoryDelta(name="Grounding", score_a=3, score_b=5, scale_max=5),
                ],
                metric_deltas=[],
            )
        ]
        out = format_diff(diffs, "a", "b")
        assert "Grounding" in out
        assert "+2" in out
