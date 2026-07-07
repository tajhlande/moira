"""Tests for moira_eval.run CLI and moira_eval.result storage.

Tests:
- Metrics-only fallback (no env vars, no --question-id)
- Unknown question-id error
- Judged run writes result file with correct structure
- Result file is JSON-serializable and contains expected fields
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from moira_eval.judge import JudgeCategoryScore, JudgeResult
from moira_eval.result import build_result, get_commit_sha, save_result
from moira_eval.rubric_pokemon import CANARY_QUESTION
from moira_eval.run import main

# ---------------------------------------------------------------------------
# Fixture DB (reuses the pattern from test_evaluation_capture.py)
# ---------------------------------------------------------------------------


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE workflow_runs (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            user_message_id INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'completed',
            started_at TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT '',
            total_elapsed_ms REAL NOT NULL DEFAULT 0,
            budget_limit REAL NOT NULL DEFAULT 0,
            total_cost REAL NOT NULL DEFAULT 0,
            knowledge_snapshot TEXT,
            report TEXT
        );
        CREATE TABLE workflow_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_run_id TEXT NOT NULL,
            node_name TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            cost REAL NOT NULL DEFAULT 0,
            tool_call_count INTEGER NOT NULL DEFAULT 0,
            elapsed_ms REAL NOT NULL DEFAULT 0,
            model TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            detail TEXT
        );
        """
    )


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    """Create a minimal fixture database with one completed run."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    _create_schema(conn)

    knowledge = {
        "question": "Test question about Tyranitar?",
        "facts": [
            {
                "id": "f1",
                "subject": "Tyranitar",
                "predicate": "type",
                "object": "Rock/Dark",
                "status": "verified",
            },
        ],
        "conclusions": [
            {
                "id": "c1",
                "text": "Tyranitar is Rock/Dark type",
                "status": "verified",
                "supporting_fact_ids": ["f1"],
            },
        ],
    }
    report = {
        "answer": "Tyranitar is a Rock/Dark type Pokemon.",
        "generation_reason": "completed",
        "critiques": [],
    }

    conn.execute(
        """
        INSERT INTO workflow_runs
            (id, conversation_id, status, knowledge_snapshot, report, budget_limit, total_cost)
        VALUES (?, ?, 'completed', ?, ?, 100.0, 25.5)
        """,
        ("run-001", "conv-001", json.dumps(knowledge), json.dumps(report)),
    )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Result storage tests
# ---------------------------------------------------------------------------


class TestResultStorage:
    def test_build_result_with_judge(self, fixture_db):
        from moira_eval.capture import capture_artifacts
        from moira_eval.metrics import compute_metrics

        artifacts = capture_artifacts(str(fixture_db), "run-001")
        metrics = compute_metrics(artifacts)

        judge_result = JudgeResult(
            categories=[
                JudgeCategoryScore(name="Tool choice", score=2, rationale="Good"),
                JudgeCategoryScore(name="Search discipline", score=2, rationale="Good"),
                JudgeCategoryScore(name="Type correctness", score=2, rationale="Good"),
                JudgeCategoryScore(name="Ability correctness", score=2, rationale="Good"),
                JudgeCategoryScore(name="Typical-move discipline", score=2, rationale="Good"),
                JudgeCategoryScore(name="OU legality and metagame", score=2, rationale="Good"),
                JudgeCategoryScore(name="Synthesis discipline", score=1, rationale="Some issues"),
                JudgeCategoryScore(name="Verification quality", score=2, rationale="Good"),
            ],
            overall_notes="Minor synthesis issues.",
            model="test-judge-model",
        )

        result = build_result(
            question_id="tyranitar-ou",
            question_text=CANARY_QUESTION,
            artifacts=artifacts,
            metrics=metrics,
            judge_result=judge_result,
            commit_sha="abc12345",
        )

        assert result.question_id == "tyranitar-ou"
        assert result.run_id == "run-001"
        assert result.commit_sha == "abc12345"
        assert result.rubric == "pokemon"
        assert result.judge is not None
        assert result.judge["total"] == 15
        assert result.judge["max_total"] == 16
        assert result.judge["passed"] is True
        assert result.model == "test-judge-model"
        assert len(result.judge["categories"]) == 8

    def test_build_result_metrics_only(self, fixture_db):
        from moira_eval.capture import capture_artifacts
        from moira_eval.metrics import compute_metrics

        artifacts = capture_artifacts(str(fixture_db), "run-001")
        metrics = compute_metrics(artifacts)

        result = build_result(
            question_id="adhoc-run-001",
            question_text="ad hoc question",
            artifacts=artifacts,
            metrics=metrics,
            judge_result=None,
            commit_sha="abc12345",
        )

        assert result.judge is None
        assert result.model == ""
        assert result.rubric == "pokemon"  # default

    def test_save_result_writes_json(self, tmp_path, fixture_db):
        from moira_eval.capture import capture_artifacts
        from moira_eval.metrics import compute_metrics

        artifacts = capture_artifacts(str(fixture_db), "run-001")
        metrics = compute_metrics(artifacts)

        result = build_result(
            question_id="tyranitar-ou",
            question_text=CANARY_QUESTION,
            artifacts=artifacts,
            metrics=metrics,
            judge_result=None,
            commit_sha="abc12345",
        )

        results_dir = tmp_path / "results"
        out_path = save_result(result, results_dir)

        assert out_path.exists()
        assert out_path == results_dir / "abc12345" / "tyranitar-ou.json"

        data = json.loads(out_path.read_text())
        assert data["question_id"] == "tyranitar-ou"
        assert data["run_id"] == "run-001"
        assert data["commit_sha"] == "abc12345"
        assert "metrics" in data
        assert "timestamp" in data

    def test_get_commit_sha_returns_string(self):
        sha = get_commit_sha()
        assert isinstance(sha, str)
        assert len(sha) > 0

    def test_get_commit_sha_short_is_8_chars(self):
        """Short SHA should be 8 chars (or 'unknown')."""
        sha = get_commit_sha(short=True)
        assert sha == "unknown" or len(sha) == 8


# ---------------------------------------------------------------------------
# CLI tests (run.main with mocked argv)
# ---------------------------------------------------------------------------


class TestCLIMetricsOnly:
    def test_no_question_id_no_env_metrics_only(self, fixture_db, capsys):
        """Without --question-id, prints metrics-only summary."""
        with patch.dict("os.environ", {}, clear=True):
            sys.argv = ["moira_eval.run", "--db", str(fixture_db), "--run-id", "run-001"]
            main()

        captured = capsys.readouterr()
        assert "run-001" in captured.out
        assert "web_search=" in captured.out

    def test_question_id_no_env_vars_metrics_only(self, fixture_db, capsys):
        """--question-id without judge env vars falls back to metrics-only."""
        with patch.dict("os.environ", {}, clear=True):
            sys.argv = [
                "moira_eval.run",
                "--db",
                str(fixture_db),
                "--run-id",
                "run-001",
                "--question-id",
                "tyranitar-ou",
            ]
            main()

        captured = capsys.readouterr()
        # Should warn about missing env vars
        assert "WARNING" in captured.err
        assert "metrics-only" in captured.err.lower()
        # Should still print metrics
        assert "web_search=" in captured.out


class TestCLIJudgedRun:
    def test_judged_run_writes_result_file(self, fixture_db, tmp_path, capsys):
        """With env vars + question-id, judge runs and result file is written."""
        mock_judge_result = JudgeResult(
            categories=[
                JudgeCategoryScore(name="Tool choice", score=2, rationale="Good"),
                JudgeCategoryScore(name="Search discipline", score=2, rationale="Good"),
                JudgeCategoryScore(name="Type correctness", score=2, rationale="Good"),
                JudgeCategoryScore(name="Ability correctness", score=2, rationale="Good"),
                JudgeCategoryScore(name="Typical-move discipline", score=2, rationale="Good"),
                JudgeCategoryScore(name="OU legality and metagame", score=2, rationale="Good"),
                JudgeCategoryScore(name="Synthesis discipline", score=1, rationale="Some issues"),
                JudgeCategoryScore(name="Verification quality", score=2, rationale="Good"),
            ],
            overall_notes="Good run.",
            model="mock-model",
        )

        env = {
            "MOIRA_EVAL_JUDGE_ENDPOINT": "http://mock",
            "MOIRA_EVAL_JUDGE_MODEL": "mock-model",
            "MOIRA_EVAL_JUDGE_API_KEY": "mock-key",
        }

        with (
            patch.dict("os.environ", env),
            patch("moira_eval.run.save_result") as mock_save,
            patch("moira_eval.run._run_judge", new_callable=AsyncMock) as mock_judge,
        ):
            mock_save.return_value = tmp_path / "results" / "abc12345" / "tyranitar-ou.json"
            mock_save.return_value.parent.mkdir(parents=True, exist_ok=True)
            mock_judge.return_value = mock_judge_result

            sys.argv = [
                "moira_eval.run",
                "--db",
                str(fixture_db),
                "--run-id",
                "run-001",
                "--question-id",
                "tyranitar-ou",
            ]
            main()

        captured = capsys.readouterr()
        assert "tyranitar-ou: 15/16 (PASS)" in captured.out
        assert "-> " in captured.out

        # Verify save_result was called with correct question_id
        mock_save.assert_called_once()
        result_arg = mock_save.call_args.args[0]
        assert result_arg.question_id == "tyranitar-ou"
        assert result_arg.judge["total"] == 15

    def test_unknown_question_id_exits_with_error(self, fixture_db, capsys):
        env = {
            "MOIRA_EVAL_JUDGE_ENDPOINT": "http://mock",
            "MOIRA_EVAL_JUDGE_MODEL": "mock-model",
        }
        with patch.dict("os.environ", env):
            sys.argv = [
                "moira_eval.run",
                "--db",
                str(fixture_db),
                "--run-id",
                "run-001",
                "--question-id",
                "nonexistent",
            ]
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "Unknown question-id" in captured.err
        assert "nonexistent" in captured.err

    def test_judge_failure_falls_back_to_metrics(self, fixture_db, capsys):
        """When the judge raises JudgeError, falls back to metrics-only."""
        from moira_eval.judge import JudgeError

        env = {
            "MOIRA_EVAL_JUDGE_ENDPOINT": "http://mock",
            "MOIRA_EVAL_JUDGE_MODEL": "mock-model",
            "MOIRA_EVAL_JUDGE_API_KEY": "mock-key",
        }

        mock_run_judge = AsyncMock(side_effect=JudgeError("parse failed"))
        with (
            patch.dict("os.environ", env),
            patch("moira_eval.run._run_judge", mock_run_judge),
        ):
            sys.argv = [
                "moira_eval.run",
                "--db",
                str(fixture_db),
                "--run-id",
                "run-001",
                "--question-id",
                "tyranitar-ou",
            ]
            main()

        captured = capsys.readouterr()
        assert "Judge error" in captured.err
        assert "web_search=" in captured.out

    def test_missing_db_exits_with_error(self, tmp_path, capsys):
        sys.argv = ["moira_eval.run", "--db", str(tmp_path / "nonexistent.db")]
        with pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert "Database not found" in captured.err
