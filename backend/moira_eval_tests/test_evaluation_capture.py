"""Tests for moira_eval.capture.

Builds a fixture SQLite database with knowledge snapshot, review/evaluation
step details, and tool results, then asserts that ``capture_artifacts``
extracts them correctly.
"""

import json
import sqlite3

import pytest

from moira_eval.capture import capture_artifacts

# ---------------------------------------------------------------------------
# Fixture DB helpers
# ---------------------------------------------------------------------------


def _create_schema(conn: sqlite3.Connection) -> None:
    """Create the minimal workflow_runs + workflow_steps tables."""
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
            user_message_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            budget_limit REAL NOT NULL DEFAULT 0,
            total_cost REAL NOT NULL DEFAULT 0,
            generation_reason TEXT,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            total_elapsed_ms INTEGER,
            updated_at TEXT,
            knowledge_snapshot TEXT,
            state_version INTEGER NOT NULL DEFAULT 1,
            report TEXT
        );
        CREATE TABLE workflow_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_run_id TEXT NOT NULL,
            node_name TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running',
            cost REAL NOT NULL DEFAULT 0,
            tool_call_cost REAL NOT NULL DEFAULT 0,
            budget_remaining REAL NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            elapsed_ms INTEGER NOT NULL DEFAULT 0,
            purpose TEXT,
            model TEXT,
            call_count INTEGER,
            input_tokens INTEGER,
            thinking_tokens INTEGER,
            output_tokens INTEGER,
            prompt_time_ms REAL,
            gen_time_ms REAL,
            error TEXT NOT NULL DEFAULT '',
            detail TEXT,
            tool_call_count INTEGER NOT NULL DEFAULT 0,
            step_version INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.commit()


def _make_knowledge_snapshot(
    *,
    facts: list[dict] | None = None,
    conclusions: list[dict] | None = None,
    citations: list[dict] | None = None,
    question: str = "Test question",
) -> str:
    """Build a JSON knowledge snapshot matching the Knowledge TypedDict shape."""
    return json.dumps(
        {
            "question": question,
            "user_goal": "Test goal",
            "topic": "test",
            "entities": [],
            "concepts": [],
            "facts": facts or [],
            "conclusions": conclusions or [],
            "citations": citations or [],
            "review_history": [],
            "evaluation_history": [],
        }
    )


def _make_review_detail(route: str = "continue") -> str:
    """Build a research_review step detail JSON."""
    return json.dumps(
        {
            "structured_output": {
                "fact_results": [{"fact_id": "f001", "result": "verified", "evidence": "ok"}],
                "coverage_assessment": "All covered",
                "missing_areas": [],
                "route": route,
            }
        }
    )


def _make_evaluation_detail(route: str = "accept") -> str:
    """Build an evaluation step detail JSON."""
    return json.dumps(
        {
            "structured_output": {
                "conclusion_results": [
                    {
                        "conclusion_id": "c001",
                        "result": "verified",
                        "reason": "ok",
                    }
                ],
                "goal_met": True,
                "goal_assessment": "Goal met",
                "route": route,
            }
        }
    )


def _make_research_step_detail(tool_name: str = "web_search") -> str:
    """Build a research step detail with tool_results."""
    return json.dumps(
        {
            "tool_results": [
                {
                    "tool": tool_name,
                    "args": {"query": "test"},
                    "result": "search result text",
                    "duration_ms": 100,
                    "success": True,
                }
            ]
        }
    )


def _insert_run(
    conn: sqlite3.Connection,
    run_id: str = "run-1",
    knowledge_snapshot: str | None = None,
    report: dict | None = None,
    total_cost: float = 30.0,
    budget_limit: float = 60.0,
    status: str = "completed",
) -> None:
    """Insert a workflow run row."""
    conn.execute(
        "INSERT INTO conversations (id, user_id) VALUES (?, ?)",
        ("conv-1", "default"),
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
        ("conv-1", "user", "test"),
    )
    conn.execute(
        "INSERT INTO workflow_runs "
        "(id, conversation_id, user_message_id, status, budget_limit, "
        "total_cost, knowledge_snapshot, report) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            "conv-1",
            1,
            status,
            budget_limit,
            total_cost,
            knowledge_snapshot,
            json.dumps(report) if report else None,
        ),
    )
    conn.commit()


def _insert_step(
    conn: sqlite3.Connection,
    run_id: str,
    node_name: str,
    detail: str | None = None,
    label: str = "",
    status: str = "completed",
    tool_call_count: int = 0,
) -> None:
    """Insert a workflow step row."""
    conn.execute(
        "INSERT INTO workflow_steps "
        "(workflow_run_id, node_name, label, status, detail, tool_call_count) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, node_name, label, status, detail, tool_call_count),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def db_with_run(tmp_path):
    """Build a fixture DB with a complete run including knowledge snapshot,
    review/eval steps with retries, and tool results."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    _create_schema(conn)

    knowledge = _make_knowledge_snapshot(
        facts=[
            {
                "id": "f001",
                "subject": "Tyranitar",
                "fact_needed": "typing",
                "claim": "Rock/Dark",
                "status": "verified",
                "citation_ids": ["cit001"],
            },
            {
                "id": "f002",
                "subject": "Tyranitar",
                "fact_needed": "weaknesses",
                "status": "unknown",
            },
            {
                "id": "f003",
                "subject": "Corviknight",
                "fact_needed": "typing",
                "claim": "Steel/Flying",
                "status": "contradicted",
                "verification_note": "Wrong typing",
            },
        ],
        conclusions=[
            {
                "id": "c001",
                "conclusion": "Corviknight is a good partner",
                "supporting_fact_ids": ["f001", "f003"],
                "citation_ids": ["cit001"],
                "status": "unsupported",
                "reasoning": "Type coverage",
            },
        ],
        citations=[
            {"id": "cit001", "source": "pokeapi", "url": "https://example.com"},
        ],
    )

    report = {
        "answer": "Test answer",
        "critiques": ["Claim X is weak"],
        "generation_reason": "budget_exhausted",
    }

    _insert_run(conn, knowledge_snapshot=knowledge, report=report)

    # Steps: research (with tools), research_review (x2 = retry), evaluation
    _insert_step(
        conn,
        "run-1",
        "research",
        detail=_make_research_step_detail("web_search"),
        tool_call_count=1,
    )
    _insert_step(
        conn,
        "run-1",
        "research",
        detail=_make_research_step_detail("pokeapi"),
        tool_call_count=1,
    )
    _insert_step(
        conn,
        "run-1",
        "research_review",
        detail=_make_review_detail(route="retry"),
    )
    _insert_step(
        conn,
        "run-1",
        "research_review",
        detail=_make_review_detail(route="continue"),
    )
    _insert_step(
        conn,
        "run-1",
        "evaluation",
        detail=_make_evaluation_detail(route="accept"),
    )

    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCaptureArtifacts:
    """Tests for capture_artifacts with a fixture database."""

    def test_returns_error_for_missing_run(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        _create_schema(conn)
        conn.close()

        result = capture_artifacts(db_path, run_id="nonexistent")
        assert "error" in result

    def test_extracts_run_metadata(self, db_with_run):
        artifacts = capture_artifacts(db_with_run, run_id="run-1")
        assert artifacts["run_id"] == "run-1"
        assert artifacts["status"] == "completed"
        assert artifacts["budget_limit"] == 60.0
        assert artifacts["budget_consumed"] == 30.0
        assert artifacts["generation_reason"] == "budget_exhausted"

    def test_extracts_knowledge_snapshot(self, db_with_run):
        artifacts = capture_artifacts(db_with_run, run_id="run-1")
        knowledge = artifacts["knowledge"]
        assert knowledge is not None
        assert knowledge["question"] == "Test question"
        assert len(knowledge["facts"]) == 3
        assert len(knowledge["conclusions"]) == 1
        assert knowledge["facts"][0]["id"] == "f001"

    def test_extracts_review_attempts_with_retries(self, db_with_run):
        artifacts = capture_artifacts(db_with_run, run_id="run-1")
        attempts = artifacts["review_attempts"]
        assert len(attempts) == 2, "Should capture both review attempts"
        assert attempts[0]["route"] == "retry"
        assert attempts[1]["route"] == "continue"

    def test_extracts_evaluation_attempts(self, db_with_run):
        artifacts = capture_artifacts(db_with_run, run_id="run-1")
        attempts = artifacts["evaluation_attempts"]
        assert len(attempts) == 1
        assert attempts[0]["route"] == "accept"
        assert attempts[0]["goal_met"] is True

    def test_extracts_tool_trace(self, db_with_run):
        artifacts = capture_artifacts(db_with_run, run_id="run-1")
        trace = artifacts["tool_trace"]
        assert len(trace) == 2
        tools = [t["tool"] for t in trace]
        assert "web_search" in tools
        assert "pokeapi" in tools

    def test_extracts_tool_counts(self, db_with_run):
        artifacts = capture_artifacts(db_with_run, run_id="run-1")
        assert artifacts["total_tool_calls"] == 2
        assert artifacts["web_search_calls"] == 1
        assert artifacts["url_content_calls"] == 0
        assert sorted(artifacts["tools_used"]) == ["pokeapi", "web_search"]

    def test_extracts_critiques_from_report(self, db_with_run):
        artifacts = capture_artifacts(db_with_run, run_id="run-1")
        assert artifacts["critiques"] == ["Claim X is weak"]

    def test_extracts_steps_summary(self, db_with_run):
        artifacts = capture_artifacts(db_with_run, run_id="run-1")
        summary = artifacts["steps_summary"]
        nodes = [s["node_name"] for s in summary]
        assert nodes.count("research") == 2
        assert nodes.count("research_review") == 2
        assert nodes.count("evaluation") == 1

    def test_latest_run_when_no_id_given(self, db_with_run):
        """When run_id is None, should pick the latest completed run."""
        artifacts = capture_artifacts(db_with_run)
        assert artifacts["run_id"] == "run-1"

    def test_handles_missing_knowledge_snapshot(self, tmp_path):
        """Run without knowledge snapshot should return None for knowledge."""
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        _create_schema(conn)
        _insert_run(conn, knowledge_snapshot=None)
        conn.close()

        artifacts = capture_artifacts(db_path, run_id="run-1")
        assert artifacts["knowledge"] is None
        assert artifacts["review_attempts"] == []
        assert artifacts["evaluation_attempts"] == []

    def test_handles_malformed_detail_json(self, tmp_path):
        """Malformed detail JSON should be skipped, not crash."""
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        _create_schema(conn)
        _insert_run(conn, knowledge_snapshot=None)
        _insert_step(
            conn,
            "run-1",
            "research_review",
            detail="{invalid json",
        )
        conn.close()

        artifacts = capture_artifacts(db_path, run_id="run-1")
        assert artifacts["review_attempts"] == []

    def test_normalizes_summary_shape_knowledge(self, tmp_path):
        """Knowledge snapshot stored as knowledge_summary() output (facts
        grouped by subject, conclusions by status) should be normalized to
        flat lists."""
        summary_snapshot = json.dumps(
            {
                "question": "Test",
                "user_goal": "Goal",
                "topic": "test",
                "entities": [],
                "concepts": [],
                "facts": {
                    "Tyranitar": [
                        {
                            "id": "f001",
                            "subject": "Tyranitar",
                            "fact_needed": "typing",
                            "status": "verified",
                            "citation_ids": ["cit001"],
                        }
                    ],
                    "Corviknight": [
                        {
                            "id": "f002",
                            "subject": "Corviknight",
                            "fact_needed": "typing",
                            "status": "unknown",
                        }
                    ],
                },
                "conclusions": {
                    "unsupported": [
                        {
                            "id": "c001",
                            "conclusion": "Best partner",
                            "supporting_fact_ids": ["f001"],
                            "status": "unsupported",
                        }
                    ]
                },
                "citations": [],
            }
        )
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        _create_schema(conn)
        _insert_run(conn, knowledge_snapshot=summary_snapshot)
        conn.close()

        artifacts = capture_artifacts(db_path, run_id="run-1")
        knowledge = artifacts["knowledge"]
        assert isinstance(knowledge["facts"], list)
        assert len(knowledge["facts"]) == 2
        assert isinstance(knowledge["conclusions"], list)
        assert len(knowledge["conclusions"]) == 1
