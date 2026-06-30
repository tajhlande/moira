"""Tests for the research review node."""

import json

import pytest
from _node_test_helpers import (
    REVIEW_RESPONSE,
    REVIEW_RETRY_RESPONSE,
    _build_state,
    _inject_services,
    _make_run_config,
)

from moira.inference.client import ChatResponse
from moira.models.knowledge import Conclusion, Fact


class TestResearchReview:
    @pytest.mark.asyncio
    async def test_happy_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REVIEW_RESPONSE)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
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
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001",
                conclusion="Entity1 is confirmed",
                supporting_fact_ids=["f001"],
                status="unverified",
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
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REVIEW_RESPONSE)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]
        state["execution_state"]["review_count"] = 2

        result = await research_review(state, _make_run_config(config))
        assert result["execution_state"]["review_count"] == 3

    @pytest.mark.asyncio
    async def test_parse_failure_raises_after_retry(
        self, config, mock_writer, mock_model
    ):
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
    async def test_structured_output_has_review_schema(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REVIEW_RESPONSE)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]

        await research_review(state, _make_run_config(config))

        node_end_events = [
            e for e in mock_writer if isinstance(e, dict) and e.get("event") == "node_end"
        ]
        assert len(node_end_events) == 1
        so = node_end_events[0]["payload"]["detail"]["structured_output"]
        assert "fact_results" in so
        assert "coverage_assessment" in so
        assert "missing_areas" in so
        assert "route" in so

    @pytest.mark.asyncio
    async def test_model_uses_status_key_instead_of_result(self, config, mock_writer, mock_model):
        """Models sometimes return 'status' instead of the documented 'result'
        field name. The node should handle this gracefully — the verdict is
        critical for routing (regression test for field name mismatch bug)."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "fact_results": [
                        {
                            "fact_id": "f001",
                            "status": "verified",
                            "evaluation": "Confirmed by source",
                        },
                    ],
                    "coverage_assessment": "All good",
                    "missing_areas": [],
                    "route": "continue",
                }
            )
        )

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]

        result = await research_review(state, _make_run_config(config))

        assert result["knowledge"]["facts"][0]["status"] == "verified"
