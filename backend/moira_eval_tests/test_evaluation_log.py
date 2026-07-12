"""Tests for moira_eval.log: result logging to EVAL_LOG.md."""

import json
import time

from moira_eval.log import (
    _extract_commit_and_question_from_entry,
    _find_latest_results_dir,
    _load_results_from_dir,
    append_to_log,
    format_batch_entry,
    format_log_entry,
    is_batch_duplicate,
    is_duplicate,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_result(
    question_id: str = "tyranitar-ou",
    commit_sha: str = "abc12345",
    rubric: str = "pokemon",
    score: int = 12,
    max_total: int = 16,
    passed: bool = False,
    metrics: dict | None = None,
    model: str = "test-agent-model",
) -> dict:
    if metrics is None:
        metrics = {
            "web_search_calls": 11,
            "unsupported_conclusion_count": 0,
            "hallucinated_fact_id_count": 0,
        }
    return {
        "question_id": question_id,
        "commit_sha": commit_sha,
        "rubric": rubric,
        "model": model,
        "metrics": metrics,
        "judge": {
            "total": score,
            "max_total": max_total,
            "passed": passed,
            "hard_fail_categories_failed": [],
            "categories": [],
            "overall_notes": "",
        },
    }


# ---------------------------------------------------------------------------
# format_log_entry
# ---------------------------------------------------------------------------


class TestFormatLogEntry:
    def test_basic_structure(self):
        result = _make_result()
        entry = format_log_entry(result, branch="main")
        assert "## " in entry
        assert "tyranitar-ou" in entry
        assert "abc12345" in entry
        assert "12/16" in entry
        assert "FAIL" in entry

    def test_pass_shown(self):
        result = _make_result(score=14, max_total=16, passed=True)
        entry = format_log_entry(result)
        assert "PASS" in entry

    def test_metrics_included(self):
        result = _make_result()
        entry = format_log_entry(result)
        assert "web_search_calls: 11" in entry
        assert "unsupported_conclusions: 0" in entry
        assert "hallucinated_ids: 0" in entry

    def test_note_included(self):
        result = _make_result()
        entry = format_log_entry(result, note="tightened synthesis")
        assert "Note:" in entry
        assert "tightened synthesis" in entry

    def test_no_note_omitted(self):
        result = _make_result()
        entry = format_log_entry(result)
        assert "Note:" not in entry

    def test_hard_fail_categories_listed(self):
        result = _make_result()
        result["judge"]["hard_fail_categories_failed"] = ["Type correctness"]
        entry = format_log_entry(result)
        assert "Hard-fail" in entry
        assert "Type correctness" in entry

    def test_metrics_only_result(self):
        result = _make_result()
        result["judge"] = None
        entry = format_log_entry(result)
        assert "metrics-only" in entry

    def test_branch_included(self):
        result = _make_result()
        entry = format_log_entry(result, branch="feature/test")
        assert "feature/test" in entry

    def test_branch_unknown_omitted(self):
        result = _make_result()
        entry = format_log_entry(result, branch="unknown")
        assert "unknown" not in entry


# ---------------------------------------------------------------------------
# format_batch_entry
# ---------------------------------------------------------------------------


class TestFormatBatchEntry:
    def test_basic_structure(self):
        results = [
            _make_result(question_id="q1", score=18, max_total=25, passed=True),
            _make_result(question_id="q2", score=12, max_total=16, passed=False),
        ]
        entry = format_batch_entry(results, branch="main")
        assert "## " in entry
        assert "batch" in entry
        assert "abc12345" in entry
        assert "| Question |" in entry
        assert "|----------|" in entry
        assert "q1" in entry
        assert "q2" in entry
        assert "18/25" in entry
        assert "12/16" in entry
        assert "PASS" in entry
        assert "FAIL" in entry

    def test_commit_from_first_result(self):
        results = [
            _make_result(question_id="q1", commit_sha="deadbeef"),
            _make_result(question_id="q2", commit_sha="deadbeef"),
        ]
        entry = format_batch_entry(results)
        assert "deadbeef" in entry

    def test_agent_model_included(self):
        results = [_make_result(model="qwen-35b")]
        entry = format_batch_entry(results)
        assert "qwen-35b" in entry

    def test_note_included(self):
        results = [_make_result()]
        entry = format_batch_entry(results, note="searxng fix")
        assert "Note:" in entry
        assert "searxng fix" in entry

    def test_no_note_omitted(self):
        results = [_make_result()]
        entry = format_batch_entry(results)
        assert "Note:" not in entry

    def test_branch_included(self):
        results = [_make_result()]
        entry = format_batch_entry(results, branch="feature/x")
        assert "feature/x" in entry

    def test_branch_unknown_omitted(self):
        results = [_make_result()]
        entry = format_batch_entry(results, branch="unknown")
        assert "unknown" not in entry

    def test_metrics_only_result_in_table(self):
        results = [_make_result()]
        results[0]["judge"] = None
        entry = format_batch_entry(results)
        assert "metrics-only" in entry

    def test_web_search_in_table(self):
        results = [
            _make_result(
                metrics={
                    "web_search_calls": 7,
                    "unsupported_conclusion_count": 1,
                    "hallucinated_fact_id_count": 0,
                }
            )
        ]
        entry = format_batch_entry(results)
        assert "| 7 |" in entry

    def test_rubric_in_table(self):
        results = [_make_result(rubric="general")]
        entry = format_batch_entry(results)
        assert "| general |" in entry


# ---------------------------------------------------------------------------
# single-question duplicate detection
# ---------------------------------------------------------------------------


class TestDuplicateDetection:
    def test_no_log_file_is_not_duplicate(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        assert not is_duplicate(log_path, "abc12345", "tyranitar-ou")

    def test_existing_entry_is_duplicate(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        entry = format_log_entry(_make_result(), branch="main")
        append_to_log(log_path, entry)
        assert is_duplicate(log_path, "abc12345", "tyranitar-ou")

    def test_different_question_not_duplicate(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        entry = format_log_entry(_make_result(), branch="main")
        append_to_log(log_path, entry)
        assert not is_duplicate(log_path, "abc12345", "different-q")

    def test_different_commit_not_duplicate(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        entry = format_log_entry(_make_result(), branch="main")
        append_to_log(log_path, entry)
        assert not is_duplicate(log_path, "different123", "tyranitar-ou")

    def test_extract_from_real_entry(self):
        entry = format_log_entry(_make_result(), branch="main")
        pairs = _extract_commit_and_question_from_entry(entry)
        assert ("abc12345", "tyranitar-ou") in pairs


# ---------------------------------------------------------------------------
# batch duplicate detection
# ---------------------------------------------------------------------------


class TestBatchDuplicateDetection:
    def test_no_log_file_is_not_duplicate(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        assert not is_batch_duplicate(log_path, "abc12345")

    def test_existing_batch_is_duplicate(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        results = [_make_result()]
        entry = format_batch_entry(results, branch="main")
        append_to_log(log_path, entry)
        assert is_batch_duplicate(log_path, "abc12345")

    def test_different_commit_not_duplicate(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        results = [_make_result()]
        entry = format_batch_entry(results, branch="main")
        append_to_log(log_path, entry)
        assert not is_batch_duplicate(log_path, "different123")

    def test_single_question_entry_not_batch_duplicate(self, tmp_path):
        """A single-question entry should not trigger batch duplicate."""
        log_path = tmp_path / "EVAL_LOG.md"
        entry = format_log_entry(_make_result(), branch="main")
        append_to_log(log_path, entry)
        assert not is_batch_duplicate(log_path, "abc12345")


# ---------------------------------------------------------------------------
# append_to_log
# ---------------------------------------------------------------------------


class TestAppendToLog:
    def test_creates_new_file_with_header(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        entry = format_log_entry(_make_result())
        append_to_log(log_path, entry)
        content = log_path.read_text(encoding="utf-8")
        assert "# EVAL_LOG" in content
        assert "tyranitar-ou" in content

    def test_appends_to_existing(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        entry1 = format_log_entry(_make_result(question_id="q1"), branch="main")
        entry2 = format_log_entry(_make_result(question_id="q2"), branch="main")
        append_to_log(log_path, entry1)
        append_to_log(log_path, entry2)
        content = log_path.read_text(encoding="utf-8")
        assert "q1" in content
        assert "q2" in content

    def test_blank_line_between_entries(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        entry1 = format_log_entry(_make_result(question_id="q1"), branch="main")
        entry2 = format_log_entry(_make_result(question_id="q2"), branch="main")
        append_to_log(log_path, entry1)
        append_to_log(log_path, entry2)
        content = log_path.read_text(encoding="utf-8")
        assert "\n\n\n## " not in content

    def test_batch_entry_header(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        results = [_make_result()]
        entry = format_batch_entry(results, branch="main")
        append_to_log(log_path, entry)
        content = log_path.read_text(encoding="utf-8")
        assert "# EVAL_LOG" in content
        assert "batch" in content


# ---------------------------------------------------------------------------
# _find_latest_results_dir
# ---------------------------------------------------------------------------


class TestFindLatestResultsDir:
    def test_no_root_returns_none(self, tmp_path):
        results_root = tmp_path / "results"
        assert _find_latest_results_dir(results_root) is None

    def test_empty_root_returns_none(self, tmp_path):
        results_root = tmp_path / "results"
        results_root.mkdir()
        assert _find_latest_results_dir(results_root) is None

    def test_finds_most_recent(self, tmp_path):
        results_root = tmp_path / "results"
        old_dir = results_root / "aaa11111"
        new_dir = results_root / "bbb22222"
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)
        # Ensure new_dir has a later mtime
        now = time.time()
        import os

        os.utime(old_dir, (now - 100, now - 100))
        os.utime(new_dir, (now, now))

        result = _find_latest_results_dir(results_root)
        assert result == new_dir

    def test_ignores_files(self, tmp_path):
        results_root = tmp_path / "results"
        results_root.mkdir()
        (results_root / "stray.txt").write_text("ignore me")
        sub = results_root / "ccc33333"
        sub.mkdir()
        result = _find_latest_results_dir(results_root)
        assert result == sub


# ---------------------------------------------------------------------------
# _load_results_from_dir
# ---------------------------------------------------------------------------


class TestLoadResultsFromDir:
    def test_loads_json_files(self, tmp_path):
        d = tmp_path / "results" / "abc12345"
        d.mkdir(parents=True)
        r1 = _make_result(question_id="q1")
        r2 = _make_result(question_id="q2")
        (d / "q1.json").write_text(json.dumps(r1))
        (d / "q2.json").write_text(json.dumps(r2))

        results = _load_results_from_dir(d)
        assert len(results) == 2
        assert results[0]["question_id"] == "q1"
        assert results[1]["question_id"] == "q2"

    def test_excludes_meta_json(self, tmp_path):
        d = tmp_path / "results" / "abc12345"
        d.mkdir(parents=True)
        (d / "q1.json").write_text(json.dumps(_make_result()))
        (d / "meta.json").write_text(json.dumps({"commit": "abc12345"}))

        results = _load_results_from_dir(d)
        assert len(results) == 1
        assert results[0]["question_id"] == "tyranitar-ou"

    def test_sorted_alphabetically(self, tmp_path):
        d = tmp_path / "results" / "abc12345"
        d.mkdir(parents=True)
        (d / "zebra.json").write_text(json.dumps(_make_result(question_id="zebra")))
        (d / "apple.json").write_text(json.dumps(_make_result(question_id="apple")))

        results = _load_results_from_dir(d)
        assert results[0]["question_id"] == "apple"
        assert results[1]["question_id"] == "zebra"

    def test_empty_dir(self, tmp_path):
        d = tmp_path / "results" / "abc12345"
        d.mkdir(parents=True)
        results = _load_results_from_dir(d)
        assert results == []


# ---------------------------------------------------------------------------
# Integration: format -> append -> detect
# ---------------------------------------------------------------------------


class TestLogIntegration:
    def test_format_append_detect_cycle(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        result = _make_result()
        entry = format_log_entry(result, branch="main")
        append_to_log(log_path, entry)
        assert is_duplicate(log_path, result["commit_sha"], result["question_id"])

    def test_multiple_different_entries(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        for qid, commit in [("q1", "aaa11111"), ("q2", "bbb22222"), ("q3", "ccc33333")]:
            result = _make_result(question_id=qid, commit_sha=commit)
            entry = format_log_entry(result, branch="main")
            append_to_log(log_path, entry)

        for qid, commit in [("q1", "aaa11111"), ("q2", "bbb22222"), ("q3", "ccc33333")]:
            assert is_duplicate(log_path, commit, qid)

    def test_batch_format_append_detect_cycle(self, tmp_path):
        log_path = tmp_path / "EVAL_LOG.md"
        results = [
            _make_result(question_id="q1", commit_sha="abc12345"),
            _make_result(question_id="q2", commit_sha="abc12345"),
        ]
        entry = format_batch_entry(results, branch="main")
        append_to_log(log_path, entry)
        assert is_batch_duplicate(log_path, "abc12345")

    def test_batch_then_single_question(self, tmp_path):
        """Batch entry should not prevent a single-question entry for
        a different commit."""
        log_path = tmp_path / "EVAL_LOG.md"
        results = [_make_result(question_id="q1", commit_sha="abc12345")]
        batch_entry = format_batch_entry(results, branch="main")
        append_to_log(log_path, batch_entry)

        # Different commit — should not be a duplicate
        assert not is_duplicate(log_path, "ddd44444", "q2")
