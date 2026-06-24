"""Tests for the synthesis node."""

import json

import pytest
from _node_test_helpers import (
    SYNTHESIS_RESPONSE,
    _build_state,
    _inject_services,
    _make_run_config,
)

from moira.inference.client import ChatResponse
from moira.models.knowledge import Fact


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
                id="f001",
                subject="entity1",
                fact_needed="some fact",
                claim="Entity1 confirmed",
                status="unverified",
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
            content=json.dumps(
                {
                    "conclusions": [
                        {
                            "conclusion": "First",
                            "supporting_fact_ids": ["f001"],
                            "reasoning": "r1",
                        },
                        {
                            "conclusion": "Second",
                            "supporting_fact_ids": ["f002"],
                            "reasoning": "r2",
                        },
                    ],
                }
            )
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
