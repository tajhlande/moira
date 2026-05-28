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
from pathlib import Path
from typing import Any

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
from moira.persistence.interfaces import DEFAULT_USER_ID
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
        SqliteModelPreferencesRepository,
    )
    from moira.persistence.sqlite.schema import run_migrations

    db_path = config.database.sqlite_path
    run_migrations(db_path)

    conversation_repo = SqliteConversationRepository(db_path)
    prefs_repo = SqliteModelPreferencesRepository(db_path)

    _services["conversation_repository"] = conversation_repo
    _services["model_preferences_repository"] = prefs_repo

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

    _services["config"] = config

    # Build the FastAPI app
    from moira.main import create_app

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client, transport

    # Teardown: MockTransport doesn't need explicit cleanup since it
    # has no real connections. Just clear the service registry.
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


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestSSEStreamingEndpoint:
    """Tests the POST /api/conversations/{id}/messages SSE endpoint end-to-end.
    Exercises: FastAPI routing → streaming.py → compiled graph → nodes →
    inference client (fake transport) → SQLite persistence."""

    async def test_full_graph_run_streams_events(self, app_with_fake_inference):
        """Integration test: a complete research workflow streams all SSE
        events from start to finish, ending with a run_complete event."""
        client, transport = app_with_fake_inference
        logger.info("INTegration test: full graph run with SSE streaming")

        # Create a conversation
        resp = await client.post("/api/conversations")
        assert resp.status_code == 200
        conversation_id = resp.json()["id"]
        logger.info("Created conversation %s", conversation_id)

        # Send a message via the SSE streaming endpoint
        resp = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "What is the speed of light?"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        events = _parse_sse_events(resp.content)
        logger.info("Received %d SSE events", len(events))
        event_types = [e["event"] for e in events]
        logger.info("Event types: %s", event_types)

        # Verify the expected event sequence
        assert "node_start" in event_types, "Should see node_start events"
        assert "node_end" in event_types, "Should see node_end events"
        assert "run_complete" in event_types, "Should end with run_complete"

    async def test_run_complete_contains_report(self, app_with_fake_inference):
        """Integration test: the run_complete event carries a structured
        ResearchReport with answer, citations, and budget."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: run_complete report structure")

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        resp = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "What is the speed of light?"},
        )
        events = _parse_sse_events(resp.content)

        complete_events = [e for e in events if e["event"] == "run_complete"]
        assert len(complete_events) == 1

        report = complete_events[0]["data"]["report"]
        assert "answer" in report
        assert "citations" in report
        assert "budget_consumed" in report
        assert isinstance(report["budget_consumed"], (int, float))
        assert report["budget_consumed"] > 0
        logger.info("Report answer: %s", report["answer"][:80])

    async def test_inference_client_was_called(self, app_with_fake_inference):
        """Integration test: the graph run makes real HTTP requests through
        the inference client (exercising request formatting and response parsing)."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: inference client request verification")

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "What is the speed of light?"},
        )

        # At minimum: /v1/models (registry refresh) + planning + report_generation
        assert len(transport.requests) >= 2
        # Verify the models endpoint was called during startup
        model_requests = [r for r in transport.requests if r.url.path == "/v1/models"]
        assert len(model_requests) >= 1
        logger.info("Transport received %d requests", len(transport.requests))

    async def test_budget_is_deducted_across_nodes(self, app_with_fake_inference):
        """Integration test: node_end events show decreasing budget_remaining,
        proving the budget enforcement module works through the full graph."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: budget deduction across nodes")

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        resp = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Budget test question"},
        )
        events = _parse_sse_events(resp.content)

        budget_events = [e for e in events if e["event"] == "node_end"]
        budgets = [e["data"]["budget_remaining"] for e in budget_events]

        # Budget should strictly decrease
        for i in range(1, len(budgets)):
            assert budgets[i] < budgets[i - 1], (
                f"Budget not decreasing: {budgets[i]} >= {budgets[i - 1]} at index {i}"
            )
        logger.info("Budget progression: %s", budgets)

    async def test_assistant_message_persisted_after_run(self, app_with_fake_inference):
        """Integration test: after a successful graph run, the full report
        is persisted as an assistant_report message and the workflow run
        contains the report data."""
        client, transport = app_with_fake_inference
        logger.info("Integration test: message persistence after graph run")

        resp = await client.post("/api/conversations")
        conversation_id = resp.json()["id"]

        await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Persistence test"},
        )

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

        resp = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "First question"},
        )
        events = _parse_sse_events(resp.content)
        assert any(e["event"] == "run_complete" for e in events)

        # Reset transport for the second run
        transport._responses = [
            PLAN_RESPONSE,
            VERIFICATION_PASS_RESPONSE,
            REPORT_RESPONSE,
        ]
        transport._index = 0

        # Second message in the same conversation
        resp = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Follow up question"},
        )
        events2 = _parse_sse_events(resp.content)
        assert any(e["event"] == "run_complete" for e in events2)
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

        resp = await client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Question that needs verification retry"},
        )
        events = _parse_sse_events(resp.content)
        event_types = [e["event"] for e in events]

        # Should see verification_report events (at least 2: fail then pass)
        verification_reports = [e for e in events if e["event"] == "verification_report"]
        assert len(verification_reports) >= 2, (
            f"Expected >= 2 verification reports (fail then pass), got {len(verification_reports)}"
        )
        logger.info(
            "Verification retry test: %d reports, %d total events",
            len(verification_reports),
            len(events),
        )

        # Should still end with a run_complete
        assert "run_complete" in event_types
