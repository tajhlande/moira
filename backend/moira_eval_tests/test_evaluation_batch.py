"""Tests for moira_eval.batch — batch evaluation of all benchmark questions.

Tests:
- find_run_for_question matches on prefix and returns latest
- find_run_for_question returns None when no match
- find_run_for_question handles prefix tolerance
- _evaluate_one captures + metrics + judge + save
- _evaluate_one handles capture errors
- _evaluate_one handles judge errors (fallback)
- _evaluate_one metrics-only (no judge config)
- _format_summary renders correct table
- CLI --only filters questions
- CLI --only with unknown ID errors
- CLI full batch run
"""

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moira_eval.batch import (
    BatchEntry,
    _evaluate_one,
    _format_summary,
    find_run_for_question,
)
from moira_eval.judge import JudgeCategoryScore, JudgeResult
from moira_eval.questions import QUESTIONS

# ---------------------------------------------------------------------------
# Schema + fixture DB
# ---------------------------------------------------------------------------


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE workflow_runs (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            user_message_id INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'completed',
            budget_limit REAL NOT NULL DEFAULT 0,
            total_cost REAL NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT '',
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
        CREATE TABLE tools (
            name TEXT PRIMARY KEY,
            description TEXT NOT NULL DEFAULT '',
            argument_schema TEXT NOT NULL DEFAULT '{}',
            config TEXT NOT NULL DEFAULT '{}',
            tags TEXT NOT NULL DEFAULT '[]',
            reliability TEXT NOT NULL DEFAULT 'unknown',
            is_default INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            implementation TEXT NOT NULL DEFAULT '',
            group_name TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.commit()


def _insert_run(
    conn: sqlite3.Connection,
    run_id: str,
    conv_id: str,
    user_content: str,
    status: str = "completed",
    started_at: str = "2025-01-01T00:00:00Z",
    knowledge: dict | None = None,
    report: dict | None = None,
) -> None:
    """Insert a conversation, user message, and workflow run."""
    conn.execute(
        "INSERT INTO conversations (id, user_id, title) VALUES (?, 'u1', 'test')",
        (conv_id,),
    )
    cursor = conn.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', ?)",
        (conv_id, user_content),
    )
    msg_id = cursor.lastrowid
    conn.execute(
        """
        INSERT INTO workflow_runs
            (id, conversation_id, user_message_id, status, started_at,
             knowledge_snapshot, report, budget_limit, total_cost)
        VALUES (?, ?, ?, ?, ?, ?, ?, 100.0, 25.5)
        """,
        (
            run_id,
            conv_id,
            msg_id,
            status,
            started_at,
            json.dumps(knowledge or {}),
            json.dumps(report or {}),
        ),
    )
    conn.commit()


@pytest.fixture
def batch_db(tmp_path: Path) -> Path:
    """Database with matching runs for all benchmark questions."""
    db_path = tmp_path / "batch_test.db"
    conn = sqlite3.connect(str(db_path))
    _create_schema(conn)

    for i, (qid, q) in enumerate(QUESTIONS.items()):
        _insert_run(
            conn,
            run_id=f"run-{qid}",
            conv_id=f"conv-{qid}",
            user_content=q.text,
            started_at=f"2025-01-0{i + 1}T00:00:00Z",
        )

    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# find_run_for_question
# ---------------------------------------------------------------------------


class TestFindRunForQuestion:
    def test_finds_matching_run(self, batch_db: Path):
        q = list(QUESTIONS.values())[0]
        run_id = find_run_for_question(str(batch_db), q.text)
        assert run_id == f"run-{q.id}"

    def test_returns_none_when_no_match(self, batch_db: Path):
        run_id = find_run_for_question(str(batch_db), "Nonexistent question text here")
        assert run_id is None

    def test_prefix_tolerance(self, batch_db: Path):
        """Matching works even with trailing whitespace differences."""
        q = list(QUESTIONS.values())[0]
        # Add trailing whitespace — prefix match should still work
        run_id = find_run_for_question(str(batch_db), q.text + "   \n")
        assert run_id == f"run-{q.id}"

    def test_returns_latest_when_multiple(self, batch_db: Path):
        """When multiple runs match, returns the most recent."""
        q = list(QUESTIONS.values())[0]
        conn = sqlite3.connect(str(batch_db))
        _insert_run(
            conn,
            run_id=f"run-newer-{q.id}",
            conv_id=f"conv-newer-{q.id}",
            user_content=q.text,
            started_at="2025-06-01T00:00:00Z",
        )
        conn.close()

        run_id = find_run_for_question(str(batch_db), q.text)
        assert run_id == f"run-newer-{q.id}"

    def test_ignores_non_completed(self, batch_db: Path):
        """Non-completed runs are not matched."""
        q = list(QUESTIONS.values())[0]
        conn = sqlite3.connect(str(batch_db))
        _insert_run(
            conn,
            run_id=f"run-error-{q.id}",
            conv_id=f"conv-error-{q.id}",
            user_content=q.text,
            status="error",
            started_at="2025-12-01T00:00:00Z",
        )
        conn.close()

        run_id = find_run_for_question(str(batch_db), q.text)
        assert run_id == f"run-{q.id}"  # still the completed one


# ---------------------------------------------------------------------------
# _evaluate_one
# ---------------------------------------------------------------------------


def _make_general_judge_result(total: int = 21) -> JudgeResult:
    return JudgeResult(
        categories=[
            JudgeCategoryScore(name="Grounding", score=4),
            JudgeCategoryScore(name="Fact atomicity", score=5),
            JudgeCategoryScore(name="Citation support", score=4),
            JudgeCategoryScore(name="Critique quality", score=3),
            JudgeCategoryScore(name="Goal alignment", score=5),
        ],
        overall_notes="Good run.",
        model="mock-judge",
        scale_max=5,
        hard_fail_categories=frozenset(),
        rubric="general",
    )


def _make_pokemon_judge_result() -> JudgeResult:
    return JudgeResult(
        categories=[
            JudgeCategoryScore(name="Tool choice", score=2),
            JudgeCategoryScore(name="Search discipline", score=2),
            JudgeCategoryScore(name="Type correctness", score=2),
            JudgeCategoryScore(name="Ability correctness", score=2),
            JudgeCategoryScore(name="Typical-move discipline", score=2),
            JudgeCategoryScore(name="OU legality and metagame", score=2),
            JudgeCategoryScore(name="Synthesis discipline", score=2),
            JudgeCategoryScore(name="Verification quality", score=2),
        ],
        overall_notes="Perfect.",
        model="mock-judge",
        scale_max=2,
        hard_fail_categories=frozenset(),
        rubric="pokemon",
    )


class TestEvaluateOne:
    def test_evaluate_one_with_judge(self, batch_db: Path, tmp_path: Path):
        q = list(QUESTIONS.values())[0]
        judge_result = (
            _make_general_judge_result() if q.rubric == "general" else _make_pokemon_judge_result()
        )
        judge_config = MagicMock()

        with (
            patch("moira_eval.batch.capture_artifacts") as mock_capture,
            patch("moira_eval.batch._run_judge", new_callable=AsyncMock) as mock_judge,
            patch("moira_eval.batch.save_result") as mock_save,
            patch("moira_eval.batch.save_meta") as mock_meta,
            patch("moira_eval.batch.compute_metrics") as mock_metrics,
        ):
            mock_capture.return_value = {"run_id": f"run-{q.id}"}
            mock_metrics.return_value = {"web_search_calls": 5, "unsupported_conclusion_count": 0}
            mock_judge.return_value = judge_result
            mock_save.return_value = tmp_path / "result.json"

            entry = asyncio.run(_evaluate_one(str(batch_db), f"run-{q.id}", q, judge_config))

            assert entry.status == "evaluated"
            assert entry.question_id == q.id
            assert entry.judge_result == judge_result
            mock_save.assert_called_once()
            mock_meta.assert_called_once()

    def test_evaluate_one_capture_error(self, batch_db: Path):
        q = list(QUESTIONS.values())[0]

        with patch("moira_eval.batch.capture_artifacts") as mock_capture:
            mock_capture.return_value = {"error": "Run not found"}
            entry = asyncio.run(_evaluate_one(str(batch_db), "bad-run", q, None))

        assert entry.status == "error"
        assert "Run not found" in entry.error

    def test_evaluate_one_no_judge_config(self, batch_db: Path, tmp_path: Path):
        """Metrics-only mode when judge_config is None."""
        q = list(QUESTIONS.values())[0]

        with (
            patch("moira_eval.batch.capture_artifacts") as mock_capture,
            patch("moira_eval.batch.save_result") as mock_save,
            patch("moira_eval.batch.save_meta"),
            patch("moira_eval.batch.compute_metrics") as mock_metrics,
        ):
            mock_capture.return_value = {"run_id": f"run-{q.id}"}
            mock_metrics.return_value = {"web_search_calls": 3}
            mock_save.return_value = tmp_path / "result.json"

            entry = asyncio.run(_evaluate_one(str(batch_db), f"run-{q.id}", q, None))

            assert entry.status == "evaluated"
            assert entry.judge_result is None
            assert entry.metrics is not None

    def test_evaluate_one_judge_error(self, batch_db: Path, tmp_path: Path):
        """Judge errors produce an error entry, not a crash."""
        from moira_eval.judge import JudgeError

        q = list(QUESTIONS.values())[0]

        with (
            patch("moira_eval.batch.capture_artifacts") as mock_capture,
            patch("moira_eval.batch._run_judge", new_callable=AsyncMock) as mock_judge,
            patch("moira_eval.batch.compute_metrics") as mock_metrics,
        ):
            mock_capture.return_value = {"run_id": f"run-{q.id}"}
            mock_metrics.return_value = {"web_search_calls": 3}
            mock_judge.side_effect = JudgeError("API failed")

            entry = asyncio.run(_evaluate_one(str(batch_db), f"run-{q.id}", q, MagicMock()))

            assert entry.status == "error"
            assert "Judge error" in entry.error


# ---------------------------------------------------------------------------
# _format_summary
# ---------------------------------------------------------------------------


class TestFormatSummary:
    def test_summary_with_evaluated(self):
        judge_result = _make_general_judge_result(total=21)
        entry = BatchEntry(
            question_id="test-q",
            run_id="run-1",
            status="evaluated",
            judge_result=judge_result,
            metrics={"web_search_calls": 8, "unsupported_conclusion_count": 2},
        )
        summary = _format_summary([entry])
        assert "test-q" in summary
        assert "21/25" in summary
        assert "PASS" in summary

    def test_summary_with_no_run(self):
        entry = BatchEntry(
            question_id="missing-q",
            run_id="",
            status="no_run",
        )
        summary = _format_summary([entry])
        assert "no run found" in summary

    def test_summary_with_error(self):
        entry = BatchEntry(
            question_id="bad-q",
            run_id="run-x",
            status="error",
            error="Something broke",
        )
        summary = _format_summary([entry])
        assert "ERROR" in summary
        assert "Something broke" in summary

    def test_summary_with_metrics_only(self):
        entry = BatchEntry(
            question_id="metrics-q",
            run_id="run-1",
            status="evaluated",
            judge_result=None,
            metrics={"web_search_calls": 5, "unsupported_conclusion_count": 1},
        )
        summary = _format_summary([entry])
        assert "metrics-only" in summary

    def test_summary_total_line(self):
        entries = [
            BatchEntry(question_id="a", run_id="r1", status="evaluated"),
            BatchEntry(question_id="b", run_id="", status="no_run"),
            BatchEntry(question_id="c", run_id="r3", status="error", error="x"),
        ]
        summary = _format_summary(entries)
        assert "1 evaluated" in summary
        assert "1 skipped" in summary
        assert "1 error" in summary


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLI:
    def test_cli_unknown_question_errors(self):
        with patch.object(sys, "argv", ["batch", "--db", "x.db", "--only", "nonexistent"]):
            with pytest.raises(SystemExit):
                from moira_eval.batch import main

                main()

    def test_cli_db_not_found_errors(self):
        with patch.object(sys, "argv", ["batch", "--db", "/nonexistent/path.db"]):
            with pytest.raises(SystemExit):
                from moira_eval.batch import main

                main()

    def test_cli_full_batch(self, batch_db: Path):
        """Full batch CLI run with mocked judge."""
        with (
            patch("moira_eval.batch.judge_config_from_env") as mock_env,
            patch("moira_eval.batch._run_judge", new_callable=AsyncMock) as mock_judge,
            patch("moira_eval.batch.save_result"),
            patch("moira_eval.batch.save_meta"),
        ):
            mock_env.return_value = MagicMock(model="mock-judge")

            # Return a different judge result per question
            async def fake_judge(config, text, artifacts, metrics, rubric):
                if rubric == "pokemon":
                    return _make_pokemon_judge_result()
                return _make_general_judge_result()

            mock_judge.side_effect = fake_judge

            with patch.object(sys, "argv", ["batch", "--db", str(batch_db)]):
                from moira_eval.batch import main

                main()  # should not raise

    def test_cli_only_filter(self, batch_db: Path):
        """--only filters to specific questions."""
        with (
            patch("moira_eval.batch.judge_config_from_env") as mock_env,
            patch("moira_eval.batch._evaluate_one", new_callable=AsyncMock) as mock_eval,
            patch.object(
                sys,
                "argv",
                [
                    "batch",
                    "--db",
                    str(batch_db),
                    "--only",
                    "tyranitar-ou,flaming-hot-cheetos",
                ],
            ),
        ):
            mock_env.return_value = MagicMock(model="mock-judge")
            mock_eval.return_value = BatchEntry(
                question_id="test",
                run_id="run-x",
                status="evaluated",
                judge_result=_make_general_judge_result(),
                metrics={"web_search_calls": 1, "unsupported_conclusion_count": 0},
            )

            from moira_eval.batch import main

            main()

            # Only 2 questions should have been evaluated
            assert mock_eval.call_count == 2
            called_ids = [call.args[2].id for call in mock_eval.call_args_list]
            assert "tyranitar-ou" in called_ids
            assert "flaming-hot-cheetos" in called_ids
