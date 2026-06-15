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
            "tool_call_limits": {},
            "tool_call_counts": {},
            "total_tool_cost_consumed": 0.0,
            "error": "",
            "synthesis_retry_count": 0,
            "research_retry_count": 0,
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

    @pytest.mark.asyncio
    async def test_post_loop_fact_extraction(self, config, mock_writer, mock_model):
        """When the model produces tool_calls but never includes discovered_facts,
        the post-loop extraction step should extract facts from tool results."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(
                tool_name="web_search",
                output="The limit of sqrt(x)/log2(x) as x->infinity is infinity",
                success=True,
                duration_ms=50,
            ),
        ])
        _services["tool_executor"] = mock_executor

        # Round 1: model returns tool_calls but no discovered_facts
        # Post-loop: model extracts facts from tool results
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps({
                    "tool_calls": [
                        {"tool": "web_search", "args": {"query": "sqrt vs log2 limit"}},
                    ],
                }),
            ),
            ChatResponse(
                content=json.dumps({
                    "discovered_facts": [
                        {
                            "fact_id": "f001",
                            "subject": "limit",
                            "claim": "lim(x->inf) sqrt(x)/log2(x) = infinity",
                        },
                    ],
                    "sources": [
                        {"source": "web_search", "excerpt": "sqrt(x)/log2(x) -> infinity"},
                    ],
                }),
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Compare sqrt(x) and log2(x)")
        state["knowledge"]["user_goal"] = "Compare growth rates"
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="limit",
                fact_needed="limit of sqrt(x)/log2(x)",
                status="unknown",
            ),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "sqrt vs log2"},
                target_fact_ids=["f001"], cost=1.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]

        result = await research(state, _make_run_config(config))

        # The post-loop extraction should have resolved the fact
        facts = result["knowledge"]["facts"]
        resolved = [f for f in facts if f["status"] != "unknown" and f.get("claim")]
        assert len(resolved) == 1
        assert resolved[0]["id"] == "f001"
        assert "infinity" in resolved[0]["claim"].lower()
        assert mock_model["client"].chat_completion.call_count == 2


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

    @pytest.mark.asyncio
    async def test_tool_calls_executed_and_re_prompted(self, config, mock_writer, mock_model):
        """When the model returns tool_calls instead of fact_results, the
        node should execute them and re-prompt for a verification result."""
        _inject_services(config, mock_model)

        tool_call_response = ChatResponse(
            content=json.dumps({
                "tool_calls": [
                    {"tool": "web_search", "args": {"query": "verify entity1"}},
                ],
            })
        )
        verification_result = ChatResponse(
            content=json.dumps({
                "fact_results": [
                    {"fact_id": "f001", "result": "verified", "evidence": "Confirmed via search"},
                ],
                "conclusion_results": [
                    {"conclusion_id": "c001", "result": "verified", "reason": "Fact confirmed"},
                ],
                "new_unknown_facts": [],
                "goal_met": True,
                "goal_assessment": "All verified",
                "route": "accept",
            })
        )
        mock_model["client"].chat_completion = AsyncMock(
            side_effect=[tool_call_response, verification_result]
        )

        executor = AsyncMock()
        executor.execute_batch = AsyncMock(return_value=[
            ToolResult(
                tool_name="web_search",
                output="Search confirmed entity1",
                duration_ms=100,
                success=True,
            ),
        ])
        _services["tool_executor"] = executor

        from moira.workflow.nodes.verification import verification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="entity1", fact_needed="some fact",
                 claim="Entity1 confirmed", status="unverified"),
        ]
        state["knowledge"]["conclusions"] = [
            Conclusion(id="c001", conclusion="Entity1 is confirmed",
                       supporting_fact_ids=["f001"], status="unverified"),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]
        state["execution_state"]["tool_costs"] = {"web_search": 5.0}
        state["execution_state"]["tool_call_limits"] = {"web_search": 3}

        result = await verification(state, _make_run_config(config))

        assert result["knowledge"]["facts"][0]["status"] == "verified"
        assert result["knowledge"]["facts"][0]["verification_note"] == "Confirmed via search"
        assert result["knowledge"]["conclusions"][0]["status"] == "verified"
        assert result["knowledge"]["verification_history"][0]["route"] == "accept"

        # Tool executor was called
        executor.execute_batch.assert_called_once()

        # Model was called twice: initial + re-prompt
        assert mock_model["client"].chat_completion.call_count == 2

        # Tool costs deducted
        assert result["execution_state"]["total_tool_cost_consumed"] == 5.0

        # Call counts updated
        assert result["execution_state"]["tool_call_counts"]["web_search"] == 1

    @pytest.mark.asyncio
    async def test_structured_output_always_has_verification_schema(
        self, config, mock_writer, mock_model
    ):
        """structured_output should contain verification fields, not tool_calls."""
        _inject_services(config, mock_model)

        tool_call_response = ChatResponse(
            content=json.dumps({
                "tool_calls": [
                    {"tool": "web_search", "args": {"query": "test"}},
                ],
            })
        )
        verification_result = ChatResponse(
            content=json.dumps({
                "fact_results": [{"fact_id": "f001", "result": "verified", "evidence": "OK"}],
                "conclusion_results": [],
                "new_unknown_facts": [],
                "goal_met": True,
                "goal_assessment": "Done",
                "route": "accept",
            })
        )
        mock_model["client"].chat_completion = AsyncMock(
            side_effect=[tool_call_response, verification_result]
        )

        executor = AsyncMock()
        executor.execute_batch = AsyncMock(return_value=[
            ToolResult(tool_name="web_search", output="result", duration_ms=50, success=True),
        ])
        _services["tool_executor"] = executor

        from moira.workflow.nodes.verification import verification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_costs"] = {"web_search": 5.0}

        await verification(state, _make_run_config(config))

        # Find the node_end event and check structured_output
        node_end_events = [
            e for e in mock_writer
            if isinstance(e, dict) and e.get("event") == "node_end"
        ]
        assert len(node_end_events) == 1
        so = node_end_events[0]["payload"]["detail"]["structured_output"]
        assert "fact_results" in so
        assert "conclusion_results" in so
        assert "goal_met" in so
        assert "route" in so
        assert "tool_calls" not in so

    @pytest.mark.asyncio
    async def test_tool_call_limit_enforced(self, config, mock_writer, mock_model):
        """Tool calls that exceed the call limit should be skipped, and the
        node should fail because no verification result was produced."""
        _inject_services(config, mock_model)

        tool_call_response = ChatResponse(
            content=json.dumps({
                "tool_calls": [
                    {"tool": "web_search", "args": {"query": "test"}},
                ],
            })
        )
        verification_result = ChatResponse(
            content=json.dumps({
                "fact_results": [],
                "conclusion_results": [],
                "new_unknown_facts": [],
                "goal_met": True,
                "goal_assessment": "Done",
                "route": "accept",
            })
        )
        mock_model["client"].chat_completion = AsyncMock(
            side_effect=[tool_call_response, verification_result]
        )

        executor = AsyncMock()
        executor.execute_batch = AsyncMock(return_value=[])
        _services["tool_executor"] = executor

        from moira.workflow.nodes.verification import verification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_call_limits"] = {"web_search": 1}
        state["execution_state"]["tool_call_counts"] = {"web_search": 1}

        with pytest.raises(RuntimeError, match="unparseable JSON"):
            await verification(state, _make_run_config(config))

        # Tool executor should NOT be called (limit already reached)
        executor.execute_batch.assert_not_called()


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
    async def test_retry_overruled_path_when_goal_not_met(self, config, mock_writer, mock_model):
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
        assert result["knowledge"]["generation_path"] == "retry_overruled"

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


# ===========================================================================
# PHASE C: Tool costs, call limits, and per-fact discovery
# ===========================================================================


class TestToolIdentificationPhaseC:

    @pytest.mark.asyncio
    async def test_per_fact_discovery_queries(self, config, mock_writer, mock_model):
        """Each unknown fact should trigger its own discover() call."""
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
            Fact(id="f001", subject="pokemon", fact_needed="typing", status="unknown"),
            Fact(id="f002", subject="pokemon", fact_needed="base stats", status="unknown"),
            Fact(id="f003", subject="pokemon", fact_needed="evolution", status="unknown"),
        ]

        await tool_identification(state, _make_run_config(config))

        assert mock_discovery.discover.call_count == 3
        calls = mock_discovery.discover.call_args_list
        assert calls[0][0][0] == "pokemon typing"
        assert calls[1][0][0] == "pokemon base stats"
        assert calls[2][0][0] == "pokemon evolution"

    @pytest.mark.asyncio
    async def test_per_fact_discovery_merges_across_facts(self, config, mock_writer, mock_model):
        """Different facts may discover different tools; all should be merged."""
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(side_effect=[
            [ToolDefinition(name="pokeapi", description="Pokemon API")],
            [ToolDefinition(name="web_search", description="Search")],
            [ToolDefinition(name="pokeapi", description="Pokemon API again")],
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
            Fact(id="f001", subject="a", fact_needed="x", status="unknown"),
            Fact(id="f002", subject="b", fact_needed="y", status="unknown"),
            Fact(id="f003", subject="c", fact_needed="z", status="unknown"),
        ]

        result = await tool_identification(state, _make_run_config(config))

        names = [t.name for t in result["execution_state"]["candidate_tools"]]
        assert "pokeapi" in names
        assert "web_search" in names
        assert "calculator" in names
        assert names.count("pokeapi") == 1

    @pytest.mark.asyncio
    async def test_tool_costs_populated_from_candidates(self, config, mock_writer, mock_model):
        """tool_costs should be populated from candidate tools' invocation_cost."""
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=[
            ToolDefinition(name="web_search", description="Search", invocation_cost=5.0),
            ToolDefinition(name="api_tool", description="API", invocation_cost=2.0),
        ])
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = [
            ToolDefinition(
                name="calculator", description="Math",
                is_default=True, invocation_cost=0.1,
            ),
        ]
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        result = await tool_identification(state, _make_run_config(config))

        tc = result["execution_state"]["tool_costs"]
        assert tc["web_search"] == 5.0
        assert tc["api_tool"] == 2.0
        assert tc["calculator"] == 0.1

    @pytest.mark.asyncio
    async def test_tool_call_limits_populated(self, config, mock_writer, mock_model):
        """tool_call_limits should be populated for tools with limit > 0."""
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=[
            ToolDefinition(
                name="web_search", description="Search",
                invocation_cost=5.0, call_limit_per_run=10,
            ),
            ToolDefinition(
                name="api_tool", description="API",
                invocation_cost=2.0,
            ),
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

        limits = result["execution_state"]["tool_call_limits"]
        assert limits["web_search"] == 10
        assert "api_tool" not in limits

    @pytest.mark.asyncio
    async def test_tool_costs_merges_with_initial_state(self, config, mock_writer, mock_model):
        """tool_costs from initial_state (from streaming.py catalog) should
        be preserved when tool_identification merges its candidate costs."""
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=[
            ToolDefinition(name="web_search", description="Search", invocation_cost=5.0),
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
        state["execution_state"]["tool_costs"] = {
            "url_content": 3.0,
            "calculator": 0.1,
        }
        state["execution_state"]["tool_call_limits"] = {
            "url_content": 15,
        }

        result = await tool_identification(state, _make_run_config(config))

        tc = result["execution_state"]["tool_costs"]
        assert tc["web_search"] == 5.0
        assert tc["url_content"] == 3.0
        assert tc["calculator"] == 0.1

        limits = result["execution_state"]["tool_call_limits"]
        assert limits["url_content"] == 15

    @pytest.mark.asyncio
    async def test_detail_includes_per_fact_queries(self, config, mock_writer, mock_model):
        """The streamed detail should include per-fact query info with top_results."""
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=[
            ToolDefinition(name="pokeapi", description="API"),
            ToolDefinition(name="pokemon_db", description="DB"),
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
            Fact(id="f001", subject="Tyranitar", fact_needed="typing", status="unknown"),
        ]

        await tool_identification(state, _make_run_config(config))

        node_end = [e for e in mock_writer if e.get("event") == "node_end"][0]
        detail = node_end["payload"]["detail"]
        assert len(detail["queries"]) == 1
        assert detail["queries"][0]["fact_id"] == "f001"
        assert detail["queries"][0]["query"] == "Tyranitar typing"
        assert "pokeapi" in detail["queries"][0]["top_results"]
        assert "calculator" in detail["default_tools_included"]


class TestResearchCallLimits:

    @pytest.mark.asyncio
    async def test_call_limit_enforced(self, config, mock_writer, mock_model):
        """When a tool has hit its call limit, further calls should be skipped."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[])
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
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_call_limits"] = {"web_search": 1}
        state["execution_state"]["tool_call_counts"] = {"web_search": 1}
        state["execution_state"]["tool_costs"] = {"web_search": 5.0}

        result = await research(state, _make_run_config(config))

        mock_executor.execute_batch.assert_not_called()
        assert result["execution_state"]["total_tool_cost_consumed"] == 0.0

    @pytest.mark.asyncio
    async def test_call_limit_partial_batch(self, config, mock_writer, mock_model):
        """When some tools hit their limit, only remaining tools should execute."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(tool_name="api_tool", output="result", success=True, duration_ms=50),
        ])
        _services["tool_executor"] = mock_executor

        model_responses = [
            ChatResponse(content=json.dumps([
                {"tool": "web_search", "args": {"query": "x"}},
                {"tool": "api_tool", "args": {"q": "y"}},
            ])),
            ChatResponse(content=json.dumps({
                "tool_calls": [], "discovered_facts": [], "sources": [],
            })),
        ]
        mock_model["client"].chat_completion.side_effect = model_responses

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "x"},
                target_fact_ids=["f001"], cost=5.0,
            ),
            ToolCallPlan(
                tool="api_tool", args={"q": "y"},
                target_fact_ids=["f001"], cost=2.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
            ToolDefinition(name="api_tool", description="API"),
        ]
        state["execution_state"]["tool_call_limits"] = {"web_search": 2}
        state["execution_state"]["tool_call_counts"] = {"web_search": 2}
        state["execution_state"]["tool_costs"] = {"web_search": 5.0, "api_tool": 2.0}

        result = await research(state, _make_run_config(config))

        executed_calls = mock_executor.execute_batch.call_args[0][0]
        assert len(executed_calls) == 1
        assert executed_calls[0][0] == "api_tool"
        assert result["execution_state"]["tool_call_counts"]["api_tool"] == 1

    @pytest.mark.asyncio
    async def test_tool_cost_deduction_custom_cost(self, config, mock_writer, mock_model):
        """Tool cost deduction should use the cost from tool_costs, not default 1.0."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(tool_name="web_search", output="data", success=True, duration_ms=100),
        ])
        _services["tool_executor"] = mock_executor

        model_responses = [
            ChatResponse(content=json.dumps([
                {"tool": "web_search", "args": {"query": "x"}},
            ])),
            ChatResponse(content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [
                    {"fact_id": "f001", "claim": "Found it"},
                ],
                "sources": [],
            })),
        ]
        mock_model["client"].chat_completion.side_effect = model_responses

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "x"},
                target_fact_ids=["f001"], cost=5.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_costs"] = {"web_search": 5.0}

        result = await research(state, _make_run_config(config))

        assert result["execution_state"]["total_tool_cost_consumed"] == 5.0
        step_cost = config.budget.cost_weights.research
        expected = config.budget.default_limit - step_cost - 5.0
        assert result["execution_state"]["budget_remaining"] == expected

    @pytest.mark.asyncio
    async def test_zero_call_limit_means_unlimited(self, config, mock_writer, mock_model):
        """A call_limit of 0 (or absent) means unlimited calls allowed."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(tool_name="web_search", output="data", success=True, duration_ms=100),
        ])
        _services["tool_executor"] = mock_executor

        model_responses = [
            ChatResponse(content=json.dumps([
                {"tool": "web_search", "args": {"query": "x"}},
            ])),
            ChatResponse(content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [
                    {"fact_id": "f001", "claim": "Found"},
                ],
                "sources": [],
            })),
        ]
        mock_model["client"].chat_completion.side_effect = model_responses

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "x"},
                target_fact_ids=["f001"], cost=1.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_call_limits"] = {}
        state["execution_state"]["tool_call_counts"] = {"web_search": 5}
        state["execution_state"]["tool_costs"] = {"web_search": 1.0}

        result = await research(state, _make_run_config(config))

        assert result["execution_state"]["tool_call_counts"]["web_search"] == 6
        assert result["execution_state"]["total_tool_cost_consumed"] == 1.0


class TestPlanningCallLimits:

    @pytest.mark.asyncio
    async def test_call_limits_in_formatted_tools(self, config, mock_writer, mock_model):
        """_format_tools_with_costs_and_limits should include call limit info."""
        from moira.workflow.nodes.planning import _format_tools_with_costs_and_limits

        tools = [
            ToolDefinition(name="web_search", description="Search the web"),
            ToolDefinition(name="calculator", description="Do math"),
        ]
        tool_costs = {"web_search": 5.0, "calculator": 0.1}
        call_counts = {"web_search": 3}
        call_limits = {"web_search": 10}

        result = _format_tools_with_costs_and_limits(tools, tool_costs, call_counts, call_limits)

        assert "cost per call: 5.0" in result
        assert "cost per call: 0.1" in result
        assert "calls remaining: 7" in result
        assert "unlimited" in result

    @pytest.mark.asyncio
    async def test_call_limit_at_zero_remaining(self, config, mock_writer, mock_model):
        """When a tool has used all its calls, remaining should be 0."""
        from moira.workflow.nodes.planning import _format_tools_with_costs_and_limits

        tools = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        tool_costs = {"web_search": 5.0}
        call_counts = {"web_search": 10}
        call_limits = {"web_search": 10}

        result = _format_tools_with_costs_and_limits(tools, tool_costs, call_counts, call_limits)

        assert "calls remaining: 0" in result

    @pytest.mark.asyncio
    async def test_planning_uses_tool_costs_from_state(self, config, mock_writer, mock_model):
        """Planning should produce a tool_call_plan with costs from tool_costs."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({
                "calls": [
                    {"tool": "web_search", "args": {"query": "x"}, "target_fact_ids": ["f001"]},
                ],
            })
        )

        from moira.workflow.nodes.planning import planning

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_costs"] = {"web_search": 5.0}

        result = await planning(state, _make_run_config(config))

        plan = result["execution_state"]["tool_call_plan"]
        assert len(plan) == 1
        assert plan[0]["cost"] == 5.0


class TestResearchSummaryRound:

    @pytest.mark.asyncio
    async def test_summary_round_on_max_rounds_exhausted(
        self, config, mock_writer, mock_model
    ):
        """When all rounds are used, a final summary round should happen
        and its response should be the one shown in the step detail."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(
                tool_name="web_search", output="Result data",
                success=True, duration_ms=50,
            ),
        ])
        _services["tool_executor"] = mock_executor

        # Round 1: tool calls, Round 2: tool calls, Round 3: tool calls
        # Then summary round (extra model call)
        tool_call_response = json.dumps({
            "tool_calls": [{"tool": "web_search", "args": {"query": "x"}}],
            "discovered_facts": [],
            "sources": [],
        })
        summary_response = json.dumps({
            "tool_calls": [],
            "discovered_facts": [
                {"fact_id": "f001", "subject": "x", "claim": "Found it"},
            ],
            "sources": [{"source": "web_search", "excerpt": "Result data"}],
        })

        # 3 rounds of tool calls + 1 summary round = 4 model calls
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(content=tool_call_response),
            ChatResponse(content=tool_call_response),
            ChatResponse(content=tool_call_response),
            ChatResponse(content=summary_response),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "x"},
                target_fact_ids=["f001"], cost=1.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_costs"] = {"web_search": 1.0}

        await research(state, _make_run_config(config))

        # The response in detail should be the summary, not a tool call list
        node_end_events = [
            e for e in mock_writer if e.get("event") == "node_end"
        ]
        assert len(node_end_events) == 1
        detail = node_end_events[0]["payload"]["detail"]
        assert "Found it" in detail["response"]

        # 3 tool rounds + 1 summary = 4 model calls
        assert mock_model["client"].chat_completion.call_count == 4

    @pytest.mark.asyncio
    async def test_no_summary_when_model_signals_done(
        self, config, mock_writer, mock_model
    ):
        """When the model signals completion (empty tool_calls), no summary
        round should happen."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(
                tool_name="web_search", output="Data",
                success=True, duration_ms=50,
            ),
        ])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(content=json.dumps({
                "tool_calls": [{"tool": "web_search", "args": {"query": "x"}}],
                "discovered_facts": [],
                "sources": [],
            })),
            ChatResponse(content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [
                    {"fact_id": "f001", "subject": "x", "claim": "Done"},
                ],
                "sources": [],
            })),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "x"},
                target_fact_ids=["f001"], cost=1.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_costs"] = {"web_search": 1.0}

        await research(state, _make_run_config(config))

        # Only 2 model calls: tool round + done signal
        assert mock_model["client"].chat_completion.call_count == 2

    @pytest.mark.asyncio
    async def test_node_end_detail_has_no_tool_results(
        self, config, mock_writer, mock_model
    ):
        """The node_end detail should NOT include tool_results (they come from
        tool_result events in the run_manager)."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(
                tool_name="web_search", output="Data", success=True,
                duration_ms=50,
            ),
        ])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(content=json.dumps({
                "tool_calls": [{"tool": "web_search", "args": {"query": "x"}}],
                "discovered_facts": [],
                "sources": [],
            })),
            ChatResponse(content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [
                    {"fact_id": "f001", "subject": "x", "claim": "Found"},
                ],
                "sources": [],
            })),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "x"},
                target_fact_ids=["f001"], cost=1.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_costs"] = {"web_search": 1.0}

        await research(state, _make_run_config(config))

        node_end_events = [
            e for e in mock_writer if e.get("event") == "node_end"
        ]
        detail = node_end_events[0]["payload"]["detail"]
        assert "tool_results" not in detail

    @pytest.mark.asyncio
    async def test_skips_calls_missing_required_params(
        self, config, mock_writer, mock_model
    ):
        """Tool calls missing required parameters should be skipped before
        execution to avoid deterministic failures."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [],
                "sources": [],
            }),
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(
                name="web_search",
                description="Search",
                argument_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            ),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={},
                target_fact_ids=["f001"], cost=1.0,
            ),
        ]
        state["execution_state"]["tool_costs"] = {"web_search": 1.0}

        # Model returns a call with no query param
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(content=json.dumps({
                "tool_calls": [{"tool": "web_search", "args": {}}],
                "discovered_facts": [],
                "sources": [],
            })),
            ChatResponse(content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [],
                "sources": [],
            })),
        ]

        await research(state, _make_run_config(config))

        # web_search should NOT have been executed (missing required param)
        mock_executor.execute_batch.assert_not_called()
