"""Tests for the planning node."""

import json

import pytest
from _node_test_helpers import (
    PLANNING_RESPONSE,
    _build_state,
    _inject_services,
    _make_run_config,
)

from moira.inference.client import ChatResponse
from moira.models.knowledge import Fact
from moira.tools.base import ToolDefinition


class TestPlanning:
    @pytest.mark.asyncio
    async def test_happy_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content=PLANNING_RESPONSE)

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

        plan = result["execution_state"]["evidence_requests"]
        assert len(plan) == 2
        assert plan[0]["candidate_tools"] == ["web_search"]
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
    async def test_empty_evidence_requests(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"evidence_requests": []})
        )

        from moira.workflow.nodes.planning import planning

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        result = await planning(state, _make_run_config(config))
        assert result["execution_state"]["evidence_requests"] == []

    @pytest.mark.asyncio
    async def test_malformed_response_gives_empty_plan(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content="no json here")

        from moira.workflow.nodes.planning import planning

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        result = await planning(state, _make_run_config(config))
        assert result["execution_state"]["evidence_requests"] == []


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
    async def test_step_limits_displayed(self, config, mock_writer, mock_model):
        """_format_tools_with_costs_and_limits should show per-step remaining."""
        from moira.workflow.nodes.planning import _format_tools_with_costs_and_limits

        tools = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]
        tool_costs = {"web_search": 5.0}
        call_counts = {"web_search": 3}
        call_limits = {"web_search": 10}
        step_limits = {"web_search": 5}
        # At planning time, baseline = current counts (step hasn't started)
        step_baseline = dict(call_counts)

        result = _format_tools_with_costs_and_limits(
            tools, tool_costs, call_counts, call_limits, step_limits, step_baseline
        )

        assert "calls remaining: 7" in result
        assert "(step: 5)" in result
