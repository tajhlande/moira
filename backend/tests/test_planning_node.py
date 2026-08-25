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


class TestSplitBundledRequests:
    """Phase 2 of the planning-freedom plan: multi-fact evidence requests
    are split at parse time — bundled requests produced vague,
    hard-to-query evidence descriptions in the 08-24 eval."""

    def _facts(self):
        return [
            Fact(
                id="f001",
                subject="Tyranitar",
                fact_needed="Tyranitar's weaknesses",
                status="unknown",
            ),
            Fact(
                id="f002",
                subject="Tyranitar",
                fact_needed="Tyranitar's resistances",
                status="unknown",
            ),
            Fact(id="f003", subject="OU", fact_needed="OU tier list", status="unknown"),
        ]

    def test_bundle_splits_one_request_per_fact(self):
        from moira.workflow.nodes.planning import _split_bundled_requests

        requests = [
            {
                "target_fact_ids": ["f001", "f002"],
                "evidence_needed": "Tyranitar's type chart (weaknesses & resistances)",
                "candidate_tools": ["pikalytics", "web_search"],
                "fallback": True,
            }
        ]

        result = _split_bundled_requests(requests, self._facts())

        assert len(result) == 2
        for req in result:
            assert len(req["target_fact_ids"]) == 1
            assert req["candidate_tools"] == ["pikalytics", "web_search"]
            assert req["fallback"] is True
        # Per-fact evidence_needed comes from each fact's own fact_needed
        assert result[0]["target_fact_ids"] == ["f001"]
        assert result[0]["evidence_needed"] == "Tyranitar's weaknesses"
        assert result[1]["target_fact_ids"] == ["f002"]
        assert result[1]["evidence_needed"] == "Tyranitar's resistances"

    def test_single_fact_request_passes_through(self):
        from moira.workflow.nodes.planning import _split_bundled_requests

        requests = [
            {
                "target_fact_ids": ["f003"],
                "evidence_needed": "Current OU tier listings",
                "candidate_tools": ["smogon"],
                "fallback": False,
            }
        ]

        result = _split_bundled_requests(requests, self._facts())

        assert len(result) == 1
        assert result[0]["target_fact_ids"] == ["f003"]
        assert result[0]["evidence_needed"] == "Current OU tier listings"
        assert result[0]["fallback"] is False

    def test_unknown_fact_id_targets_dropped(self):
        from moira.workflow.nodes.planning import _split_bundled_requests

        requests = [
            {
                "target_fact_ids": ["f003", "f999"],
                "evidence_needed": "Tier list",
                "candidate_tools": ["web_search"],
                "fallback": True,
            },
            # All targets unknown: dropped entirely
            {
                "target_fact_ids": ["f998", "f999"],
                "evidence_needed": "Vague bundle",
                "candidate_tools": ["web_search"],
                "fallback": True,
            },
        ]

        result = _split_bundled_requests(requests, self._facts())

        assert len(result) == 1
        assert result[0]["target_fact_ids"] == ["f003"]

    def test_fact_without_fact_needed_uses_bundle_description(self):
        from moira.workflow.nodes.planning import _split_bundled_requests

        facts = [
            Fact(id="f001", subject="A", fact_needed="", status="unknown"),
            Fact(id="f002", subject="B", fact_needed="B's resistances", status="unknown"),
        ]
        requests = [
            {
                "target_fact_ids": ["f001", "f002"],
                "evidence_needed": "Type chart lookups",
                "candidate_tools": ["web_search"],
                "fallback": True,
            }
        ]

        result = _split_bundled_requests(requests, facts)

        assert len(result) == 2
        assert result[0]["evidence_needed"] == "Type chart lookups"
        assert result[1]["evidence_needed"] == "B's resistances"

    @pytest.mark.asyncio
    async def test_bundled_response_is_split_in_node_output(self, config, mock_writer, mock_model):
        """End-to-end through the planning node: a bundled model response
        comes out of the node as per-fact requests."""
        _inject_services(config, mock_model)
        bundled = json.dumps(
            {
                "evidence_requests": [
                    {
                        "target_fact_ids": ["f001", "f002"],
                        "evidence_needed": "Economic theory and empirical evidence",
                        "candidate_tools": ["web_search"],
                        "fallback": True,
                    }
                ]
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=bundled)

        from moira.workflow.nodes.planning import planning

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="A",
                fact_needed="Tariff effect on input costs",
                status="unknown",
            ),
            Fact(
                id="f002",
                subject="B",
                fact_needed="Tariff effect on employment",
                status="unknown",
            ),
        ]

        result = await planning(state, _make_run_config(config))

        plan = result["execution_state"]["evidence_requests"]
        assert len(plan) == 2
        assert plan[0]["target_fact_ids"] == ["f001"]
        assert plan[0]["evidence_needed"] == "Tariff effect on input costs"
        assert plan[1]["target_fact_ids"] == ["f002"]
        assert plan[1]["evidence_needed"] == "Tariff effect on employment"


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
