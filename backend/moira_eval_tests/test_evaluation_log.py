"""Tests for moira_eval.log: result logging to EVAL_LOG.md."""

from moira_eval.log import (
    _extract_commit_and_question_from_entry,
    append_to_log,
    format_log_entry,
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
# duplicate detection
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
        # Each entry ends with a blank line, so between them there
        # should be exactly one blank line separator.
        assert "\n\n\n## " not in content  # no triple newlines before entries


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
