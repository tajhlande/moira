import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moira.config import MoiraConfig
from moira.inference.client import ChatResponse, InferenceClient
from moira.inference.registry import ModelRegistry, ResolvedModel
from moira.models.state import Finding, ResearchState, VerificationReport
from moira.service_setup import _services
from moira.tools.base import ToolDefinition, ToolResult


@pytest.fixture
def config():
    return MoiraConfig()


@pytest.fixture
def mock_writer():
    """Collects all streaming events for assertion."""
    events = []

    def write(event):
        events.append(event)

    with patch("moira.workflow.nodes.research_nodes.get_stream_writer", return_value=write):
        yield events


@pytest.fixture
def mock_model():
    """Returns a mock client that returns configurable responses."""
    client = AsyncMock(spec=InferenceClient)
    client.chat_completion = AsyncMock(return_value=ChatResponse(content="test response"))

    resolved = ResolvedModel(model_id="test-model", client=client)

    registry = MagicMock(spec=ModelRegistry)
    registry.resolve = AsyncMock(return_value=resolved)

    return {"client": client, "resolved": resolved, "registry": registry}


@pytest.fixture
def base_state(config) -> ResearchState:
    cw = config.budget.cost_weights
    return {
        "question": "What is the speed of light?",
        "plan": "",
        "active_tools": [],
        "findings": [],
        "compressed_findings": [],
        "draft": "",
        "verification": "",
        "report": None,
        "budget_remaining": float(config.budget.default_limit),
        "budget_limit": float(config.budget.default_limit),
        "cost_weights": {
            "planning": cw.planning,
            "tool_discovery": cw.tool_discovery,
            "tool_selection": cw.tool_selection,
            "research_execution": cw.research_execution,
            "compression": cw.compression,
            "draft_synthesis": cw.draft_synthesis,
            "verification": cw.verification,
            "report_generation": cw.report_generation,
        },
        "verification_history": [],
        "unverified_claims": [],
        "error": "",
        "draft_retry_count": 0,
    }


def _inject_services(config, mock_model):
    _services.clear()
    _services["config"] = config
    _services["model_registry"] = mock_model["registry"]


def _make_run_config(config):
    return {"configurable": {"moira_config": config}}


class TestPlanning:
    async def test_produces_plan_and_deducts_budget(
        self, config, mock_writer, mock_model, base_state
    ):
        mock_model[
            "client"
        ].chat_completion.return_value = ChatResponse(
            content="1. Look up the speed of light\n2. Verify with multiple sources"
        )
        _inject_services(config, mock_model)

        from moira.workflow.nodes.research_nodes import planning

        result = await planning(base_state, _make_run_config(config))

        assert result["plan"] == "1. Look up the speed of light\n2. Verify with multiple sources"
        assert result["budget_remaining"] == 48.0

    async def test_emits_node_start_and_end_events(
        self, config, mock_writer, mock_model, base_state
    ):
        _inject_services(config, mock_model)

        from moira.workflow.nodes.research_nodes import planning

        await planning(base_state, _make_run_config(config))

        event_types = [e["event"] for e in mock_writer]
        assert "node_start" in event_types
        assert "node_end" in event_types

    async def test_insufficient_budget_returns_error(
        self, config, mock_writer, mock_model, base_state
    ):
        _inject_services(config, mock_model)
        base_state["budget_remaining"] = 1.0

        from moira.workflow.nodes.research_nodes import planning

        result = await planning(base_state, _make_run_config(config))

        assert "error" in result
        assert "Insufficient budget" in result["error"]

    async def test_includes_prior_report_context(
        self, config, mock_writer, mock_model, base_state
    ):
        _inject_services(config, mock_model)
        run_config = _make_run_config(config)
        run_config["configurable"]["prior_report"] = {"answer": "Prior answer here"}

        from moira.workflow.nodes.research_nodes import planning

        await planning(base_state, run_config)

        call_args = mock_model["client"].chat_completion.call_args
        messages = call_args.kwargs["messages"]
        system_msgs = [m for m in messages if m["role"] == "system"]
        assert any("Prior answer here" in m["content"] for m in system_msgs)

    async def test_includes_verification_history_on_retry(
        self, config, mock_writer, mock_model, base_state
    ):
        _inject_services(config, mock_model)
        base_state["verification_history"] = [
            VerificationReport(
                outcome="retry_plan",
                case=5,
                assessment="Unsupported claims essential to answer",
                supported_claims=[],
                unsupported_claims=["claim X is wrong"],
                contradictions=[],
                relevance="on_topic",
                depth="sufficient",
                guidance="find better sources",
            )
        ]

        from moira.workflow.nodes.research_nodes import planning

        await planning(base_state, _make_run_config(config))

        call_args = mock_model["client"].chat_completion.call_args
        messages = call_args.kwargs["messages"]
        system_content = " ".join(m["content"] for m in messages if m["role"] == "system")
        assert "Unsupported claims essential to answer" in system_content
        assert "find better sources" in system_content


class TestToolDiscovery:
    async def test_calls_discovery_and_returns_tools(
        self, config, mock_writer, mock_model, base_state
    ):
        _inject_services(config, mock_model)
        tools = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]
        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=tools)
        _services["tool_discovery"] = mock_discovery
        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = []
        _services["tool_catalog"] = mock_catalog

        base_state["plan"] = "Search for speed of light"

        from moira.workflow.nodes.research_nodes import tool_discovery

        result = await tool_discovery(base_state, _make_run_config(config))

        assert len(result["active_tools"]) == 1
        assert result["active_tools"][0].name == "web_search"
        assert result["budget_remaining"] == 49.0

    async def test_no_model_call_needed(self, config, mock_writer, mock_model, base_state):
        _inject_services(config, mock_model)
        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=[])
        _services["tool_discovery"] = mock_discovery
        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = []
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.research_nodes import tool_discovery

        await tool_discovery(base_state, _make_run_config(config))

        mock_model["client"].chat_completion.assert_not_called()

    async def test_default_tools_always_included(
        self, config, mock_writer, mock_model, base_state
    ):
        _inject_services(config, mock_model)
        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=[])
        _services["tool_discovery"] = mock_discovery
        default_tool = ToolDefinition(
            name="calculator", description="Math", is_default=True, enabled=True
        )
        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = [default_tool]
        _services["tool_catalog"] = mock_catalog

        base_state["plan"] = "Calculate something"

        from moira.workflow.nodes.research_nodes import tool_discovery

        result = await tool_discovery(base_state, _make_run_config(config))

        assert len(result["active_tools"]) == 1
        assert result["active_tools"][0].name == "calculator"


class TestToolSelection:
    async def test_selects_tools_from_model_response(
        self, config, mock_writer, mock_model, base_state
    ):
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content='["web_search"]'
        )
        _inject_services(config, mock_model)
        base_state["active_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
            ToolDefinition(name="paper_search", description="Search papers"),
        ]

        from moira.workflow.nodes.research_nodes import tool_selection

        result = await tool_selection(base_state, _make_run_config(config))

        assert len(result["active_tools"]) == 1
        assert result["active_tools"][0].name == "web_search"
        assert result["budget_remaining"] == 48.0

    async def test_fallback_to_all_tools_if_model_returns_empty(
        self, config, mock_writer, mock_model, base_state
    ):
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content="I cannot parse this"
        )
        _inject_services(config, mock_model)
        base_state["active_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]

        from moira.workflow.nodes.research_nodes import tool_selection

        result = await tool_selection(base_state, _make_run_config(config))

        assert len(result["active_tools"]) == 1

    async def test_no_tools_discovered_returns_empty(
        self, config, mock_writer, mock_model, base_state
    ):
        _inject_services(config, mock_model)
        base_state["active_tools"] = []

        from moira.workflow.nodes.research_nodes import tool_selection

        result = await tool_selection(base_state, _make_run_config(config))
        assert "active_tools" not in result


class TestResearchExecution:
    async def test_executes_tools_and_produces_findings(
        self, config, mock_writer, mock_model, base_state
    ):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    [{"tool": "web_search", "args": {"query": "speed of light"}}]
                )
            ),
            ChatResponse(content="Research complete."),
        ]

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="The speed of light is 299,792,458 m/s",
                    success=True,
                    duration_ms=150,
                )
            ]
        )
        _services["tool_executor"] = mock_executor

        base_state["active_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        base_state["plan"] = "Search for speed of light"

        from moira.workflow.nodes.research_nodes import research_execution

        result = await research_execution(base_state, _make_run_config(config))

        assert len(result["findings"]) == 1
        assert result["findings"][0]["source"] == "web_search"
        assert "299,792,458" in result["findings"][0]["content"]
        assert result["budget_remaining"] == 45.0

    async def test_emits_tool_result_events(self, config, mock_writer, mock_model, base_state):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps([{"tool": "web_search", "args": {"query": "test"}}])
            ),
            ChatResponse(content="Done."),
        ]
        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(tool_name="web_search", output="result", success=True, duration_ms=100)
            ]
        )
        _services["tool_executor"] = mock_executor
        base_state["active_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        base_state["plan"] = "test plan"

        from moira.workflow.nodes.research_nodes import research_execution

        await research_execution(base_state, _make_run_config(config))

        tool_events = [e for e in mock_writer if e["event"] == "tool_result"]
        assert len(tool_events) == 1
        assert tool_events[0]["payload"]["tool"] == "web_search"

    async def test_no_tools_returns_empty_findings(
        self, config, mock_writer, mock_model, base_state
    ):
        _inject_services(config, mock_model)
        base_state["active_tools"] = []

        from moira.workflow.nodes.research_nodes import research_execution

        result = await research_execution(base_state, _make_run_config(config))
        assert result["findings"] == []

    async def test_multi_round_tool_loop(self, config, mock_writer, mock_model, base_state):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    [{"tool": "web_search", "args": {"query": "speed of light"}}]
                )
            ),
            ChatResponse(
                content=json.dumps(
                    [
                        {
                            "tool": "url_content",
                            "args": {"url": "https://example.com/physics"},
                        }
                    ]
                )
            ),
            ChatResponse(
                content="Research complete. The speed of light is 299,792,458 m/s."
            ),
        ]

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            side_effect=[
                [
                    ToolResult(
                        tool_name="web_search",
                        output="Results about speed of light",
                        success=True,
                        duration_ms=100,
                    )
                ],
                [
                    ToolResult(
                        tool_name="url_content",
                        output="The speed of light in vacuum is exactly 299,792,458 m/s",
                        success=True,
                        duration_ms=200,
                    )
                ],
            ]
        )
        _services["tool_executor"] = mock_executor

        base_state["active_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
            ToolDefinition(name="url_content", description="Fetch URL"),
        ]
        base_state["plan"] = "Search for speed of light"

        from moira.workflow.nodes.research_nodes import research_execution

        result = await research_execution(base_state, _make_run_config(config))

        assert len(result["findings"]) == 2
        assert result["findings"][0]["source"] == "web_search"
        assert result["findings"][1]["source"] == "url_content"
        tool_events = [e for e in mock_writer if e["event"] == "tool_result"]
        assert len(tool_events) == 2


class TestCompression:
    async def test_compresses_findings_to_summary(
        self, config, mock_writer, mock_model, base_state
    ):
        mock_model[
            "client"
        ].chat_completion.return_value = ChatResponse(
            content="Key findings: speed of light is ~3e8 m/s"
        )
        _inject_services(config, mock_model)
        base_state["findings"] = [
            Finding(
                content="The speed of light in vacuum is 299,792,458 meters per second",
                source="web_search",
                type="evidence",
            )
        ]

        from moira.workflow.nodes.research_nodes import compression

        result = await compression(base_state, _make_run_config(config))

        assert len(result["compressed_findings"]) == 1
        assert "3e8" in result["compressed_findings"][0]["content"]
        assert result["budget_remaining"] == 49.0

    async def test_uses_task_model(self, config, mock_writer, mock_model, base_state):
        _inject_services(config, mock_model)
        base_state["findings"] = [Finding(content="some finding", source="test", type="evidence")]

        from moira.workflow.nodes.research_nodes import compression

        await compression(base_state, _make_run_config(config))

        mock_model["registry"].resolve.assert_called_with("task")

    async def test_empty_findings_returns_empty(self, config, mock_writer, mock_model, base_state):
        _inject_services(config, mock_model)

        from moira.workflow.nodes.research_nodes import compression

        result = await compression(base_state, _make_run_config(config))
        assert result["compressed_findings"] == []


class TestDraftSynthesis:
    async def test_produces_draft_from_evidence(self, config, mock_writer, mock_model, base_state):
        mock_model[
            "client"
        ].chat_completion.return_value = ChatResponse(
            content="The speed of light is approximately 3 x 10^8 m/s [web_search]."
        )
        _inject_services(config, mock_model)
        base_state["plan"] = "Search for speed of light"
        base_state["findings"] = [
            Finding(
                content="Speed of light: 299,792,458 m/s",
                source="web_search",
                type="evidence",
            )
        ]

        from moira.workflow.nodes.research_nodes import draft_synthesis

        result = await draft_synthesis(base_state, _make_run_config(config))

        assert "3" in result["draft"]
        assert result["budget_remaining"] == 47.0


class TestVerification:
    async def test_accept_verification(self, config, mock_writer, mock_model, base_state):
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "outcome": "accept",
                    "case": 1,
                    "assessment": "Draft is correct",
                    "supported_claims": ["speed of light is ~3e8 m/s"],
                    "unsupported_claims": [],
                    "contradictions": [],
                    "relevance": "on_topic",
                    "depth": "sufficient",
                    "guidance": "Accept as is",
                }
            )
        )
        _inject_services(config, mock_model)
        base_state["draft"] = "The speed of light is ~3e8 m/s"

        from moira.workflow.nodes.research_nodes import verification

        result = await verification(base_state, _make_run_config(config))

        assert len(result["verification_history"]) == 1
        assert result["verification_history"][0]["outcome"] == "accept"
        assert result["verification_history"][0]["unsupported_claims"] == []
        assert result["unverified_claims"] == []
        assert result["budget_remaining"] == 46.0

    async def test_retry_verification_populates_unsupported_claims(
        self, config, mock_writer, mock_model, base_state
    ):
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "outcome": "retry_plan",
                    "case": 5,
                    "assessment": "Draft has unsupported claims that are essential",
                    "supported_claims": [],
                    "unsupported_claims": ["exact speed is wrong", "no primary source"],
                    "contradictions": [],
                    "relevance": "on_topic",
                    "depth": "sufficient",
                    "guidance": "Find a different source for the speed value",
                }
            )
        )
        _inject_services(config, mock_model)
        base_state["draft"] = "The speed of light is exactly 3e8 m/s"

        from moira.workflow.nodes.research_nodes import verification

        result = await verification(base_state, _make_run_config(config))

        assert "exact speed is wrong" in result["unverified_claims"]
        assert "no primary source" in result["unverified_claims"]

    async def test_accumulates_verification_history(
        self, config, mock_writer, mock_model, base_state
    ):
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "outcome": "retry_plan",
                    "case": 5,
                    "assessment": "Unsupported claims",
                    "supported_claims": [],
                    "unsupported_claims": ["claim A"],
                    "contradictions": [],
                    "relevance": "on_topic",
                    "depth": "sufficient",
                    "guidance": "Try different approach",
                }
            )
        )
        _inject_services(config, mock_model)
        base_state["verification_history"] = [
            VerificationReport(
                outcome="retry_plan",
                case=5,
                assessment="Old failure",
                supported_claims=[],
                unsupported_claims=["old claim"],
                contradictions=[],
                relevance="on_topic",
                depth="sufficient",
                guidance="Try again",
            )
        ]

        from moira.workflow.nodes.research_nodes import verification

        result = await verification(base_state, _make_run_config(config))

        assert len(result["verification_history"]) == 2
        assert result["verification_history"][0]["unsupported_claims"] == ["old claim"]
        assert result["verification_history"][1]["unsupported_claims"] == ["claim A"]

    async def test_emits_verification_report_event(
        self, config, mock_writer, mock_model, base_state
    ):
        _inject_services(config, mock_model)
        base_state["draft"] = "Some draft"

        from moira.workflow.nodes.research_nodes import verification

        await verification(base_state, _make_run_config(config))

        report_events = [e for e in mock_writer if e["event"] == "verification_report"]
        assert len(report_events) == 1
        assert report_events[0]["payload"]["attempt"] == 1

    async def test_non_json_response_treated_as_error(
        self, config, mock_writer, mock_model, base_state
    ):
        # Model returns reasoning text instead of JSON — should be treated as
        # a technical error (case 11), not a silent pass.
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content="I need to analyze the claims...\n1. First claim...\n2. Second..."
        )
        _inject_services(config, mock_model)
        base_state["draft"] = "The speed of light is exactly 3e8 m/s"

        from moira.workflow.nodes.research_nodes import verification

        result = await verification(base_state, _make_run_config(config))

        assert len(result["verification_history"]) == 1
        assert result["verification_history"][0]["outcome"] == "error"
        assert result["verification_history"][0]["case"] == 11
        assert result["error"] != ""

    async def test_tool_assisted_fact_checking(self, config, mock_writer, mock_model, base_state):
        _inject_services(config, mock_model)
        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="Confirmed: speed of light is 299,792,458 m/s",
                    success=True,
                    duration_ms=100,
                )
            ]
        )
        _services["tool_executor"] = mock_executor

        # Phase 1 (fact-check): returns tool call, then no more calls.
        # Phase 2 (verdict): returns structured JSON.
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    [
                        {
                            "tool": "web_search",
                            "args": {"query": "speed of light exact value"},
                        }
                    ]
                )
            ),
            ChatResponse(content="Fact-checking complete."),
            ChatResponse(
                content=json.dumps(
                    {
                        "outcome": "accept",
                        "case": 1,
                        "assessment": "All claims verified by independent search",
                        "supported_claims": ["speed of light is ~3e8 m/s"],
                        "unsupported_claims": [],
                        "contradictions": [],
                        "relevance": "on_topic",
                        "depth": "sufficient",
                        "guidance": "Accept",
                    }
                )
            ),
        ]

        base_state["active_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        base_state["draft"] = "The speed of light is approximately 3e8 m/s"

        from moira.workflow.nodes.research_nodes import verification

        result = await verification(base_state, _make_run_config(config))

        assert result["verification_history"][0]["outcome"] == "accept"
        assert result["verification_history"][0]["supported_claims"] == [
            "speed of light is ~3e8 m/s"
        ]
        tool_events = [e for e in mock_writer if e["event"] == "tool_result"]
        assert len(tool_events) == 1
        assert tool_events[0]["payload"]["tool"] == "web_search"

    async def test_tool_fact_checking_contradiction_leads_to_retry(
        self, config, mock_writer, mock_model, base_state
    ):
        _inject_services(config, mock_model)
        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="Speed of light is 299,792,458 m/s, NOT 3e9",
                    success=True,
                    duration_ms=100,
                )
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    [{"tool": "web_search", "args": {"query": "speed of light"}}]
                )
            ),
            ChatResponse(content="Found contradiction."),
            ChatResponse(
                content=json.dumps(
                    {
                        "outcome": "retry_plan",
                        "case": 6,
                        "assessment": "Draft claims wrong value for speed of light",
                        "supported_claims": [],
                        "unsupported_claims": ["exact speed is wrong"],
                        "contradictions": ["Draft says 3e9 but actual is 3e8"],
                        "relevance": "on_topic",
                        "depth": "sufficient",
                        "guidance": "Fix the speed of light value",
                    }
                )
            ),
        ]

        base_state["active_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        base_state["draft"] = "The speed of light is approximately 3e9 m/s"

        from moira.workflow.nodes.research_nodes import verification

        result = await verification(base_state, _make_run_config(config))

        assert result["verification_history"][0]["outcome"] == "retry_plan"
        assert result["verification_history"][0]["case"] == 6
        assert "exact speed is wrong" in result["unverified_claims"]

    async def test_no_tools_skips_fact_checking(self, config, mock_writer, mock_model, base_state):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "outcome": "accept",
                    "case": 1,
                    "assessment": "Draft looks correct",
                    "supported_claims": ["speed of light"],
                    "unsupported_claims": [],
                    "contradictions": [],
                    "relevance": "on_topic",
                    "depth": "sufficient",
                    "guidance": "Accept",
                }
            )
        )
        base_state["active_tools"] = []
        base_state["draft"] = "The speed of light is ~3e8 m/s"

        from moira.workflow.nodes.research_nodes import verification

        result = await verification(base_state, _make_run_config(config))

        assert result["verification_history"][0]["outcome"] == "accept"
        tool_events = [e for e in mock_writer if e["event"] == "tool_result"]
        assert len(tool_events) == 0


class TestReportGeneration:
    async def test_verified_path(self, config, mock_writer, mock_model, base_state):
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "answer": "The speed of light is 299,792,458 m/s.",
                    "citations": [{"source": "web_search"}],
                    "support": [],
                    "critiques": ["Limited to vacuum measurement"],
                    "unverified_claims": [],
                }
            )
        )
        _inject_services(config, mock_model)
        base_state["draft"] = "Draft about speed of light"
        base_state["budget_remaining"] = 10.0
        base_state["budget_limit"] = 50.0

        from moira.workflow.nodes.research_nodes import report_generation

        result = await report_generation(base_state, _make_run_config(config))

        report = result["report"]
        assert report["answer"] == "The speed of light is 299,792,458 m/s."
        assert report["critiques"] == ["Limited to vacuum measurement"]
        assert report["unverified_claims"] == []
        assert report["budget_consumed"] == 43.0

    async def test_budget_exhausted_path(self, config, mock_writer, mock_model, base_state):
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "answer": "Partial answer.",
                    "citations": [],
                    "support": [],
                    "critiques": ["Incomplete verification"],
                    "unverified_claims": ["claim X not verified"],
                }
            )
        )
        _inject_services(config, mock_model)
        base_state["draft"] = "Some draft"
        base_state["unverified_claims"] = ["claim X not verified"]
        base_state["budget_remaining"] = 5.0
        base_state["budget_limit"] = 50.0

        from moira.workflow.nodes.research_nodes import report_generation

        result = await report_generation(base_state, _make_run_config(config))

        report = result["report"]
        assert "claim X not verified" in report["unverified_claims"]

    async def test_error_path(self, config, mock_writer, mock_model, base_state):
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "answer": "Partial answer from interrupted workflow.",
                    "citations": [],
                    "support": [],
                    "critiques": [],
                    "unverified_claims": [],
                }
            )
        )
        _inject_services(config, mock_model)
        base_state["error"] = "Model timeout during research"
        base_state["budget_remaining"] = 30.0
        base_state["budget_limit"] = 50.0

        from moira.workflow.nodes.research_nodes import report_generation

        result = await report_generation(base_state, _make_run_config(config))

        report = result["report"]
        assert any("Model timeout" in c for c in report["critiques"])

    async def test_always_runs_regardless_of_budget(
        self, config, mock_writer, mock_model, base_state
    ):
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "answer": "Emergency answer.",
                    "citations": [],
                    "support": [],
                    "critiques": [],
                    "unverified_claims": [],
                }
            )
        )
        _inject_services(config, mock_model)
        base_state["budget_remaining"] = 0.0
        base_state["budget_limit"] = 50.0
        base_state["draft"] = "Draft text"

        from moira.workflow.nodes.research_nodes import report_generation

        result = await report_generation(base_state, _make_run_config(config))
        assert result["report"] is not None
        assert result["report"]["answer"] == "Emergency answer."

    async def test_robust_to_missing_state_fields(self, config, mock_writer, mock_model):
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "answer": "Best effort answer.",
                    "citations": [],
                    "support": [],
                    "critiques": [],
                    "unverified_claims": [],
                }
            )
        )
        _inject_services(config, mock_model)
        # Minimal state as if failure happened at planning
        minimal_state: ResearchState = {
            "question": "What is X?",
            "plan": "",
            "active_tools": [],
            "findings": [],
            "compressed_findings": [],
            "draft": "",
            "verification": "",
            "report": None,
            "budget_remaining": 48.0,
            "budget_limit": 50.0,
            "verification_history": [],
            "unverified_claims": [],
            "error": "Failed at planning",
        }

        from moira.workflow.nodes.research_nodes import report_generation

        result = await report_generation(minimal_state, _make_run_config(config))
        assert result["report"]["answer"] == "Best effort answer."
        assert any("Failed at planning" in c for c in result["report"]["critiques"])

    async def test_handles_model_failure_gracefully(
        self, config, mock_writer, mock_model, base_state
    ):
        mock_model["client"].chat_completion.side_effect = Exception("Model unavailable")
        _inject_services(config, mock_model)
        base_state["draft"] = "Fallback draft text"
        base_state["budget_remaining"] = 10.0
        base_state["budget_limit"] = 50.0

        from moira.workflow.nodes.research_nodes import report_generation

        result = await report_generation(base_state, _make_run_config(config))

        assert result["report"] is not None
        assert result["report"]["answer"] == "Fallback draft text"

    async def test_emits_run_complete_event(self, config, mock_writer, mock_model, base_state):
        _inject_services(config, mock_model)
        base_state["budget_remaining"] = 10.0
        base_state["budget_limit"] = 50.0

        from moira.workflow.nodes.research_nodes import report_generation

        await report_generation(base_state, _make_run_config(config))

        complete_events = [e for e in mock_writer if e["event"] == "run_complete"]
        assert len(complete_events) == 1
        assert "report" in complete_events[0]["payload"]


class TestJsonParsing:
    def test_parse_json_list_extracts_array(self):
        from moira.workflow.nodes.research_nodes import _parse_json_list

        text = 'Here are the tools: ["web_search", "paper_search"]'
        result = _parse_json_list(text)
        assert result == ["web_search", "paper_search"]

    def test_parse_json_list_handles_plain_response(self):
        from moira.workflow.nodes.research_nodes import _parse_json_list

        assert _parse_json_list("I don't know") == []

    def test_parse_json_object_extracts_dict(self):
        from moira.workflow.nodes.research_nodes import _parse_json_object

        text = 'Result: {"answer": "yes", "citations": []}'
        result = _parse_json_object(text)
        assert result["answer"] == "yes"

    def test_parse_json_object_handles_invalid_json(self):
        from moira.workflow.nodes.research_nodes import _parse_json_object

        assert _parse_json_object("no json here") == {}

    def test_parse_tool_calls_extracts_calls(self):
        from moira.workflow.nodes.research_nodes import _parse_tool_calls

        text = json.dumps(
            [
                {"tool": "web_search", "args": {"query": "test"}},
                {"tool": "paper_search", "args": {"query": "other"}},
            ]
        )
        result = _parse_tool_calls(text)
        assert len(result) == 2
        assert result[0] == ("web_search", {"query": "test"})

    def test_parse_tool_calls_handles_empty(self):
        from moira.workflow.nodes.research_nodes import _parse_tool_calls

        assert _parse_tool_calls("no tools") == []

    def test_parse_tool_calls_skips_entries_without_name(self):
        from moira.workflow.nodes.research_nodes import _parse_tool_calls

        text = json.dumps([{"args": {"query": "test"}}])
        assert _parse_tool_calls(text) == []

    def test_parse_tool_calls_handles_line_delimited_json(self):
        from moira.workflow.nodes.research_nodes import _parse_tool_calls

        text = (
            '{"tool": "web_search", "args": {"query": "speed of light"}}\n'
            '{"tool": "web_search", "args": {"query": "c in physics"}}\n'
            '{"tool": "calculator", "args": {"expression": "3e8 * 2"}}'
        )
        result = _parse_tool_calls(text)
        assert len(result) == 3
        assert result[0] == ("web_search", {"query": "speed of light"})
        assert result[2] == ("calculator", {"expression": "3e8 * 2"})

    def test_parse_tool_calls_line_delimited_ignores_prose(self):
        from moira.workflow.nodes.research_nodes import _parse_tool_calls

        text = (
            "I will search for information.\n"
            '{"tool": "web_search", "args": {"query": "test"}}\n'
            "That should help."
        )
        result = _parse_tool_calls(text)
        assert len(result) == 1
        assert result[0] == ("web_search", {"query": "test"})

    def test_parse_tool_calls_handles_markdown_fenced_multi_array(self):
        from moira.workflow.nodes.research_nodes import _parse_tool_calls

        text = (
            "```json\n"
            '[{"tool": "web_search", "args": {"query": "topic A"}}]\n'
            '[{"tool": "web_search", "args": {"query": "topic B"}}]\n'
            "```"
        )
        result = _parse_tool_calls(text)
        assert len(result) == 2
        assert result[0] == ("web_search", {"query": "topic A"})
        assert result[1] == ("web_search", {"query": "topic B"})

    def test_parse_tool_calls_handles_markdown_fenced_single_array(self):
        from moira.workflow.nodes.research_nodes import _parse_tool_calls

        text = (
            "```json\n"
            '[{"tool": "web_search", "args": {"query": "a"}}, '
            '{"tool": "calculator", "args": {"expression": "2+2"}}]\n'
            "```"
        )
        result = _parse_tool_calls(text)
        assert len(result) == 2
        assert result[0] == ("web_search", {"query": "a"})
        assert result[1] == ("calculator", {"expression": "2+2"})


class TestLooksLikeFailedToolCalls:
    def test_detects_markdown_fence(self):
        from moira.workflow.nodes.research_nodes import _looks_like_failed_tool_calls

        text = '```json\n[{"tool": "web_search", "args": {"query": "test"}}]\n```'
        assert _looks_like_failed_tool_calls(text) is True

    def test_detects_malformed_array(self):
        from moira.workflow.nodes.research_nodes import _looks_like_failed_tool_calls

        text = '[{"tool": "web_search", "args" {"query": "missing colon"}}]'
        assert _looks_like_failed_tool_calls(text) is True

    def test_ignores_prose(self):
        from moira.workflow.nodes.research_nodes import _looks_like_failed_tool_calls

        assert _looks_like_failed_tool_calls("Research complete.") is False

    def test_ignores_valid_tool_calls(self):
        from moira.workflow.nodes.research_nodes import _looks_like_failed_tool_calls

        text = json.dumps([{"tool": "web_search", "args": {"query": "test"}}])
        assert _looks_like_failed_tool_calls(text) is False

    def test_ignores_empty_array(self):
        from moira.workflow.nodes.research_nodes import _looks_like_failed_tool_calls

        assert _looks_like_failed_tool_calls("[]") is False


class TestBuildPlanningMessages:
    def test_basic_question(self):
        from moira.workflow.nodes.research_nodes import _build_planning_messages

        messages = _build_planning_messages("What is X?", [], None)
        assert len(messages) == 2
        assert messages[-1]["content"] == "What is X?"

    def test_includes_prior_report(self):
        from moira.workflow.nodes.research_nodes import _build_planning_messages

        messages = _build_planning_messages("Follow up", [], {"answer": "Prior answer text"})
        assert any("Prior answer text" in m["content"] for m in messages)

    def test_includes_verification_failures(self):
        from moira.workflow.nodes.research_nodes import _build_planning_messages

        history = [
            VerificationReport(
                outcome="retry_plan",
                case=5,
                assessment="Essential claims are unsupported",
                supported_claims=[],
                unsupported_claims=["claim A is wrong"],
                contradictions=["X contradicts Y"],
                relevance="on_topic",
                depth="insufficient",
                guidance="find a better source for A",
            )
        ]
        messages = _build_planning_messages("Retry question", history, None)
        system_text = " ".join(m["content"] for m in messages if m["role"] == "system")
        assert "Essential claims are unsupported" in system_text
        assert "find a better source for A" in system_text
