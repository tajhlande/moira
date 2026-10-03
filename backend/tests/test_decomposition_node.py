"""Tests for the decomposition node."""

import json

import pytest
from _node_test_helpers import (
    DECOMPOSITION_RESPONSE,
    _build_state,
    _inject_services,
    _make_run_config,
)

from moira.inference.client import ChatResponse


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
    async def test_malformed_json_retries_then_raises(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        # First call plus two corrective retries all unparseable.
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(content="This is not JSON at all"),
            ChatResponse(content="still not json"),
            ChatResponse(content="nope"),
        ]

        from moira.workflow.nodes.decomposition import decomposition

        state = _build_state(config, "Tell me about test topic")

        with pytest.raises(RuntimeError, match="could not be parsed after 3 attempts"):
            await decomposition(state, _make_run_config(config))

        assert mock_model["client"].chat_completion.await_count == 3
        # The failure surfaces as a run_error event, not a silent empty run.
        error_events = [e for e in mock_writer if e["event"] == "run_error"]
        assert len(error_events) == 1
        assert "could not be parsed" in error_events[0]["payload"]["error"]
        payload = error_events[0]["payload"]
        assert payload["call_count"] == 3
        assert payload["detail"]["parse_failure_raw"] == "This is not JSON at all"

    @pytest.mark.asyncio
    async def test_parse_retry_recovers(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        # First response defeats the parser; the corrective retry returns
        # the standard payload.
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(content="broken { not json"),
            ChatResponse(content=DECOMPOSITION_RESPONSE),
        ]

        from moira.workflow.nodes.decomposition import decomposition

        state = _build_state(config, "Tell me about test topic")
        result = await decomposition(state, _make_run_config(config))

        assert mock_model["client"].chat_completion.await_count == 2
        assert result["knowledge"]["user_goal"] == "Find information about test topic"
        assert len(result["knowledge"]["facts"]) == 2
        # The recovered run records what defeated the parser for diagnosis.
        end_events = [e for e in mock_writer if e["event"] == "node_end"]
        assert len(end_events) == 1
        payload = end_events[0]["payload"]
        assert payload["call_count"] == 2
        assert payload["detail"]["parse_failure_raw"] == "broken { not json"
        assert payload["detail"]["response"] == DECOMPOSITION_RESPONSE

    @pytest.mark.asyncio
    async def test_empty_unknown_facts(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "user_goal": "Vague question",
                    "topic": "general",
                    "entities": [],
                    "concepts": [],
                    "unknown_facts": [],
                }
            )
        )

        from moira.workflow.nodes.decomposition import decomposition

        state = _build_state(config, "Hello")
        result = await decomposition(state, _make_run_config(config))

        assert result["knowledge"]["facts"] == []

    @pytest.mark.asyncio
    async def test_facts_get_sequential_ids(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "user_goal": "g",
                    "topic": "t",
                    "entities": [],
                    "concepts": [],
                    "unknown_facts": [
                        {"subject": "a", "fact_needed": "x"},
                        {"subject": "b", "fact_needed": "y"},
                        {"subject": "c", "fact_needed": "z"},
                    ],
                }
            )
        )

        from moira.workflow.nodes.decomposition import decomposition

        state = _build_state(config, "q")
        result = await decomposition(state, _make_run_config(config))

        ids = [f["id"] for f in result["knowledge"]["facts"]]
        assert ids == ["f001", "f002", "f003"]

    async def test_facts_marked_decomposition_origin(self, config, mock_writer, mock_model):
        """Decomposition-produced facts carry origin="decomposition" — the
        baseline provenance the retrieval harness measures research
        agency against."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "user_goal": "g",
                    "topic": "t",
                    "entities": [],
                    "concepts": [],
                    "unknown_facts": [{"subject": "a", "fact_needed": "x"}],
                }
            )
        )

        from moira.workflow.nodes.decomposition import decomposition

        state = _build_state(config, "q")
        result = await decomposition(state, _make_run_config(config))

        assert [f["origin"] for f in result["knowledge"]["facts"]] == ["decomposition"]
