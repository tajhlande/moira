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
from moira.models.knowledge import Conclusion, Fact, ToolCallPlan
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
         patch("moira.workflow.nodes.research_review.get_stream_writer", return_value=write), \
         patch("moira.workflow.nodes.evaluation.get_stream_writer", return_value=write), \
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
        "research_review": cw.research_review,
        "evaluation": cw.evaluation,
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
            "review_history": [],
            "evaluation_history": [],
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
            "research_retry_count": 0,
            "review_count": 0,
            "evaluation_count": 0,
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

REVIEW_RESPONSE = json.dumps({
    "fact_results": [
        {"fact_id": "f001", "result": "verified", "evidence": "Confirmed by source"},
    ],
    "coverage_assessment": "Research sufficiently covered the question",
    "missing_areas": [],
    "route": "continue",
})

REVIEW_RETRY_RESPONSE = json.dumps({
    "fact_results": [
        {"fact_id": "f001", "result": "contradicted", "evidence": "Wrong"},
    ],
    "coverage_assessment": "Missing critical info",
    "missing_areas": ["Need more data on X"],
    "route": "retry",
})

EVALUATION_RESPONSE = json.dumps({
    "conclusion_results": [
        {"conclusion_id": "c001", "result": "verified", "reason": "Supported by facts"},
    ],
    "goal_met": True,
    "goal_assessment": "Goal fully met",
    "route": "accept",
})

EVALUATION_RETRY_RESPONSE = json.dumps({
    "conclusion_results": [
        {"conclusion_id": "c001", "result": "contradicted", "reason": "Based on wrong fact"},
    ],
    "goal_met": False,
    "goal_assessment": "Conclusions flawed",
    "route": "retry",
})

REPORT_RESPONSE = json.dumps({
    "answer": "Entity1 is confirmed based on research. [1]",
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
            ToolResult(
                tool_name="web_search",
                output="Entity1 is confirmed",
                success=True,
                duration_ms=100,
                metadata={"results": [
                    {"title": "Entity1 Page", "url": "https://example.com/entity1",
                     "snippet": "Entity1 is confirmed"},
                ]},
            ),
        ])
        _services["tool_executor"] = mock_executor

        model_responses = [
            ChatResponse(content=json.dumps([
                {"tool": "web_search", "args": {"query": "entity1"}},
            ])),
            ChatResponse(content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [
                    {"fact_id": "f001", "claim": "Entity1 confirmed",
                     "citation_ids": ["cit001"]},
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
        cit = result["knowledge"]["citations"][0]
        assert cit["url"] == "https://example.com/entity1"
        assert cit["title"] == "Entity1 Page"
        assert len(result["knowledge"]["facts"][0].get("citation_ids", [])) >= 1, (
            "Fact should be linked to at least one citation from tool execution"
        )
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
    async def test_citation_linked_when_discovered_facts_come_first(
        self, config, mock_writer, mock_model
    ):
        """Citations must be linked to facts even when the model returns
        discovered_facts in the same round as tool_calls (before tools run).
        _apply_discovered_facts changes status to 'unverified' before tool
        execution, so plan-matching must accept 'unverified' facts too.

        Regression test for citation_ids=[] bug where all report citations
        collapsed to [1] because no facts were linked to any sources."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(tool_name="web_search", output="Result data",
                       success=True, duration_ms=100),
        ])
        _services["tool_executor"] = mock_executor

        # Round 1: model returns tool_calls. Round 2: model sees [cit001]
        # labeled result and returns discovered_facts with citation_ids.
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(content=json.dumps({
                "tool_calls": [{"tool": "web_search", "args": {"query": "test"}}],
                "discovered_facts": [],
                "sources": [],
            })),
            ChatResponse(content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [
                    {"fact_id": "f001", "claim": "Entity1 confirmed",
                     "citation_ids": ["cit001"]},
                ],
                "sources": [],
            })),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="entity1", fact_needed="some fact",
                 status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(tool="web_search", args={"query": "entity1"},
                         target_fact_ids=["f001"], cost=1.0),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]

        result = await research(state, _make_run_config(config))

        fact = result["knowledge"]["facts"][0]
        assert fact["status"] == "unverified"
        assert "cit001" in fact.get("citation_ids", []), (
            "Fact should have citation_ids linked from the model's "
            "discovered_facts response. "
            f"Got citation_ids={fact.get('citation_ids', [])}"
        )

    @pytest.mark.asyncio
    async def test_structured_results_create_per_result_citations(
        self, config, mock_writer, mock_model
    ):
        """When web_search metadata carries structured results, one citation
        is created per search result, each with url/title/excerpt."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(
                tool_name="web_search",
                output="formatted text output",
                success=True,
                duration_ms=100,
                metadata={"results": [
                    {"title": "First", "url": "https://a.com",
                     "snippet": "snippet A"},
                    {"title": "Second", "url": "https://b.com",
                     "snippet": "snippet B"},
                    {"title": "Third", "url": "https://c.com",
                     "snippet": "snippet C"},
                ]},
            ),
        ])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(content=json.dumps({
                "tool_calls": [{"tool": "web_search", "args": {"query": "test"}}],
                "discovered_facts": [],
                "sources": [],
            })),
            ChatResponse(content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [
                    {"fact_id": "f001", "claim": "found it",
                     "citation_ids": ["cit002"]},
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
            ToolCallPlan(tool="web_search", args={"query": "x"},
                         target_fact_ids=["f001"], cost=1.0),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]

        result = await research(state, _make_run_config(config))

        citations = result["knowledge"]["citations"]
        assert len(citations) == 3
        assert citations[0]["id"] == "cit001"
        assert citations[0]["url"] == "https://a.com"
        assert citations[0]["title"] == "First"
        assert citations[0]["excerpt"] == "snippet A"
        assert citations[0]["snippets"] == ["snippet A"]
        assert citations[1]["id"] == "cit002"
        assert citations[1]["url"] == "https://b.com"
        assert citations[2]["id"] == "cit003"
        assert citations[2]["url"] == "https://c.com"

    @pytest.mark.asyncio
    async def test_no_metadata_falls_back_to_single_citation(
        self, config, mock_writer, mock_model
    ):
        """Tools without metadata (e.g., url_content, calculator) still
        get a single citation with text excerpt."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(tool_name="url_content",
                       output="# Some Page\n\nLots of content here.",
                       success=True, duration_ms=100),
        ])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(content=json.dumps({
                "tool_calls": [{"tool": "url_content", "args": {"url": "https://x.com"}}],
                "discovered_facts": [],
                "sources": [],
            })),
            ChatResponse(content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [
                    {"fact_id": "f001", "claim": "found it",
                     "citation_ids": ["cit001"]},
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
            ToolCallPlan(tool="url_content", args={"url": "https://x.com"},
                         target_fact_ids=["f001"], cost=1.0),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="url_content", description="Fetch a URL"),
        ]

        result = await research(state, _make_run_config(config))

        citations = result["knowledge"]["citations"]
        assert len(citations) == 1
        assert citations[0]["id"] == "cit001"
        assert "url" not in citations[0] or citations[0].get("url") is None
        assert "Some Page" in citations[0].get("excerpt", "")

    @pytest.mark.asyncio
    async def test_tool_result_display_truncation_note(
        self, config, mock_writer, mock_model
    ):
        """When tool output exceeds the display limit, the writer event
        should include a truncation note."""
        _inject_services(config, mock_model)

        long_output = "x" * 5000
        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(tool_name="url_content",
                       output=long_output,
                       success=True, duration_ms=100),
        ])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(content=json.dumps({
                "tool_calls": [{"tool": "url_content", "args": {"url": "https://x.com"}}],
                "discovered_facts": [],
                "sources": [],
            })),
            ChatResponse(content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [
                    {"fact_id": "f001", "claim": "found it",
                     "citation_ids": ["cit001"]},
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
            ToolCallPlan(tool="url_content", args={"url": "https://x.com"},
                         target_fact_ids=["f001"], cost=1.0),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="url_content", description="Fetch a URL"),
        ]

        await research(state, _make_run_config(config))

        # Find the tool_result event in mock_writer
        tool_events = [
            e for e in mock_writer
            if isinstance(e, dict) and e.get("event") == "tool_result"
        ]
        assert len(tool_events) >= 1
        payload = tool_events[0]["payload"]
        assert "more chars not shown" in payload["output"]
        assert len(payload["output"]) < len(long_output)

    @pytest.mark.asyncio
    async def test_citation_dedup_same_url_across_rounds(
        self, config, mock_writer, mock_model
    ):
        """When the same URL appears in search results across multiple
        research rounds, snippets are merged into a single citation."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()

        async def _execute_batch(calls):
            return [
                ToolResult(
                    tool_name="web_search",
                    output="text",
                    success=True,
                    duration_ms=100,
                    metadata={"results": [
                        {"title": "Result", "url": "https://shared.com",
                         "snippet": f"snippet from query '{calls[i][1]['query']}'"},
                    ]},
                )
                for i in range(len(calls))
            ]

        mock_executor.execute_batch = AsyncMock(side_effect=_execute_batch)
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            # Round 1: search
            ChatResponse(content=json.dumps({
                "tool_calls": [{"tool": "web_search", "args": {"query": "alpha"}}],
                "discovered_facts": [],
                "sources": [],
            })),
            # Round 2: search again (different query, same URL in results)
            ChatResponse(content=json.dumps({
                "tool_calls": [{"tool": "web_search", "args": {"query": "beta"}}],
                "discovered_facts": [],
                "sources": [],
            })),
            # Round 3: done
            ChatResponse(content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [
                    {"fact_id": "f001", "claim": "found it",
                     "citation_ids": ["cit001"]},
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
            ToolCallPlan(tool="web_search", args={"query": "x"},
                         target_fact_ids=["f001"], cost=1.0),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]

        result = await research(state, _make_run_config(config))

        citations = result["knowledge"]["citations"]
        assert len(citations) == 1
        assert citations[0]["url"] == "https://shared.com"
        assert citations[0]["snippets"] == [
            "snippet from query 'alpha'",
            "snippet from query 'beta'",
        ]

    @pytest.mark.asyncio
    async def test_citation_dedup_same_url_same_batch(
        self, config, mock_writer, mock_model
    ):
        """Two web_search calls in the same batch returning the same URL
        should merge into one citation with two snippets."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[
            ToolResult(
                tool_name="web_search",
                output="text",
                success=True,
                duration_ms=100,
                metadata={"results": [
                    {"title": "Shared", "url": "https://dup.com",
                     "snippet": "snippet from first search"},
                ]},
            ),
            ToolResult(
                tool_name="web_search",
                output="text",
                success=True,
                duration_ms=100,
                metadata={"results": [
                    {"title": "Shared", "url": "https://dup.com",
                     "snippet": "snippet from second search"},
                    {"title": "Unique", "url": "https://unique.com",
                     "snippet": "only here"},
                ]},
            ),
        ])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            # Round 1: two searches
            ChatResponse(content=json.dumps({
                "tool_calls": [
                    {"tool": "web_search", "args": {"query": "a"}},
                    {"tool": "web_search", "args": {"query": "b"}},
                ],
                "discovered_facts": [],
                "sources": [],
            })),
            # Round 2: done
            ChatResponse(content=json.dumps({
                "tool_calls": [],
                "discovered_facts": [],
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
            ToolCallPlan(tool="web_search", args={"query": "x"},
                         target_fact_ids=["f001"], cost=1.0),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]

        result = await research(state, _make_run_config(config))

        citations = result["knowledge"]["citations"]
        # dedup: dup.com merged, unique.com new
        assert len(citations) == 2
        dup = [c for c in citations if c.get("url") == "https://dup.com"][0]
        assert dup["snippets"] == [
            "snippet from first search",
            "snippet from second search",
        ]

    @pytest.mark.asyncio
    async def test_apply_sources_dedup_by_url(
        self, config, mock_writer, mock_model
    ):
        """Model-emitted sources with a URL that already has a citation
        should merge instead of creating a duplicate."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}

        # First call: creates a new citation
        cit_id_1, is_new_1 = _find_or_merge_citation(
            citations, seen_urls,
            source="web_search",
            url="https://example.com",
            title="Example",
            snippet="first snippet",
        )
        assert is_new_1 is True
        assert cit_id_1 == "cit001"
        assert len(citations) == 1
        assert citations[0]["snippets"] == ["first snippet"]

        # Second call: same URL, different snippet → merge
        cit_id_2, is_new_2 = _find_or_merge_citation(
            citations, seen_urls,
            source="web_search",
            url="https://example.com",
            title="Example",
            snippet="second snippet",
        )
        assert is_new_2 is False
        assert cit_id_2 == "cit001"
        assert len(citations) == 1
        assert citations[0]["snippets"] == ["first snippet", "second snippet"]

        # Third call: new URL → new citation
        cit_id_3, is_new_3 = _find_or_merge_citation(
            citations, seen_urls,
            source="web_search",
            url="https://other.com",
            title="Other",
            snippet="other snippet",
        )
        assert is_new_3 is True
        assert cit_id_3 == "cit002"
        assert len(citations) == 2

    @pytest.mark.asyncio
    async def test_find_or_merge_no_url_always_creates_new(
        self, config, mock_writer, mock_model
    ):
        """When url is None (e.g., calculator results), each call creates
        a new citation since there's nothing to dedup on."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}

        cit_id_1, is_new_1 = _find_or_merge_citation(
            citations, seen_urls,
            source="calculator",
            snippet="42",
        )
        cit_id_2, is_new_2 = _find_or_merge_citation(
            citations, seen_urls,
            source="calculator",
            snippet="99",
        )
        assert is_new_1 is True
        assert is_new_2 is True
        assert cit_id_1 == "cit001"
        assert cit_id_2 == "cit002"
        assert len(citations) == 2

    # ---- Overlap-aware snippet merge tests ----

    def test_try_merge_snippets_suffix_prefix(self):
        """A's suffix overlaps B's prefix → merged into one string."""
        from moira.workflow.nodes.research import _try_merge_snippets

        a = "The recipe uses cherries and sugar for maceration"
        b = "cherries and sugar for maceration over one month"
        result = _try_merge_snippets(a, b)
        assert result == "The recipe uses cherries and sugar for maceration over one month"

    def test_try_merge_snippets_reverse_direction(self):
        """B's suffix overlaps A's prefix → merged (reverse direction)."""
        from moira.workflow.nodes.research import _try_merge_snippets

        a = "cherries and sugar for maceration over one month"
        b = "The recipe uses cherries and sugar for maceration"
        result = _try_merge_snippets(a, b)
        assert result == "The recipe uses cherries and sugar for maceration over one month"

    def test_try_merge_snippets_no_overlap(self):
        """No meaningful overlap → None."""
        from moira.workflow.nodes.research import _try_merge_snippets

        result = _try_merge_snippets(
            "Cherries are harvested in June",
            "Bottling happens in December",
        )
        assert result is None

    def test_try_merge_snippets_substring_is_handled(self):
        """When one is a token-level substring of the other, overlap merge
        finds the full overlap and produces the longer string."""
        from moira.workflow.nodes.research import _try_merge_snippets

        a = "Need cherries sugar brandy"
        b = "Need cherries sugar brandy cinnamon cloves"
        result = _try_merge_snippets(a, b)
        assert result == "Need cherries sugar brandy cinnamon cloves"

    def test_try_merge_snippets_case_insensitive(self):
        """Overlap detection should be case-insensitive."""
        from moira.workflow.nodes.research import _try_merge_snippets

        a = "Add the Sugar and stir well"
        b = "and stir well until dissolved"
        result = _try_merge_snippets(a, b)
        assert result is not None
        assert "dissolved" in result

    def test_try_merge_snippets_min_words_threshold(self):
        """Overlap below min_words threshold should not merge."""
        from moira.workflow.nodes.research import _try_merge_snippets

        # Only 2 words overlap, default min is 3
        result = _try_merge_snippets(
            "the end",
            "the beginning",
            min_words=3,
        )
        assert result is None

    def test_dedup_substring_keeps_longer(self):
        """When new snippet is substring of existing, existing is kept."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}

        # First: long snippet
        _find_or_merge_citation(
            citations, seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Need cherries sugar brandy cinnamon cloves",
        )
        # Second: shorter version of same content
        cit_id, is_new = _find_or_merge_citation(
            citations, seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Need cherries sugar",
        )
        assert is_new is False
        assert len(citations) == 1
        assert len(citations[0]["snippets"]) == 1
        assert citations[0]["snippets"][0] == "Need cherries sugar brandy cinnamon cloves"

    def test_dedup_existing_substring_replaced_by_longer(self):
        """When existing snippet is substring of new, existing is replaced."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}

        # First: short snippet
        _find_or_merge_citation(
            citations, seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Need cherries sugar",
        )
        # Second: longer version
        _find_or_merge_citation(
            citations, seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Need cherries sugar brandy cinnamon cloves",
        )
        assert len(citations) == 1
        assert len(citations[0]["snippets"]) == 1
        assert citations[0]["snippets"][0] == "Need cherries sugar brandy cinnamon cloves"

    def test_dedup_overlap_merges_two_snippets(self):
        """Suffix-prefix overlap → two snippets merged into one."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}

        _find_or_merge_citation(
            citations, seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="The recipe uses cherries and sugar for maceration",
        )
        _find_or_merge_citation(
            citations, seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="cherries and sugar for maceration over one month",
        )
        assert len(citations) == 1
        assert len(citations[0]["snippets"]) == 1
        assert citations[0]["snippets"][0] == (
            "The recipe uses cherries and sugar for maceration over one month"
        )

    def test_dedup_no_overlap_keeps_both(self):
        """Genuinely different snippets for same URL → both kept."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}

        _find_or_merge_citation(
            citations, seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Cherries are harvested in June",
        )
        _find_or_merge_citation(
            citations, seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Bottling happens in December",
        )
        assert len(citations) == 1
        assert len(citations[0]["snippets"]) == 2

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
# RESEARCH REVIEW
# ===========================================================================


class TestResearchReview:

    @pytest.mark.asyncio
    async def test_happy_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=REVIEW_RESPONSE
        )

        from moira.workflow.nodes.research_review import research_review

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

        result = await research_review(state, _make_run_config(config))

        assert result["knowledge"]["facts"][0]["status"] == "verified"
        assert result["knowledge"]["facts"][0]["verification_note"] == "Confirmed by source"

        history = result["knowledge"]["review_history"]
        assert len(history) == 1
        assert history[0]["route"] == "continue"

        step_cost = config.budget.cost_weights.research_review
        assert result["execution_state"]["budget_remaining"] == (
            config.budget.default_limit - step_cost
        )
        assert result["execution_state"]["review_count"] == 1

    @pytest.mark.asyncio
    async def test_budget_exhaustion(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["execution_state"]["budget_remaining"] = 0.0

        result = await research_review(state, _make_run_config(config))

        assert "error" in result["execution_state"]
        assert "Insufficient budget" in result["execution_state"]["error"]

    @pytest.mark.asyncio
    async def test_model_failure(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = RuntimeError("Model error")

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]

        with pytest.raises(RuntimeError, match="Model error"):
            await research_review(state, _make_run_config(config))

    @pytest.mark.asyncio
    async def test_contradicted_facts(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=REVIEW_RETRY_RESPONSE
        )

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]

        result = await research_review(state, _make_run_config(config))

        assert result["knowledge"]["facts"][0]["status"] == "contradicted"
        assert result["knowledge"]["review_history"][0]["route"] == "retry"
        assert len(result["knowledge"]["review_history"][0]["missing_areas"]) == 1

    @pytest.mark.asyncio
    async def test_increments_review_count(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=REVIEW_RESPONSE
        )

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]
        state["execution_state"]["review_count"] = 2

        result = await research_review(state, _make_run_config(config))
        assert result["execution_state"]["review_count"] == 3

    @pytest.mark.asyncio
    async def test_parse_failure_raises(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content="This is not JSON at all"
        )

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]

        with pytest.raises(RuntimeError, match="unparseable JSON"):
            await research_review(state, _make_run_config(config))

    @pytest.mark.asyncio
    async def test_structured_output_has_review_schema(
        self, config, mock_writer, mock_model
    ):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=REVIEW_RESPONSE
        )

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]

        await research_review(state, _make_run_config(config))

        node_end_events = [
            e for e in mock_writer
            if isinstance(e, dict) and e.get("event") == "node_end"
        ]
        assert len(node_end_events) == 1
        so = node_end_events[0]["payload"]["detail"]["structured_output"]
        assert "fact_results" in so
        assert "coverage_assessment" in so
        assert "missing_areas" in so
        assert "route" in so

    @pytest.mark.asyncio
    async def test_model_uses_status_key_instead_of_result(
        self, config, mock_writer, mock_model
    ):
        """Models sometimes return 'status' instead of the documented 'result'
        field name. The node should handle this gracefully — the verdict is
        critical for routing (regression test for field name mismatch bug)."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({
                "fact_results": [
                    {"fact_id": "f001", "status": "verified",
                     "evaluation": "Confirmed by source"},
                ],
                "coverage_assessment": "All good",
                "missing_areas": [],
                "route": "continue",
            })
        )

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y",
                 claim="z", status="unverified"),
        ]

        result = await research_review(state, _make_run_config(config))

        assert result["knowledge"]["facts"][0]["status"] == "verified"


# ===========================================================================
# EVALUATION
# ===========================================================================


class TestEvaluation:

    @pytest.mark.asyncio
    async def test_happy_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=EVALUATION_RESPONSE
        )

        from moira.workflow.nodes.evaluation import evaluation

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y",
                 claim="z", status="verified"),
        ]
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001", conclusion="Entity1 is confirmed",
                supporting_fact_ids=["f001"], status="unverified",
            ),
        ]

        result = await evaluation(state, _make_run_config(config))

        assert result["knowledge"]["conclusions"][0]["status"] == "verified"

        history = result["knowledge"]["evaluation_history"]
        assert len(history) == 1
        assert history[0]["goal_met"] is True
        assert history[0]["route"] == "accept"

        step_cost = config.budget.cost_weights.evaluation
        assert result["execution_state"]["budget_remaining"] == (
            config.budget.default_limit - step_cost
        )
        assert result["execution_state"]["evaluation_count"] == 1

    @pytest.mark.asyncio
    async def test_budget_exhaustion(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        from moira.workflow.nodes.evaluation import evaluation

        state = _build_state(config, "Test question")
        state["execution_state"]["budget_remaining"] = 0.0

        result = await evaluation(state, _make_run_config(config))

        assert "error" in result["execution_state"]
        assert "Insufficient budget" in result["execution_state"]["error"]

    @pytest.mark.asyncio
    async def test_model_failure(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = RuntimeError("Model error")

        from moira.workflow.nodes.evaluation import evaluation

        state = _build_state(config, "Test question")

        with pytest.raises(RuntimeError, match="Model error"):
            await evaluation(state, _make_run_config(config))

    @pytest.mark.asyncio
    async def test_retry_route_resets_review_count(
        self, config, mock_writer, mock_model
    ):
        """When evaluation route is 'retry', review_count should be reset to 0."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=EVALUATION_RETRY_RESPONSE
        )

        from moira.workflow.nodes.evaluation import evaluation

        state = _build_state(config, "Test question")
        state["knowledge"]["conclusions"] = [
            Conclusion(id="c001", conclusion="Something",
                       supporting_fact_ids=["f001"], status="unverified"),
        ]
        state["execution_state"]["review_count"] = 2

        result = await evaluation(state, _make_run_config(config))

        assert result["knowledge"]["evaluation_history"][0]["route"] == "retry"
        assert result["execution_state"]["review_count"] == 0

    @pytest.mark.asyncio
    async def test_accept_route_preserves_review_count(
        self, config, mock_writer, mock_model
    ):
        """When evaluation route is 'accept', review_count should NOT be reset."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=EVALUATION_RESPONSE
        )

        from moira.workflow.nodes.evaluation import evaluation

        state = _build_state(config, "Test question")
        state["knowledge"]["conclusions"] = [
            Conclusion(id="c001", conclusion="Something",
                       supporting_fact_ids=["f001"], status="unverified"),
        ]
        state["execution_state"]["review_count"] = 2

        result = await evaluation(state, _make_run_config(config))

        assert result["execution_state"]["review_count"] == 2

    @pytest.mark.asyncio
    async def test_parse_failure_raises(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content="not json"
        )

        from moira.workflow.nodes.evaluation import evaluation

        state = _build_state(config, "Test question")
        state["knowledge"]["conclusions"] = [
            Conclusion(id="c001", conclusion="X",
                       supporting_fact_ids=["f001"], status="unverified"),
        ]

        with pytest.raises(RuntimeError, match="unparseable JSON"):
            await evaluation(state, _make_run_config(config))


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
        state["knowledge"]["citations"] = [
            {"id": "cit001", "source": "web_search",
             "url": "https://example.com/result1",
             "title": "First Result", "excerpt": "snippet A"},
        ]
        state["knowledge"]["evaluation_history"] = [
            {"conclusion_results": [], "goal_met": True,
             "goal_assessment": "Done", "route": "accept"},
        ]

        result = await report_generation(state, _make_run_config(config))

        assert result["knowledge"]["report"] is not None
        assert "[1]" in result["knowledge"]["report"]["answer"]
        assert result["knowledge"]["report"]["generation_path"] == "verified"
        assert result["knowledge"]["generation_path"] == "verified"

        # Report citations come from the knowledge model, not model JSON output.
        # [1] in the answer references the first citation, so it's "cited".
        report_citations = result["knowledge"]["report"]["citations"]
        assert len(report_citations) == 1
        assert report_citations[0]["url"] == "https://example.com/result1"
        assert report_citations[0]["title"] == "First Result"
        assert result["knowledge"]["report"]["uncited_sources"] == []

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
    async def test_model_call_failure_raises_error(self, config, mock_writer, mock_model):
        """When the model call itself fails (network error, crash),
        the node must raise RuntimeError and emit run_error — not
        silently produce a fallback report."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = RuntimeError("Model crashed")

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"

        with pytest.raises(RuntimeError, match="Model crashed"):
            await report_generation(state, _make_run_config(config))

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
        state["knowledge"]["evaluation_history"] = [
            {"conclusion_results": [], "goal_met": False,
             "goal_assessment": "Incomplete", "route": "retry"},
        ]

        result = await report_generation(state, _make_run_config(config))
        assert result["knowledge"]["generation_path"] == "retry_overruled"

    @pytest.mark.asyncio
    async def test_no_evaluation_history_is_budget_exhausted(
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
        state["knowledge"]["evaluation_history"] = [
            {"conclusion_results": [], "goal_met": True,
             "goal_assessment": "Done", "route": "accept"},
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

    @pytest.mark.asyncio
    async def test_citation_pruning_and_renumbering(
        self, config, mock_writer, mock_model,
    ):
        """Citations not referenced in the answer are moved to
        uncited_sources.  Referenced citations are renumbered sequentially."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({
                "answer": "Result from [1] and also [3].",
                "citations": [],
                "verified_facts": [],
                "verified_conclusions": [],
                "contradicted": [],
                "unknown_facts": [],
                "critiques": [],
            })
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["citations"] = [
            {"id": "cit001", "source": "web_search",
             "url": "https://a.com", "title": "A", "excerpt": "aa"},
            {"id": "cit002", "source": "web_search",
             "url": "https://b.com", "title": "B", "excerpt": "bb"},
            {"id": "cit003", "source": "web_search",
             "url": "https://c.com", "title": "C", "excerpt": "cc"},
            {"id": "cit004", "source": "web_search",
             "url": "https://d.com", "title": "D", "excerpt": "dd"},
        ]

        result = await report_generation(state, _make_run_config(config))
        report = result["knowledge"]["report"]

        # [1] and [3] cited → renumbered to [1] and [2]
        assert "[1]" in report["answer"]
        assert "[2]" in report["answer"]
        assert "[3]" not in report["answer"]

        assert len(report["citations"]) == 2
        assert report["citations"][0]["url"] == "https://a.com"
        assert report["citations"][1]["url"] == "https://c.com"

        assert len(report["uncited_sources"]) == 2
        assert report["uncited_sources"][0]["url"] == "https://b.com"
        assert report["uncited_sources"][1]["url"] == "https://d.com"

    @pytest.mark.asyncio
    async def test_no_citation_markers_all_uncited(
        self, config, mock_writer, mock_model,
    ):
        """When the model doesn't use [n] markers, all citations
        go to uncited_sources."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({
                "answer": "Just a plain answer with no markers.",
                "citations": [],
                "verified_facts": [],
                "verified_conclusions": [],
                "contradicted": [],
                "unknown_facts": [],
                "critiques": [],
            })
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["citations"] = [
            {"id": "cit001", "source": "web_search",
             "url": "https://a.com", "title": "A", "excerpt": "aa"},
            {"id": "cit002", "source": "web_search",
             "url": "https://b.com", "title": "B", "excerpt": "bb"},
        ]

        result = await report_generation(state, _make_run_config(config))
        report = result["knowledge"]["report"]

        assert report["citations"] == []
        assert len(report["uncited_sources"]) == 2

    @pytest.mark.asyncio
    async def test_parse_failure_raises_error(self, config, mock_writer, mock_model):
        """When the model returns content that can't be parsed as JSON,
        the node must raise RuntimeError and emit run_error — not
        silently produce an empty report."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content="This is not JSON at all.",
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["citations"] = [
            {"id": "cit001", "source": "web_search",
             "url": "https://a.com", "title": "A", "excerpt": "aa"},
        ]

        with pytest.raises(RuntimeError, match="unparseable JSON"):
            await report_generation(state, _make_run_config(config))

    @pytest.mark.asyncio
    async def test_empty_response_raises_error(self, config, mock_writer, mock_model):
        """When the model returns empty content, the node must raise."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content="",
            thinking="I was thinking but produced nothing.",
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")

        with pytest.raises(RuntimeError, match="empty content"):
            await report_generation(state, _make_run_config(config))

    @pytest.mark.asyncio
    async def test_literal_newlines_in_json_are_repaired(
        self, config, mock_writer, mock_model,
    ):
        """When a quantized model emits literal newlines inside JSON string
        values, _fix_json_control_chars should repair them so parsing
        succeeds and the report is generated normally."""
        _inject_services(config, mock_model)
        # Build a response with a LITERAL newline inside the answer string.
        # This is what quantized models sometimes produce.
        raw = '{"answer": "line1\nline2 [1]", "critiques": ["c1"]}'
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=raw,
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["citations"] = [
            {"id": "cit001", "source": "web_search",
             "url": "https://a.com", "title": "A", "excerpt": "aa"},
        ]

        result = await report_generation(state, _make_run_config(config))
        report = result["knowledge"]["report"]

        assert "line1" in report["answer"]
        assert "line2" in report["answer"]
        assert report["critiques"] == ["c1"]


# ---------------------------------------------------------------------------
# _fix_json_control_chars unit tests
# ---------------------------------------------------------------------------

class TestFixJsonControlChars:
    """Unit tests for the literal-control-character repair utility."""

    def test_noop_on_valid_json(self):
        """Already-valid JSON should pass through unchanged."""
        from moira.workflow.nodes._helpers import _fix_json_control_chars

        valid = '{"answer": "hello\\nworld", "x": 1}'
        assert _fix_json_control_chars(valid) == valid

    def test_noop_on_json_without_strings(self):
        """JSON with no string values should pass through unchanged."""
        from moira.workflow.nodes._helpers import _fix_json_control_chars

        valid = '{"x": 1, "y": [1, 2, 3]}'
        assert _fix_json_control_chars(valid) == valid

    def test_repairs_literal_newline_in_string(self):
        """Literal newline inside a JSON string value should be escaped."""
        from moira.workflow.nodes._helpers import _fix_json_control_chars

        broken = '{"answer": "line1\nline2"}'
        fixed = _fix_json_control_chars(broken)
        assert fixed == '{"answer": "line1\\nline2"}'

    def test_preserves_structural_newlines(self):
        """Newlines between JSON tokens (outside strings) should be
        preserved as-is — they are valid JSON whitespace."""
        from moira.workflow.nodes._helpers import _fix_json_control_chars

        text = '{\n  "answer": "ok"\n}'
        assert _fix_json_control_chars(text) == text

    def test_repairs_literal_tab_in_string(self):
        """Literal tab inside a JSON string value should be escaped."""
        from moira.workflow.nodes._helpers import _fix_json_control_chars

        broken = '{"a": "col1\tcol2"}'
        fixed = _fix_json_control_chars(broken)
        assert fixed == '{"a": "col1\\tcol2"}'

    def test_handles_escaped_quotes(self):
        """Escaped quotes (\\") should not toggle the in-string state."""
        from moira.workflow.nodes._helpers import _fix_json_control_chars

        # The \" inside the string should not end the string, so the
        # literal newline after it should be escaped.
        broken = '{"a": "say \\"hi\\" then\nmore"}'
        fixed = _fix_json_control_chars(broken)
        assert "\\n" in fixed
        assert "\n" not in fixed.split('"')[3]  # inside the string value

    def test_parse_succeeds_after_repair(self):
        """End-to-end: json.loads should succeed after repair."""
        import json

        from moira.workflow.nodes._helpers import _fix_json_control_chars

        broken = '{"answer": "line1\nline2", "x": 1}'
        # Direct json.loads should fail
        with pytest.raises(json.JSONDecodeError):
            json.loads(broken)
        # After repair, it should succeed
        fixed = _fix_json_control_chars(broken)
        result = json.loads(fixed)
        assert result["answer"] == "line1\nline2"
        assert result["x"] == 1


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
