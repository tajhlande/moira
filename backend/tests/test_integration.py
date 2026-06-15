import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moira.config import MoiraConfig
from moira.inference.client import ChatResponse, InferenceClient
from moira.inference.registry import ModelRegistry, ResolvedModel
from moira.service_setup import _services
from moira.tools.base import ToolDefinition, ToolResult
from moira.workflow.graph import compile_graph

DECOMPOSITION_JSON = json.dumps({
    "user_goal": "Find the capital of France",
    "topic": "Geography",
    "entities": ["France"],
    "concepts": ["capital cities"],
    "unknown_facts": [{"subject": "France", "fact_needed": "Current capital city of France"}],
})

PLANNING_JSON = json.dumps({
    "calls": [
        {
            "tool": "web_search",
            "args": {"query": "capital of France"},
            "target_fact_ids": ["f001"],
            "rationale": "Search for the capital of France",
        }
    ],
})

RESEARCH_JSON = json.dumps({
    "tool_calls": [],
    "discovered_facts": [{"fact_id": "f001", "claim": "Paris is the capital of France"}],
    "sources": [],
})

SYNTHESIS_JSON = json.dumps({
    "conclusions": [
        {
            "conclusion": "Paris is the capital of France",
            "supporting_fact_ids": ["f001"],
            "reasoning": "Research confirms Paris is the capital",
        }
    ],
})

VERIFICATION_ACCEPT_JSON = json.dumps({
    "fact_results": [{"fact_id": "f001", "result": "verified", "evidence": "Confirmed"}],
    "conclusion_results": [{"conclusion_id": "c001", "result": "verified", "reason": "Supported"}],
    "new_unknown_facts": [],
    "goal_met": True,
    "goal_assessment": "Goal met",
    "route": "accept",
})

VERIFICATION_RETRY_RESEARCH_JSON = json.dumps({
    "fact_results": [
        {"fact_id": "f001", "result": "unverified", "evidence": "Needs more"}
    ],
    "conclusion_results": [],
    "new_unknown_facts": [],
    "goal_met": False,
    "goal_assessment": "More research needed",
    "route": "retry_research",
})

VERIFICATION_RETRY_SYNTHESIS_JSON = json.dumps({
    "fact_results": [{"fact_id": "f001", "result": "verified", "evidence": "Confirmed"}],
    "conclusion_results": [
        {"conclusion_id": "c001", "result": "unverified", "reason": "Weak"}
    ],
    "new_unknown_facts": [],
    "goal_met": False,
    "goal_assessment": "Synthesis needs improvement",
    "route": "retry_synthesis",
})

REPORT_JSON = json.dumps({
    "answer": "Paris is the capital of France.",
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


def _cr(content):
    return ChatResponse(content=content)


def _build_state(config, budget=None):
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
    limit = float(budget if budget is not None else config.budget.default_limit)
    return {
        "knowledge": {
            "question": "What is the capital of France?",
            "user_goal": "",
            "topic": "",
            "entities": [],
            "concepts": [],
            "facts": [],
            "conclusions": [],
            "citations": [],
            "verification_history": [],
        },
        "execution_state": {
            "candidate_tools": [],
            "tool_call_plan": [],
            "budget_remaining": limit,
            "budget_limit": limit,
            "step_costs": step_costs,
            "tool_costs": {"web_search": 5.0},
            "tool_call_counts": {},
            "total_tool_cost_consumed": 0.0,
            "error": "",
            "synthesis_retry_count": 0,
            "research_retry_count": 0,
            "verification_attempts": 0,
        },
    }


def _make_run_config(config):
    return {"configurable": {"moira_config": config}}


def _inject_services(config, mock_model):
    _services.clear()
    _services["config"] = config
    _services["model_registry"] = mock_model["registry"]

    mock_discovery = AsyncMock()
    mock_discovery.discover = AsyncMock(
        return_value=[ToolDefinition(name="web_search", description="Search the web")]
    )
    _services["tool_discovery"] = mock_discovery

    mock_catalog = MagicMock()
    mock_catalog.get_default_tools.return_value = [
        ToolDefinition(name="calculator", description="Calculator", is_default=True),
    ]
    _services["tool_catalog"] = mock_catalog

    mock_executor = AsyncMock()
    mock_executor.execute_batch = AsyncMock(
        return_value=[
            ToolResult(
                tool_name="web_search",
                output="Paris is the capital of France",
                success=True,
                duration_ms=100,
            )
        ]
    )
    _services["tool_executor"] = mock_executor


_STREAM_WRITER_MODULES = [
    "moira.workflow.nodes.decomposition",
    "moira.workflow.nodes.tool_identification",
    "moira.workflow.nodes.planning",
    "moira.workflow.nodes.research",
    "moira.workflow.nodes.synthesis",
    "moira.workflow.nodes.verification",
    "moira.workflow.nodes.report_generation",
    "moira.workflow.nodes._helpers",
]


@pytest.fixture
def config():
    return MoiraConfig()


@pytest.fixture
def mock_writer():
    events = []

    def write(event):
        events.append(event)

    with ExitStack() as stack:
        for mod in _STREAM_WRITER_MODULES:
            stack.enter_context(patch(f"{mod}.get_stream_writer", return_value=write))
        yield events


@pytest.fixture
def mock_model():
    client = AsyncMock(spec=InferenceClient)
    resolved = ResolvedModel(model_id="test-model", client=client)
    registry = MagicMock(spec=ModelRegistry)
    registry.resolve = AsyncMock(return_value=resolved)
    return {"client": client, "resolved": resolved, "registry": registry}


class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_cycle_verified_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = [
            _cr(DECOMPOSITION_JSON),
            _cr(PLANNING_JSON),
            _cr(RESEARCH_JSON),
            _cr(SYNTHESIS_JSON),
            _cr(VERIFICATION_ACCEPT_JSON),
            _cr(REPORT_JSON),
        ]

        state = _build_state(config)
        graph = compile_graph(config)
        result = await graph.ainvoke(state, config=_make_run_config(config))

        report = result["knowledge"]["report"]
        assert report is not None
        assert report["generation_path"] == "verified"
        assert report["answer"] != ""
        initial = state["execution_state"]["budget_remaining"]
        assert result["execution_state"]["budget_remaining"] < initial

    @pytest.mark.asyncio
    async def test_full_cycle_budget_exhausted(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = [
            _cr(DECOMPOSITION_JSON),
            _cr(PLANNING_JSON),
            _cr(RESEARCH_JSON),
            _cr(SYNTHESIS_JSON),
            _cr(VERIFICATION_RETRY_RESEARCH_JSON),
            _cr(REPORT_JSON),
        ]

        state = _build_state(config, budget=35)
        graph = compile_graph(config)
        result = await graph.ainvoke(state, config=_make_run_config(config))

        report = result["knowledge"]["report"]
        assert report is not None
        assert report["generation_path"] == "retry_overruled"

    @pytest.mark.asyncio
    async def test_full_cycle_error_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = [
            _cr(REPORT_JSON),
        ]

        state = _build_state(config, budget=0)
        graph = compile_graph(config)
        result = await graph.ainvoke(state, config=_make_run_config(config))

        report = result["knowledge"]["report"]
        assert report is not None
        assert report["generation_path"] == "error"

    @pytest.mark.asyncio
    async def test_retry_synthesis_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = [
            _cr(DECOMPOSITION_JSON),
            _cr(PLANNING_JSON),
            _cr(RESEARCH_JSON),
            _cr(SYNTHESIS_JSON),
            _cr(VERIFICATION_RETRY_SYNTHESIS_JSON),
            _cr(SYNTHESIS_JSON),
            _cr(VERIFICATION_ACCEPT_JSON),
            _cr(REPORT_JSON),
        ]

        state = _build_state(config)
        graph = compile_graph(config)
        result = await graph.ainvoke(state, config=_make_run_config(config))

        report = result["knowledge"]["report"]
        assert report is not None
        assert report["generation_path"] == "verified"
        assert result["execution_state"]["verification_attempts"] >= 2
        assert len(result["knowledge"]["verification_history"]) >= 2

    @pytest.mark.asyncio
    async def test_retry_research_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = [
            _cr(DECOMPOSITION_JSON),
            _cr(PLANNING_JSON),
            _cr(RESEARCH_JSON),
            _cr(SYNTHESIS_JSON),
            _cr(VERIFICATION_RETRY_RESEARCH_JSON),
            _cr(PLANNING_JSON),
            _cr(RESEARCH_JSON),
            _cr(SYNTHESIS_JSON),
            _cr(VERIFICATION_ACCEPT_JSON),
            _cr(REPORT_JSON),
        ]

        state = _build_state(config, budget=80)
        graph = compile_graph(config)
        result = await graph.ainvoke(state, config=_make_run_config(config))

        report = result["knowledge"]["report"]
        assert report is not None
        assert report["generation_path"] == "verified"
        assert len(result["knowledge"]["verification_history"]) >= 2
