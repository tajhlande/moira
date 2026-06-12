"""Comprehensive unit tests for all 7 research loop nodes.

Tests cover happy path, budget exhaustion, model failure, and edge cases
for each node in the overhauled research loop.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moira.config import MoiraConfig
from moira.inference.client import ChatResponse, InferenceClient
from moira.inference.registry import ModelRegistry, ResolvedModel
from moira.models.knowledge import Conclusion, Fact, ToolCallPlan, VerificationOutcome
from moira.service_setup import _services
from moira.tools.base import ToolDefinition, ToolResult

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return MoiraConfig()


@pytest.fixture
def mock_writer():
    events = []

    def write(event):
        events.append(event)

    with patch("moira.workflow.nodes.decomposition.get_stream_writer", return_value=write), \
         patch("moira.workflow.nodes.synthesis.get_stream_writer", return_value=write), \
         patch("moira.workflow.nodes.verification.get_stream_writer", return_value=write), \
         patch("moira.workflow.nodes.planning.get_stream_writer", return_value=write), \
         patch("moira.workflow.nodes.research.get_stream_writer", return_value=write), \
         patch("moira.workflow.nodes.report_generation.get_stream_writer", return_value=write), \
         patch("moira.workflow.nodes.tool_identification.get_stream_writer", return_value=write), \
         patch("moira.workflow.nodes._helpers.get_stream_writer", return_value=write):
        yield events


@pytest.fixture
def mock_model():
    client = AsyncMock(spec=InferenceClient)
    client.chat_completion = AsyncMock(return_value=ChatResponse(content="test response"))
    resolved = ResolvedModel(model_id="test-model", client=client)
    registry = MagicMock(spec=ModelRegistry)
    registry.resolve = AsyncMock(return_value=resolved)
    return {"client": client, "resolved": resolved, "registry": registry}


def _inject_services(config, mock_model):
    _services.clear()
    _services["config"] = config
    _services["model_registry"] = mock_model["registry"]


def _make_run_config(config):
    return {"configurable": {"moira_config": config}}


def _build_state(config, question="Test question", facts=None, conclusions=None):
    cw = config.budget.cost_weights
    step_costs = {
        "decomposition": cw.decomposition,
        "tool_identification": cw.tool_identification,
        "planning": cw.planning,
        "research": cw.research,
        "synthesis": cw.synthesis,
        "verification": cw.verification,
        "report_generation": cw.report_generation,
    }
    return {
        "knowledge": {
            "question": question,
            "user_goal": "",
            "topic": "",
            "entities": [],
            "concepts": [],
            "facts": facts or [],
            "conclusions": conclusions or [],
            "citations": [],
            "verification_history": [],
        },
        "execution_state": {
            "candidate_tools": [],
            "tool_call_plan": [],
            "budget_remaining": float(config.budget.default_limit),
            "budget_limit": float(config.budget.default_limit),
            "step_costs": step_costs,
            "tool_costs": {},
            "tool_call_counts": {},
            "total_tool_cost_consumed": 0.0,
            "error": "",
            "synthesis_retry_count": 0,
            "verification_attempts": 0,
        },
    }


DECOMPOSITION_RESPONSE = json.dumps({
    "user_goal": "Find information about test topic",
    "topic": "testing",
    "entities": ["entity1", "entity2"],
    "concepts": ["concept1"],
    "unknown_facts": [
        {"subject": "entity1", "fact_needed": "fact about entity1"},
        {"subject": "entity2", "fact_needed": "fact about entity2"},
    ],
})

PLANNING_RESPONSE = json.dumps({
    "calls": [
        {
            "tool": "web_search",
            "args": {"query": "entity1 fact"},
            "target_fact_ids": ["f001"],
            "rationale": "Search for entity1 information",
        },
        {
            "tool": "web_search",
            "args": {"query": "entity2 fact"},
            "target_fact_ids": ["f002"],
            "rationale": "Search for entity2 information",
        },
    ],
})

SYNTHESIS_RESPONSE = json.dumps({
    "conclusions": [
        {
            "conclusion": "Entity1 has been confirmed",
            "supporting_fact_ids": ["f001"],
            "reasoning": "Based on verified data",
        },
    ],
})

VERIFICATION_RESPONSE = json.dumps({
    "fact_results": [
        {"fact_id": "f001", "result": "verified", "evidence": "Confirmed by source"},
    ],
    "conclusion_results": [
        {"conclusion_id": "c001", "result": "verified", "reason": "Supported by facts"},
    ],
    "new_unknown_facts": [],
    "goal_met": True,
    "goal_assessment": "Goal fully met",
    "route": "accept",
})

REPORT_RESPONSE = json.dumps({
    "answer": "Entity1 is confirmed based on research.",
    "citations": [],
    "verified_facts": [],
    "verified_conclusions": [],
    "contradicted": [],
    "unknown_facts": [],
    "critiques": [],
    "total_cost": 0,
    "tool_call_total_cost": 0,
    "generation_path": "verified",
})


# ===========================================================================
# DECOMPOSITION
# ===========================================================================


class TestDecomposition:

    @pytest.mark.asyncio
    async def test_happy_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=DECOMPOSITION_RESPONSE
        )

        from moira.workflow.nodes.decomposition import decomposition

        state = _build_state(config, "Tell me about test topic")
        result = await decomposition(state, _make_run_config(config))

        assert "knowledge" in result
        assert "execution_state" in result
        assert result["knowledge"]["user_goal"] == "Find information about test topic"
        assert result["knowledge"]["topic"] == "testing"
        assert result["knowledge"]["entities"] == ["entity1", "entity2"]
        assert result["knowledge"]["concepts"] == ["concept1"]
        assert len(result["knowledge"]["facts"]) == 2
        assert result["knowledge"]["facts"][0]["status"] == "unknown"
        assert result["knowledge"]["facts"][0]["subject"] == "entity1"

        step_cost = config.budget.cost_weights.decomposition
        expected_budget = config.budget.default_limit - step_cost
        assert result["execution_state"]["budget_remaining"] == expected_budget

    @pytest.mark.asyncio
    async def test_budget_exhaustion(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        from moira.workflow.nodes.decomposition import decomposition

        state = _build_state(config, "Tell me about test topic")
        state["execution_state"]["budget_remaining"] = 0.0

        result = await decomposition(state, _make_run_config(config))

        assert "error" in result["execution_state"]
        assert "Insufficient budget" in result["execution_state"]["error"]
        assert result["execution_state"]["budget_remaining"] == 0.0

    @pytest.mark.asyncio
    async def test_model_failure(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = RuntimeError("Model unavailable")

        from moira.workflow.nodes.decomposition import decomposition

        state = _build_state(config, "Tell me about test topic")

        with pytest.raises(RuntimeError, match="Model unavailable"):
            await decomposition(state, _make_run_config(config))

    @pytest.mark.asyncio
    async def test_malformed_json_response(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content="This is not JSON at all"
        )

        from moira.workflow.nodes.decomposition import decomposition

        state = _build_state(config, "Tell me about test topic")
        result = await decomposition(state, _make_run_config(config))

        assert result["knowledge"]["user_goal"] == ""
        assert result["knowledge"]["facts"] == []

    @pytest.mark.asyncio
    async def test_empty_unknown_facts(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({
                "user_goal": "Vague question",
                "topic": "general",
                "entities": [],
                "concepts": [],
                "unknown_facts": [],
            })
        )

        from moira.workflow.nodes.decomposition import decomposition

        state = _build_state(config, "Hello")
        result = await decomposition(state, _make_run_config(config))

        assert result["knowledge"]["facts"] == []

    @pytest.mark.asyncio
    async def test_facts_get_sequential_ids(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({
                "user_goal": "g",
                "topic": "t",
                "entities": [],
                "concepts": [],
                "unknown_facts": [
                    {"subject": "a", "fact_needed": "x"},
                    {"subject": "b", "fact_needed": "y"},
                    {"subject": "c", "fact_needed": "z"},
                ],
            })
        )

        from moira.workflow.nodes.decomposition import decomposition

        state = _build_state(config, "q")
        result = await decomposition(state, _make_run_config(config))

        ids = [f["id"] for f in result["knowledge"]["facts"]]
        assert ids == ["f001", "f002", "f003"]


# ===========================================================================
# TOOL IDENTIFICATION
# ===========================================================================


class TestToolIdentification:

    @pytest.mark.asyncio
    async def test_happy_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=[
            ToolDefinition(name="web_search", description="Search the web"),
            ToolDefinition(name="api_tool", description="API tool"),
        ])
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = [
            ToolDefinition(name="calculator", description="Math", is_default=True),
        ]
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="entity1", fact_needed="some fact", status="unknown"),
        ]

        result = await tool_identification(state, _make_run_config(config))

        candidates = result["execution_state"]["candidate_tools"]
        names = [t.name for t in candidates]
        assert "web_search" in names
        assert "api_tool" in names
        assert "calculator" in names

        step_cost = config.budget.cost_weights.tool_identification
        assert result["execution_state"]["budget_remaining"] == (
            config.budget.default_limit - step_cost
        )

    @pytest.mark.asyncio
    async def test_empty_facts_list(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=[])
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = [
            ToolDefinition(name="calculator", description="Math", is_default=True),
        ]
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = []

        result = await tool_identification(state, _make_run_config(config))

        mock_discovery.discover.assert_not_called()
        names = [t.name for t in result["execution_state"]["candidate_tools"]]
        assert "calculator" in names

    @pytest.mark.asyncio
    async def test_discovery_failure_gets_defaults(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(side_effect=Exception("LanceDB down"))
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = [
            ToolDefinition(name="calculator", description="Math", is_default=True),
        ]
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        result = await tool_identification(state, _make_run_config(config))

        names = [t.name for t in result["execution_state"]["candidate_tools"]]
        assert "calculator" in names

    @pytest.mark.asyncio
    async def test_deduplication_of_discovered_tools(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=[
            ToolDefinition(name="web_search", description="Search"),
            ToolDefinition(name="web_search", description="Search again"),
        ])
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = []
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        result = await tool_identification(state, _make_run_config(config))

        names = [t.name for t in result["execution_state"]["candidate_tools"]]
        assert names.count("web_search") == 1

    @pytest.mark.asyncio
    async def test_skips_verified_facts(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=[])
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = []
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="verified"),
        ]

        await tool_identification(state, _make_run_config(config))
        mock_discovery.discover.assert_not_called()


# ===========================================================================
# PLANNING
# ===========================================================================


class TestPlanning:

    @pytest.mark.asyncio
    async def test_happy_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=PLANNING_RESPONSE
        )

        from moira.workflow.nodes.planning import planning

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="entity1", fact_needed="some fact", status="unknown"),
            Fact(id="f002", subject="entity2", fact_needed="another fact", status="unknown"),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]

        result = await planning(state, _make_run_config(config))

        plan = result["execution_state"]["tool_call_plan"]
        assert len(plan) == 2
        assert plan[0]["tool"] == "web_search"
        assert plan[0]["target_fact_ids"] == ["f001"]
        assert plan[1]["target_fact_ids"] == ["f002"]

        step_cost = config.budget.cost_weights.planning
        assert result["execution_state"]["budget_remaining"] == (
            config.budget.default_limit - step_cost
        )

    @pytest.mark.asyncio
    async def test_budget_exhaustion(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        from moira.workflow.nodes.planning import planning

        state = _build_state(config, "Test question")
        state["execution_state"]["budget_remaining"] = 0.0

        result = await planning(state, _make_run_config(config))

        assert "error" in result["execution_state"]
        assert "Insufficient budget" in result["execution_state"]["error"]

    @pytest.mark.asyncio
    async def test_model_failure(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = RuntimeError("Model down")

        from moira.workflow.nodes.planning import planning

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        with pytest.raises(RuntimeError, match="Model down"):
            await planning(state, _make_run_config(config))

    @pytest.mark.asyncio
    async def test_empty_tool_plan(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"calls": []})
        )

        from moira.workflow.nodes.planning import planning

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        result = await planning(state, _make_run_config(config))
        assert result["execution_state"]["tool_call_plan"] == []

    @pytest.mark.asyncio
    async def test_malformed_response_gives_empty_plan(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content="no json here"
        )

        from moira.workflow.nodes.planning import planning

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        result = await planning(state, _make_run_config(config))
        assert result["execution_state"]["tool_call_plan"] == []


# ===========================================================================
# RESEARCH
# ===========================================================================


class TestResearch:

    @pytest.mark.asyncio
    async def test_happy_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(tool_name="web_search",
                       output="Entity1 is confirmed",
                       success=True, duration_ms=100),
        ])
        _services["tool_executor"] = mock_executor

        model_responses = [
            ChatResponse(content=json.dumps([
                {"tool": "web_search", "args": {"query": "entity1"}},
            ])),
            ChatResponse(content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [
                    {"fact_id": "f001", "claim": "Entity1 confirmed"},
                ],
                "sources": [],
            })),
        ]
        mock_model["client"].chat_completion.side_effect = model_responses

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="entity1", fact_needed="some fact", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(tool="web_search", args={"query": "entity1"},
                         target_fact_ids=["f001"], cost=1.0),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]

        result = await research(state, _make_run_config(config))

        assert len(result["knowledge"]["facts"]) >= 1
        assert result["knowledge"]["facts"][0]["status"] == "unverified"
        assert "confirmed" in result["knowledge"]["facts"][0]["claim"].lower()

        assert len(result["knowledge"]["citations"]) >= 1
        assert result["execution_state"]["tool_call_counts"]["web_search"] == 1
        assert result["execution_state"]["total_tool_cost_consumed"] == 1.0

        step_cost = config.budget.cost_weights.research
        expected = config.budget.default_limit - step_cost - 1.0
        assert result["execution_state"]["budget_remaining"] == expected

    @pytest.mark.asyncio
    async def test_budget_exhaustion(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["execution_state"]["budget_remaining"] = 0.0

        result = await research(state, _make_run_config(config))

        assert "error" in result["execution_state"]
        assert "Insufficient budget" in result["execution_state"]["error"]

    @pytest.mark.asyncio
    async def test_model_interpretation_failure_fatal(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(tool_name="web_search", output="Some result", success=True, duration_ms=50),
        ])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            RuntimeError("Model crashed"),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(tool="web_search", args={"query": "x"},
                         target_fact_ids=["f001"], cost=1.0),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]

        with pytest.raises(RuntimeError, match="Model crashed"):
            await research(state, _make_run_config(config))

    @pytest.mark.asyncio
    async def test_no_tool_executor_skips_calls(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"tool_calls": [], "discovered_facts": [], "sources": []})
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(tool="web_search", args={"query": "x"},
                         target_fact_ids=["f001"], cost=1.0),
        ]

        result = await research(state, _make_run_config(config))

        assert result["execution_state"]["total_tool_cost_consumed"] == 0.0

    @pytest.mark.asyncio
    async def test_tool_execution_error_continues(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(side_effect=Exception("Tool error"))
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"tool_calls": [], "discovered_facts": [], "sources": []})
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(tool="web_search", args={"query": "x"},
                         target_fact_ids=["f001"], cost=1.0),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]

        result = await research(state, _make_run_config(config))

        assert result["knowledge"]["facts"][0]["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_empty_tool_plan(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"tool_calls": [], "discovered_facts": [], "sources": []})
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = []
        state["execution_state"]["tool_call_plan"] = []

        result = await research(state, _make_run_config(config))

        assert result["execution_state"]["total_tool_cost_consumed"] == 0.0

    @pytest.mark.asyncio
    async def test_per_tool_budget_skip(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(tool_name="expensive_tool", output="data", success=True, duration_ms=50),
        ])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"tool_calls": [], "discovered_facts": [], "sources": []})
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(tool="expensive_tool", args={}, target_fact_ids=["f001"], cost=50.0),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="expensive_tool", description="Expensive"),
        ]
        state["execution_state"]["tool_costs"] = {"expensive_tool": 50.0}
        state["execution_state"]["budget_remaining"] = 5.0

        result = await research(state, _make_run_config(config))

        assert result["execution_state"]["total_tool_cost_consumed"] == 0.0


# ===========================================================================
# SYNTHESIS
# ===========================================================================


class TestSynthesis:

    @pytest.mark.asyncio
    async def test_happy_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=SYNTHESIS_RESPONSE
        )

        from moira.workflow.nodes.synthesis import synthesis

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["topic"] = "testing"
        state["knowledge"]["entities"] = ["entity1"]
        state["knowledge"]["facts"] = [
            Fact(
                id="f001", subject="entity1", fact_needed="some fact",
                claim="Entity1 confirmed", status="unverified",
                citation_ids=["cit001"],
            ),
        ]

        result = await synthesis(state, _make_run_config(config))

        assert len(result["knowledge"]["conclusions"]) == 1
        assert result["knowledge"]["conclusions"][0]["conclusion"] == "Entity1 has been confirmed"
        assert result["knowledge"]["conclusions"][0]["supporting_fact_ids"] == ["f001"]
        assert result["knowledge"]["conclusions"][0]["status"] == "unverified"

        step_cost = config.budget.cost_weights.synthesis
        assert result["execution_state"]["budget_remaining"] == (
            config.budget.default_limit - step_cost
        )

    @pytest.mark.asyncio
    async def test_budget_exhaustion(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        from moira.workflow.nodes.synthesis import synthesis

        state = _build_state(config, "Test question")
        state["execution_state"]["budget_remaining"] = 0.0

        result = await synthesis(state, _make_run_config(config))

        assert "error" in result["execution_state"]
        assert "Insufficient budget" in result["execution_state"]["error"]

    @pytest.mark.asyncio
    async def test_model_failure(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = RuntimeError("Model error")

        from moira.workflow.nodes.synthesis import synthesis

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]

        with pytest.raises(RuntimeError, match="Model error"):
            await synthesis(state, _make_run_config(config))

    @pytest.mark.asyncio
    async def test_no_facts_with_claims(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"conclusions": []})
        )

        from moira.workflow.nodes.synthesis import synthesis

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        result = await synthesis(state, _make_run_config(config))
        assert result["knowledge"]["conclusions"] == []

    @pytest.mark.asyncio
    async def test_multiple_conclusions_get_sequential_ids(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({
                "conclusions": [
                    {"conclusion": "First", "supporting_fact_ids": ["f001"], "reasoning": "r1"},
                    {"conclusion": "Second", "supporting_fact_ids": ["f002"], "reasoning": "r2"},
                ],
            })
        )

        from moira.workflow.nodes.synthesis import synthesis

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="a", status="unverified"),
            Fact(id="f002", subject="z", fact_needed="w", claim="b", status="unverified"),
        ]

        result = await synthesis(state, _make_run_config(config))
        ids = [c["id"] for c in result["knowledge"]["conclusions"]]
        assert ids == ["c001", "c002"]


# ===========================================================================
# VERIFICATION
# ===========================================================================


class TestVerification:

    @pytest.mark.asyncio
    async def test_happy_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=VERIFICATION_RESPONSE
        )

        from moira.workflow.nodes.verification import verification

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(
                id="f001", subject="entity1", fact_needed="some fact",
                claim="Entity1 confirmed", status="unverified",
                citation_ids=["cit001"],
            ),
        ]
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001", conclusion="Entity1 is confirmed",
                supporting_fact_ids=["f001"], status="unverified",
            ),
        ]

        result = await verification(state, _make_run_config(config))

        assert result["knowledge"]["facts"][0]["status"] == "verified"
        assert result["knowledge"]["facts"][0]["verification_note"] == "Confirmed by source"
        assert result["knowledge"]["conclusions"][0]["status"] == "verified"

        history = result["knowledge"]["verification_history"]
        assert len(history) == 1
        assert history[0]["goal_met"] is True
        assert history[0]["route"] == "accept"

        step_cost = config.budget.cost_weights.verification
        assert result["execution_state"]["budget_remaining"] == (
            config.budget.default_limit - step_cost
        )
        assert result["execution_state"]["verification_attempts"] == 1

    @pytest.mark.asyncio
    async def test_budget_exhaustion(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        from moira.workflow.nodes.verification import verification

        state = _build_state(config, "Test question")
        state["execution_state"]["budget_remaining"] = 0.0

        result = await verification(state, _make_run_config(config))

        assert "error" in result["execution_state"]
        assert "Insufficient budget" in result["execution_state"]["error"]

    @pytest.mark.asyncio
    async def test_model_failure(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = RuntimeError("Model error")

        from moira.workflow.nodes.verification import verification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]

        with pytest.raises(RuntimeError, match="Model error"):
            await verification(state, _make_run_config(config))

    @pytest.mark.asyncio
    async def test_new_unknown_facts_added(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({
                "fact_results": [],
                "conclusion_results": [],
                "new_unknown_facts": ["Additional detail needed"],
                "goal_met": False,
                "goal_assessment": "Missing info",
                "route": "retry_research",
            })
        )

        from moira.workflow.nodes.verification import verification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]

        result = await verification(state, _make_run_config(config))

        fact_ids = [f["id"] for f in result["knowledge"]["facts"]]
        assert len(fact_ids) == 2
        assert result["knowledge"]["facts"][1]["fact_needed"] == "Additional detail needed"
        assert result["knowledge"]["facts"][1]["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_contradicted_facts_and_conclusions(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({
                "fact_results": [
                    {"fact_id": "f001", "result": "contradicted", "evidence": "Wrong"},
                ],
                "conclusion_results": [
                    {"conclusion_id": "c001", "result": "contradicted",
                     "reason": "Based on wrong fact"},
                ],
                "new_unknown_facts": [],
                "goal_met": False,
                "goal_assessment": "Contradictions found",
                "route": "retry_research",
            })
        )

        from moira.workflow.nodes.verification import verification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]
        state["knowledge"]["conclusions"] = [
            Conclusion(id="c001", conclusion="Something",
                       supporting_fact_ids=["f001"], status="unverified"),
        ]

        result = await verification(state, _make_run_config(config))

        assert result["knowledge"]["facts"][0]["status"] == "contradicted"
        assert result["knowledge"]["conclusions"][0]["status"] == "contradicted"
        assert result["knowledge"]["verification_history"][0]["route"] == "retry_research"

    @pytest.mark.asyncio
    async def test_increments_verification_attempts(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=VERIFICATION_RESPONSE
        )

        from moira.workflow.nodes.verification import verification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]
        state["execution_state"]["verification_attempts"] = 2

        result = await verification(state, _make_run_config(config))
        assert result["execution_state"]["verification_attempts"] == 3


# ===========================================================================
# REPORT GENERATION
# ===========================================================================


class TestReportGeneration:

    @pytest.mark.asyncio
    async def test_happy_path_verified(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=REPORT_RESPONSE
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="verified"),
        ]
        state["knowledge"]["conclusions"] = [
            Conclusion(id="c001", conclusion="Confirmed",
                       supporting_fact_ids=["f001"], status="verified"),
        ]
        state["knowledge"]["verification_history"] = [
            VerificationOutcome(
                fact_results=[], conclusion_results=[], new_unknown_facts=[],
                goal_met=True, goal_assessment="Done", route="accept",
            ),
        ]

        result = await report_generation(state, _make_run_config(config))

        assert result["knowledge"]["report"] is not None
        assert result["knowledge"]["report"]["answer"] == "Entity1 is confirmed based on research."
        assert result["knowledge"]["report"]["generation_path"] == "verified"
        assert result["knowledge"]["generation_path"] == "verified"

        step_cost = config.budget.cost_weights.report_generation
        assert result["execution_state"]["budget_remaining"] == (
            config.budget.default_limit - step_cost
        )

    @pytest.mark.asyncio
    async def test_always_runs_even_with_zero_budget(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=REPORT_RESPONSE
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["execution_state"]["budget_remaining"] = 0.0

        result = await report_generation(state, _make_run_config(config))

        assert result["knowledge"]["report"] is not None
        assert ("error" not in result["execution_state"]
                or result["execution_state"].get("error") == "")

    @pytest.mark.asyncio
    async def test_model_failure_fallback(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = RuntimeError("Model crashed")

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"

        result = await report_generation(state, _make_run_config(config))

        assert result["knowledge"]["report"] is not None
        assert result["knowledge"]["report"]["generation_path"] == "budget_exhausted"
        assert len(result["knowledge"]["report"]["critiques"]) > 0
        assert "failed" in result["knowledge"]["report"]["critiques"][0].lower()

    @pytest.mark.asyncio
    async def test_error_generation_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=REPORT_RESPONSE
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["execution_state"]["error"] = "Something went wrong"

        result = await report_generation(state, _make_run_config(config))

        assert result["knowledge"]["generation_path"] == "error"

    @pytest.mark.asyncio
    async def test_budget_exhausted_path_when_goal_not_met(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=REPORT_RESPONSE
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["verification_history"] = [
            VerificationOutcome(
                fact_results=[], conclusion_results=[], new_unknown_facts=[],
                goal_met=False, goal_assessment="Incomplete", route="retry_research",
            ),
        ]

        result = await report_generation(state, _make_run_config(config))
        assert result["knowledge"]["generation_path"] == "budget_exhausted"

    @pytest.mark.asyncio
    async def test_no_verification_history_is_budget_exhausted(
        self, config, mock_writer, mock_model,
    ):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=REPORT_RESPONSE
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")

        result = await report_generation(state, _make_run_config(config))
        assert result["knowledge"]["generation_path"] == "budget_exhausted"

    @pytest.mark.asyncio
    async def test_verified_path_with_contradicted_conclusion(
        self, config, mock_writer, mock_model,
    ):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=REPORT_RESPONSE
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["conclusions"] = [
            Conclusion(id="c001", conclusion="A", supporting_fact_ids=[], status="contradicted"),
        ]
        state["knowledge"]["verification_history"] = [
            VerificationOutcome(
                fact_results=[], conclusion_results=[], new_unknown_facts=[],
                goal_met=True, goal_assessment="Done", route="accept",
            ),
        ]

        result = await report_generation(state, _make_run_config(config))

        assert result["knowledge"]["generation_path"] == "budget_exhausted"

    @pytest.mark.asyncio
    async def test_report_includes_tool_cost(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=REPORT_RESPONSE
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["execution_state"]["total_tool_cost_consumed"] = 7.5

        result = await report_generation(state, _make_run_config(config))

        assert result["knowledge"]["report"]["tool_call_total_cost"] == 7.5
