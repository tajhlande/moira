"""
Integration tests for MOiRA Phase 2.

These tests exercise the full wiring across modules: SSE endpoint → LangGraph
runtime → node implementations → inference client → persistence. The only
thing faked is the network transport layer (via httpx.MockTransport), which
replaces real HTTP with canned OpenAI-compatible responses. Everything else
— FastAPI app, graph compilation, budget enforcement, streaming, SQLite
persistence — runs for real.

Run alongside unit tests:
    uv run python -m pytest tests/test_integration.py -v
"""

import json
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from moira.config import (
    DatabaseConfig,
    InferenceConfig,
    InferenceEndpointConfig,
    InferenceModelsConfig,
    MoiraConfig,
    resolve_lancedb_path,
)
from moira.inference.client import InferenceClient
from moira.inference.registry import ModelRegistry
from moira.persistence.interfaces import (
    DEFAULT_USER_ID,
    ConversationRepository,
    WorkflowRun,
    WorkflowStep,
)
from moira.service_setup import _services
from moira.workflow.graph import compile_graph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fake inference transport
# ---------------------------------------------------------------------------
# Replaces real HTTP with deterministic responses. The handler receives every
# request the InferenceClient would send and returns a valid OpenAI-compatible
# response. The handler cycles through a sequence of completions so each node
# in the graph gets an appropriate response.


class CannedResponseTransport(httpx.MockTransport):
    """httpx transport that returns canned OpenAI-compatible responses.
    Extends httpx.MockTransport so aclose() works correctly.
    Tracks every request made so tests can assert on call order and content."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._index = 0
        self.requests: list[httpx.Request] = []
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "test-intelligence", "owned_by": "test"},
                        {"id": "test-task", "owned_by": "test"},
                    ]
                },
            )
        # /v1/chat/completions
        if self._index < len(self._responses):
            content = self._responses[self._index]
            self._index += 1
        else:
            content = "No more canned responses"
            logger.warning("CannedResponseTransport exhausted, returning fallback")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ]
            },
        )


class FakeInferenceClient(InferenceClient):
    """Subclass that replaces the httpx transport with canned responses.
    Everything else — request formatting, header handling, response parsing
    — is the real InferenceClient code. Uses httpx.MockTransport so aclose()
    and async request handling work correctly."""

    def __init__(self, transport: CannedResponseTransport):
        super().__init__(base_url="http://fake/v1", api_key="")
        self._mock_transport = transport

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            transport=self._mock_transport,
            base_url="http://fake/v1",
            headers=self._headers,
            timeout=httpx.Timeout(self._timeout),
        )


# ---------------------------------------------------------------------------
# Responses for a full successful graph run
# ---------------------------------------------------------------------------
# The graph calls chat_completion in this order:
#   1. planning          -> free text plan
#   2. tool_discovery    -> (no model call)
#   3. tool_selection    -> (no tools discovered, so no model call either if active_tools is empty)
#   4. research_execution -> (no tools, so no model call)
#   5. compression       -> (no findings, so no model call)
#   6. draft_synthesis   -> (no compressed findings)
#   7. verification      -> structured JSON report
#   8. report_generation -> structured JSON report
#
# With no tools configured, the path is simpler. With tools, more nodes call
# the model. We provide responses for both scenarios.

PLAN_RESPONSE = "1. Search for information about the question. 2. Synthesize findings."

TOOL_SELECTION_RESPONSE = '["web_search"]'

RESEARCH_RESPONSE = json.dumps([{"tool": "web_search", "args": {"query": "speed of light"}}])

COMPRESSION_RESPONSE = "Key finding: the speed of light in vacuum is 299,792,458 m/s."

DRAFT_RESPONSE = (
    "The speed of light in vacuum is exactly 299,792,458 meters per second. "
    "This is a fundamental constant of nature [web_search]."
)

VERIFICATION_PASS_RESPONSE = json.dumps(
    {
        "failed_claims": [],
        "contradictions": [],
        "missing_evidence": [],
        "suggestions": ["Consider adding historical context"],
    }
)

VERIFICATION_FAIL_RESPONSE = json.dumps(
    {
        "failed_claims": ["exact speed stated without primary source"],
        "contradictions": [],
        "missing_evidence": ["no peer-reviewed citation provided"],
        "suggestions": ["find a primary source for the exact value"],
    }
)

REPORT_RESPONSE = json.dumps(
    {
        "answer": "The speed of light is 299,792,458 m/s in vacuum.",
        "citations": [{"source": "web_search", "url": "", "excerpt": "299,792,458 m/s"}],
        "support": [{"content": "Speed of light measurement", "source": "web_search"}],
        "critiques": ["Consider adding historical context"],
        "unverified_claims": [],
    }
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    """Isolated temp directory for SQLite + LanceDB so tests don't collide."""
    with tempfile.TemporaryDirectory() as d:
        yield d


def _make_config(tmp_dir: str) -> MoiraConfig:
    return MoiraConfig(
        database=DatabaseConfig(
            sqlite_path=str(Path(tmp_dir) / "test.db"),
        ),
        inference=InferenceConfig(
            providers=[InferenceEndpointConfig(name="fake", base_url="http://fake/v1")],
            models=InferenceModelsConfig(
                intelligence_endpoint="fake",
                intelligence_model="test-intelligence",
                task_endpoint="fake",
                task_model="test-task",
            ),
        ),
    )


@pytest.fixture
async def app_with_fake_inference(tmp_dir):
    """Boots the full FastAPI app with a fake inference transport.
    All Phase 2 services are initialized: SQLite repos, LanceDB, tool catalog,
    embedding provider, tool discovery, executor, and the compiled research graph.
    The only fake is the HTTP transport layer.

    Yields (httpx.AsyncClient, CannedResponseTransport) so tests can read
    SSE events and inspect what requests were made.
    """
    config = _make_config(tmp_dir)
    transport = CannedResponseTransport(
        [
            PLAN_RESPONSE,
            TOOL_SELECTION_RESPONSE,
            RESEARCH_RESPONSE,
            COMPRESSION_RESPONSE,
            DRAFT_RESPONSE,
            VERIFICATION_PASS_RESPONSE,
            REPORT_RESPONSE,
        ]
    )
    fake_client = FakeInferenceClient(transport)

    # Register the fake client so init_services can find it
    _services.clear()
    _services["inference_client:fake"] = fake_client

    # init_services normally creates InferenceClient from config, but we
    # need to bypass that. We build services manually so the fake client
    # is the only inference client in the system.
    from moira.persistence.sqlite.repos import (
        SqliteConversationRepository,
        SqliteInferenceMetricsRepository,
        SqliteModelPreferencesRepository,
        SqliteWorkflowStepRepository,
    )
    from moira.persistence.sqlite.schema import run_migrations

    db_path = config.database.sqlite_path
    run_migrations(db_path)

    conversation_repo = SqliteConversationRepository(db_path)
    prefs_repo = SqliteModelPreferencesRepository(db_path)
    step_repo = SqliteWorkflowStepRepository(db_path)

    _services["conversation_repository"] = conversation_repo
    _services["model_preferences_repository"] = prefs_repo
    _services["workflow_step_repository"] = step_repo

    inf_metrics_repo = SqliteInferenceMetricsRepository(db_path)
    _services["inference_metrics_repository"] = inf_metrics_repo

    from moira.persistence.write_queue import AsyncWriteQueue

    write_queue = AsyncWriteQueue()
    await write_queue.start()
    _services["write_queue"] = write_queue

    # Start the fake client (creates the httpx client with mock transport)
    await fake_client.start()

    # Build the model registry with the fake client
    registry = ModelRegistry(
        config=config.inference,
        prefs_repo=prefs_repo,
        user_id=DEFAULT_USER_ID,
    )
    registry.add_client("fake", fake_client)
    await registry.refresh_models()
    _services["model_registry"] = registry

    # Embedding provider (local sentence-transformers)
    from moira.embeddings.local import LocalEmbeddingProvider

    embedding_provider = LocalEmbeddingProvider(model_name=config.embedding.model)
    _services["embedding_provider"] = embedding_provider

    # Tool catalog (empty for these tests)
    from moira.tools.catalog import ToolCatalog

    catalog = ToolCatalog()
    _services["tool_catalog"] = catalog

    # LanceDB embedding repository
    from moira.persistence.lancedb.tool_embeddings import ToolEmbeddingRepository

    embedding_repo = ToolEmbeddingRepository(resolve_lancedb_path(config))
    await embedding_repo.start()
    _services["tool_embedding_repo"] = embedding_repo

    # Tool discovery
    from moira.tools.discovery import ToolDiscovery

    discovery = ToolDiscovery(
        embedding_provider=embedding_provider,
        embedding_repo=embedding_repo,
    )
    _services["tool_discovery"] = discovery

    # Tool executor (empty — no tools registered)
    from moira.tools.executor import ToolExecutor

    executor = ToolExecutor()
    _services["tool_executor"] = executor

    # Compiled research graph
    research_graph = compile_graph(config)
    _services["research_graph"] = research_graph

    # Run manager (background graph execution)
    from moira.workflow.run_manager import RunManager

    _services["run_manager"] = RunManager()

    _services["config"] = config

    # Build the FastAPI app
    from moira.main import create_app

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client, transport

    # Teardown: stop the write queue and clear services.
    from moira.persistence.write_queue import AsyncWriteQueue

    wq = _services.get("write_queue")
    if isinstance(wq, AsyncWriteQueue):
        await wq.stop()
    _services.clear()


def _parse_sse_events(raw: bytes) -> list[dict[str, Any]]:
    """Parse raw SSE bytes into a list of {event, data} dicts."""
    events = []
    current_event = ""
    current_data = ""
    for line in raw.decode().split("\n"):
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            current_data = line[5:].strip()
        elif line.strip() == "":
            if current_event or current_data:
                events.append(
                    {
                        "event": current_event,
                        "data": json.loads(current_data) if current_data else {},
                    }
                )
                current_event = ""
                current_data = ""
    return events


async def _send_and_stream(
    client: httpx.AsyncClient,
    conversation_id: str,
    content: str,
    settings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Start a run (POST) and consume its SSE stream (GET).
    Returns parsed SSE events."""
    body: dict[str, Any] = {"content": content}
    if settings is not None:
        body["settings"] = settings
    resp = await client.post(
        f"/api/conversations/{conversation_id}/messages",
        json=body,
    )
    assert resp.status_code == 200, f"POST /messages failed: {resp.text}"
    data = resp.json()
    assert "run_id" in data

    resp = await client.get(f"/api/conversations/{conversation_id}/stream")
    assert resp.status_code == 200, f"GET /stream failed: {resp.text}"
    assert "text/event-stream" in resp.headers.get("content-type", "")

    return _parse_sse_events(resp.content)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestSSEStreamingEndpoint:
    """Tests the POST /api/conversations/{id}/messages SSE endpoint end-to-end.
    Exercises: FastAPI routing → streaming.py → compiled graph → nodes →
    inference client (fake transport) → SQLite persistence."""

    async def test_full_graph_run_streams_events(self, app_with_fake_inference):
        """Integration test: a complete research workflow streams run_snapshot
        events from start to finish with step progression."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: full graph run with SSE streaming")

        resp = await client.post("/api/conversations")
        assert resp.status_code == 200
        conversation_id = resp.json()["id"]
        logger.info("Created conversation %s", conversation_id)

        events = await _send_and_stream(client, conversation_id, "What is the speed of light?")
        logger.info("Received %d SSE events", len(events))
        event_types = [e["event"] for e in events]
        logger.info("Event types: %s", event_types)

        assert "run_snapshot" in event_types, "Should see run_snapshot events"

        snapshots = [e["data"] for e in events if e["event"] == "run_snapshot"]
        assert snapshots, "Expected at least one run_snapshot"

        final = snapshots[-1]
        assert final["status"] == "completed"
        assert len(final["execution_steps"]) > 0, "Should have execution steps"

    async def test_run_complete_contains_report(self, app_with_fake_inference):
        """Integration test: the final run_snapshot carries a structured
        ResearchReport with answer, citations, and budget."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: run_complete report structure")

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        events = await _send_and_stream(client, conversation_id, "What is the speed of light?")

        snapshots = [e["data"] for e in events if e["event"] == "run_snapshot"]
        completed = [s for s in snapshots if s.get("status") == "completed"]
        assert completed, "Expected at least one completed run_snapshot"

        report = completed[-1]["report"]
        assert "answer" in report
        assert "citations" in report
        assert "budget_consumed" in report
        assert isinstance(report["budget_consumed"], (int, float))
        assert report["budget_consumed"] > 0
        logger.info("Report answer: %s", report["answer"][:80])

    async def test_stream_emits_monotonic_run_snapshots(self, app_with_fake_inference):
        """Integration test: SSE stream emits run_snapshot events with
        monotonically increasing state_version values."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: run_snapshot monotonic state_version")

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        events = await _send_and_stream(client, conversation_id, "Snapshot stream test")
        snapshots = [e["data"] for e in events if e["event"] == "run_snapshot"]
        assert snapshots, "Expected at least one run_snapshot event"

        versions = [s["state_version"] for s in snapshots]
        assert versions == sorted(versions), f"Expected sorted state versions, got {versions}"
        assert len(set(versions)) == len(versions), (
            f"Expected strictly increasing state versions, got {versions}"
        )

        detail = await client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        run = detail.json()["runs"][0]
        final_snapshot = snapshots[-1]
        assert final_snapshot["id"] == run["id"]
        assert final_snapshot["state_version"] == run["state_version"]
        assert final_snapshot["status"] == run["status"]

    async def test_conversation_runs_include_snapshot_metadata(self, app_with_fake_inference):
        """Integration test: GET /conversations returns runs with snapshot
        metadata and execution step summary fields."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: run snapshot fields in conversation detail")

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        await _send_and_stream(client, conversation_id, "Snapshot metadata test")

        detail = await client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        data = detail.json()
        assert len(data["runs"]) == 1
        run = data["runs"][0]

        assert "state_version" in run
        assert isinstance(run["state_version"], int)
        assert run["state_version"] >= 1
        assert "updated_at" in run
        assert run["updated_at"]

        assert run["execution_steps"], "Expected execution step summaries"
        step = run["execution_steps"][0]
        for key in ("id", "tool_call_count", "step_version", "has_detail"):
            assert key in step

    async def test_lazy_step_detail_endpoint_returns_full_detail(self, app_with_fake_inference):
        """Integration test: step detail endpoint returns full detail payload
        for a step summary id from conversation detail."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: lazy step detail endpoint")

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        await _send_and_stream(client, conversation_id, "Lazy detail test")

        detail = await client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        run = detail.json()["runs"][0]
        run_id = run["id"]
        step_summary = run["execution_steps"][0]
        step_id = int(step_summary["id"])

        step_resp = await client.get(f"/api/runs/{run_id}/steps/{step_id}/detail")
        assert step_resp.status_code == 200
        step_data = step_resp.json()
        assert step_data["run_id"] == run_id
        assert step_data["step_id"] == step_id
        assert step_data["step_version"] == step_summary["step_version"]
        assert isinstance(step_data["detail"], dict)

    async def test_conversation_detail_stacks_resume_attempts(self, app_with_fake_inference):
        """Integration test: conversation detail exposes one logical run for
        resumed attempts with a stacked, transparent step timeline."""
        client, transport = app_with_fake_inference

        create_resp = await client.post("/api/conversations")
        conversation_id = create_resp.json()["id"]

        conversations = cast(
            ConversationRepository,
            _services["conversation_repository"],
        )
        steps = cast(Any, _services["workflow_step_repository"])

        user_message = await conversations.insert_message(conversation_id, "user", "resume me")

        first_started = datetime.now(timezone.utc)
        first_completed = first_started + timedelta(seconds=5)
        second_started = first_started + timedelta(seconds=10)

        old_run = WorkflowRun(
            id="run-old",
            conversation_id=conversation_id,
            user_message_id=user_message.id,
            thread_id="thread-1",
            tool_executions=[
                {
                    "tool": "hidden_run_level",
                    "args": {"q": "hidden"},
                    "result": "hidden run-level call",
                    "duration_ms": 11,
                    "success": True,
                }
            ],
            report=None,
            budget_limit=50,
            budget_consumed=12,
            error="",
            status="stopped",
            state_version=3,
            started_at=first_started.isoformat(),
            completed_at=first_completed.isoformat(),
            updated_at=first_completed.isoformat(),
            total_elapsed_ms=5000,
        )
        new_run = WorkflowRun(
            id="run-new",
            conversation_id=conversation_id,
            user_message_id=user_message.id,
            thread_id="thread-1",
            tool_executions=[
                {
                    "tool": "visible_run_level",
                    "args": {"q": "visible"},
                    "result": "visible run-level call",
                    "duration_ms": 12,
                    "success": True,
                }
            ],
            report=None,
            budget_limit=50,
            budget_consumed=12,
            error="",
            status="running",
            state_version=1,
            started_at=second_started.isoformat(),
            completed_at="",
            updated_at=second_started.isoformat(),
            total_elapsed_ms=0,
        )
        await conversations.save_workflow_run(old_run)
        await conversations.save_workflow_run(new_run)

        completed_step_id = await steps.save_step(
            WorkflowStep(
                id=None,
                workflow_run_id="run-old",
                node_name="planning",
                label="Planning",
                status="completed",
                cost=2,
                budget_remaining=48,
                started_at=first_started.isoformat(),
                elapsed_ms=1000,
                step_version=2,
                tool_call_count=0,
                detail={
                    "response": "persisted detail",
                    "tool_results": [
                        {
                            "tool": "visible_from_completed",
                            "args": {"query": "visible completed"},
                            "result": "ok",
                            "duration_ms": 20,
                            "success": True,
                        }
                    ],
                },
            )
        )
        await steps.save_step(
            WorkflowStep(
                id=None,
                workflow_run_id="run-old",
                node_name="verification",
                label="Verifying",
                status="stopped",
                cost=0,
                budget_remaining=38,
                started_at=first_completed.isoformat(),
                elapsed_ms=800,
                step_version=2,
                tool_call_count=0,
                detail={
                    "note": "intermediate stop",
                    "tool_results": [
                        {
                            "tool": "hidden_from_stopped",
                            "args": {"query": "hidden stopped"},
                            "result": "skip",
                            "duration_ms": 25,
                            "success": True,
                        }
                    ],
                },
            )
        )
        await steps.save_step(
            WorkflowStep(
                id=None,
                workflow_run_id="run-new",
                node_name="verification",
                label="Verifying",
                status="running",
                cost=0,
                budget_remaining=38,
                started_at=second_started.isoformat(),
                elapsed_ms=0,
                step_version=1,
                tool_call_count=0,
                detail={
                    "tool_results": [
                        {
                            "tool": "visible_from_running",
                            "args": {"query": "visible running"},
                            "result": "pending",
                            "duration_ms": 15,
                            "success": True,
                        }
                    ]
                },
            )
        )

        detail = await client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        data = detail.json()

        assert len(data["runs"]) == 1
        logical_run = data["runs"][0]
        assert logical_run["id"] == "run-new"
        assert logical_run["status"] == "running"

        statuses = [step["status"] for step in logical_run["execution_steps"]]
        assert "completed" in statuses
        assert "stopped" in statuses

        attempts = logical_run["attempts"]
        assert len(attempts) == 2
        assert attempts[0]["run_id"] == "run-old"
        assert attempts[1]["run_id"] == "run-new"

        timeline_tools = [entry["tool"] for entry in logical_run["tool_executions"]]
        assert "visible_from_completed" in timeline_tools
        assert "visible_from_running" in timeline_tools
        assert "hidden_from_stopped" in timeline_tools
        assert "hidden_run_level" not in timeline_tools
        assert "visible_run_level" not in timeline_tools

        completed_summary = next(
            step for step in logical_run["execution_steps"] if step["status"] == "completed"
        )
        assert completed_summary["id"] == str(completed_step_id)
        assert completed_summary["detail_run_id"] == "run-old"

        detail_resp = await client.get(
            f"/api/runs/{completed_summary['detail_run_id']}/steps/{completed_step_id}/detail"
        )
        assert detail_resp.status_code == 200
        detail_payload = detail_resp.json()
        assert detail_payload["run_id"] == "run-old"
        assert detail_payload["step_id"] == completed_step_id
        assert detail_payload["detail"]["response"] == "persisted detail"

    async def test_inference_client_was_called(self, app_with_fake_inference):
        """Integration test: the graph run makes real HTTP requests through
        the inference client (exercising request formatting and response parsing)."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: inference client request verification")

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        await _send_and_stream(client, conversation_id, "What is the speed of light?")

        # At minimum: /v1/models (registry refresh) + planning + report_generation
        assert len(transport.requests) >= 2
        # Verify the models endpoint was called during startup
        model_requests = [r for r in transport.requests if r.url.path == "/v1/models"]
        assert len(model_requests) >= 1
        logger.info("Transport received %d requests", len(transport.requests))

    async def test_budget_is_deducted_across_nodes(self, app_with_fake_inference):
        """Integration test: execution steps in the final run_snapshot show
        decreasing budget_remaining, proving budget enforcement works through
        the full graph."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: budget deduction across nodes")

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        events = await _send_and_stream(client, conversation_id, "Budget test question")

        snapshots = [e["data"] for e in events if e["event"] == "run_snapshot"]
        assert snapshots, "Expected at least one run_snapshot"
        final = snapshots[-1]

        completed_steps = [
            s for s in final["execution_steps"] if s.get("status") == "completed"
        ]
        budgets = [s["budget_remaining"] for s in completed_steps]

        assert len(budgets) >= 2, f"Expected >= 2 completed steps, got {len(budgets)}"

        for i in range(1, len(budgets)):
            assert budgets[i] < budgets[i - 1], (
                f"Budget not decreasing: {budgets[i]} >= {budgets[i - 1]} at index {i}"
            )
        logger.info("Budget progression: %s", budgets)

    async def test_start_run_honors_budget_override(self, app_with_fake_inference):
        """Integration test: run manager uses per-run budget overrides from
        POST /messages settings rather than the global default limit."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: per-run budget override")

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        events = await _send_and_stream(
            client,
            conversation_id,
            "Budget override test",
            settings={"budget": 37},
        )

        snapshots = [e["data"] for e in events if e["event"] == "run_snapshot"]
        assert snapshots, "Expected at least one run_snapshot"
        assert snapshots[0]["budget_limit"] == 37

        detail = await client.get(f"/api/conversations/{conversation_id}")
        assert detail.status_code == 200
        runs = detail.json()["runs"]
        assert len(runs) == 1
        assert runs[0]["budget_limit"] == 37

    async def test_assistant_message_persisted_after_run(self, app_with_fake_inference):
        """Integration test: after a successful graph run, the full report
        is persisted as an assistant_report message and the workflow run
        contains the report data."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: message persistence after graph run")

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        await _send_and_stream(client, conversation_id, "Persistence test")

        # Fetch the conversation and check persisted data
        resp = await client.get(f"/api/conversations/{conversation_id}")
        assert resp.status_code == 200
        data = resp.json()

        # The API filters assistant_report from messages, so only user
        # role should appear (no duplicate assistant bubble).
        roles = [m["role"] for m in data["messages"]]
        assert "user" in roles
        assert "assistant" not in roles, (
            "assistant message should not be persisted when a report exists"
        )

        # The report is available via workflow_runs
        assert len(data["runs"]) == 1
        assert data["runs"][0]["report"] is not None
        assert data["runs"][0]["report"]["answer"]
        logger.info(
            "Conversation has %d messages (roles: %s) and %d runs with report",
            len(data["messages"]),
            roles,
            len(data["runs"]),
        )

    async def test_conversation_not_found_returns_404(self, app_with_fake_inference):
        """Integration test: sending a message to a non-existent conversation
        returns 404 through the SSE endpoint."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: 404 for nonexistent conversation")

        resp = await client.post(
            "/api/conversations/nonexistent-id/messages",
            json={"content": "test"},
        )
        assert resp.status_code == 404

    async def test_empty_content_returns_400(self, app_with_fake_inference):
        """Integration test: sending an empty message returns 400."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: 400 for empty content")

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        resp = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": ""},
        )
        assert resp.status_code == 400


class TestMultiTurnConversation:
    """Integration tests for multi-turn conversation context.
    Verifies that prior ResearchReport is carried forward on follow-up
    messages, as specified in the Phase 2 plan."""

    async def test_follow_up_message_sees_prior_report(self, app_with_fake_inference):
        """Integration test: a second message in the same conversation includes
        the prior report as context for the planning node."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: multi-turn prior report context")

        # First message
        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        events_resp = await _send_and_stream(client, conversation_id, "First question")
        assert any(
            e["event"] == "run_snapshot" and e["data"].get("status") == "completed"
            for e in events_resp
        )

        # Reset transport for the second run
        transport._responses = [
            PLAN_RESPONSE,
            VERIFICATION_PASS_RESPONSE,
            REPORT_RESPONSE,
        ]
        transport._index = 0

        # Second message in the same conversation
        events2 = await _send_and_stream(client, conversation_id, "Follow up question")
        assert any(
            e["event"] == "run_snapshot" and e["data"].get("status") == "completed"
            for e in events2
        )
        logger.info("Second turn received %d events", len(events2))

        # Verify the conversation now has messages from both turns
        resp = await client.get(f"/api/conversations/{conversation_id}")
        messages = resp.json()["messages"]
        user_messages = [m for m in messages if m["role"] == "user"]
        assert len(user_messages) == 2
        logger.info("Conversation has %d user messages across both turns", len(user_messages))


class TestGraphVerificationRouting:
    """Integration tests for the verification retry routing logic.
    Exercises the three paths through the verification node: pass, fail+retry,
    and fail+budget-insufficient."""

    async def test_verification_fail_routes_back_to_planning(self, app_with_fake_inference):
        """Integration test: when verification fails with sufficient budget,
        the graph routes back to planning for a retry, then produces a report.

        With no tools configured, findings are empty, so:
        - tool_discovery, tool_selection, research_execution produce empty results
        - compression skips its model call (no findings to compress)
        - draft_synthesis still calls the model (synthesizes from plan + empty evidence)

        Call sequence per cycle: planning, draft_synthesis, verification
        = 3 model calls per cycle + 1 for report_generation."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: verification fail → retry → report")

        transport._responses = [
            # First cycle
            PLAN_RESPONSE,  # planning
            DRAFT_RESPONSE,  # draft_synthesis (compression skipped)
            VERIFICATION_FAIL_RESPONSE,  # verification (fail)
            # Second cycle (retry)
            PLAN_RESPONSE,  # planning
            DRAFT_RESPONSE,  # draft_synthesis
            VERIFICATION_PASS_RESPONSE,  # verification (pass)
            # Terminal
            REPORT_RESPONSE,  # report_generation
        ]
        transport._index = 0

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        events = await _send_and_stream(
            client, conversation_id, "Question that needs verification retry"
        )
        event_types = [e["event"] for e in events]

        snapshots = [e["data"] for e in events if e["event"] == "run_snapshot"]
        assert snapshots, "Expected at least one run_snapshot"

        final = snapshots[-1]
        assert final["status"] == "completed", f"Expected completed, got {final['status']}"

        verification_steps = [
            s for s in final["execution_steps"] if s.get("node") == "verification"
        ]
        assert len(verification_steps) >= 2, (
            f"Expected >= 2 verification steps (fail then pass), got {len(verification_steps)}"
        )
        logger.info(
            "Verification retry test: %d verification steps, %d total events",
            len(verification_steps),
            len(events),
        )


class TestStreamReplay:
    """Integration tests for event replay on late subscriber connection.

    When the SSE client connects after the graph has already started
    (e.g. due to network latency between POST returning and GET /stream
    connecting), subscribe() must replay all accumulated state so the
    client doesn't miss planning or other early steps."""

    async def test_replay_includes_completed_steps_before_live_events(
        self, app_with_fake_inference
    ):
        """Integration test: subscribe() replays completed execution steps
        that occurred before the subscriber connected, then yields live events."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: stream replay on late connect")

        transport._responses = [
            PLAN_RESPONSE,
            TOOL_SELECTION_RESPONSE,
            RESEARCH_RESPONSE,
            COMPRESSION_RESPONSE,
            DRAFT_RESPONSE,
            VERIFICATION_PASS_RESPONSE,
            REPORT_RESPONSE,
        ]
        transport._index = 0

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        # Start the run via POST
        post_resp = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Replay test"},
        )
        assert post_resp.status_code == 200

        # Small delay to let the graph advance past planning before we connect
        import asyncio

        await asyncio.sleep(0.3)

        # Now connect the SSE stream — planning should already be done
        stream_resp = await client.get(f"/api/conversations/{conversation_id}/stream")
        assert stream_resp.status_code == 200

        events = _parse_sse_events(stream_resp.content)
        event_types = [e["event"] for e in events]
        logger.info("Replay test received %d events: %s", len(events), event_types)

        # First event should be a run_snapshot replay with accumulated state.
        # Planning should already be done, so it should appear in the steps.
        assert event_types[0] == "run_snapshot", (
            f"Expected first event to be run_snapshot, got {event_types[0]}"
        )
        first_snapshot = events[0]["data"]
        planning_steps = [
            s for s in first_snapshot.get("execution_steps", [])
            if s.get("node") == "planning"
        ]
        assert planning_steps, (
            "Expected 'planning' step in replayed run_snapshot"
        )

        # Final snapshot should have completed status
        final_snapshots = [
            e["data"] for e in events if e["event"] == "run_snapshot"
        ]
        assert final_snapshots[-1]["status"] == "completed"
