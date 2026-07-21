"""Tests for the evaluation node."""

import pytest
from _node_test_helpers import (
    EVALUATION_RESPONSE,
    EVALUATION_RETRY_RESPONSE,
    _build_state,
    _inject_services,
    _make_run_config,
)

from moira.inference.client import ChatResponse
from moira.models.knowledge import Conclusion, Fact


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
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="verified"),
        ]
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001",
                conclusion="Entity1 is confirmed",
                supporting_fact_ids=["f001"],
                status="unverified",
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

        # Evaluation should NOT set error on insufficient budget — it should
        # return gracefully so report_generation uses "budget_exhausted"
        # instead of "error" as the generation reason.
        assert result["execution_state"].get("error", "") == ""
        assert len(result.get("knowledge", {}).get("evaluation_history", [])) == 0

    @pytest.mark.asyncio
    async def test_model_failure(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = RuntimeError("Model error")

        from moira.workflow.nodes.evaluation import evaluation

        state = _build_state(config, "Test question")

        with pytest.raises(RuntimeError, match="Model error"):
            await evaluation(state, _make_run_config(config))

    @pytest.mark.asyncio
    async def test_retry_route_resets_review_count(self, config, mock_writer, mock_model):
        """When evaluation route is 'retry', review_count should be reset to 0."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=EVALUATION_RETRY_RESPONSE
        )

        from moira.workflow.nodes.evaluation import evaluation

        state = _build_state(config, "Test question")
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001",
                conclusion="Something",
                supporting_fact_ids=["f001"],
                status="unverified",
            ),
        ]
        state["execution_state"]["review_count"] = 2

        result = await evaluation(state, _make_run_config(config))

        assert result["knowledge"]["evaluation_history"][0]["route"] == "retry"
        assert result["execution_state"]["review_count"] == 0

    @pytest.mark.asyncio
    async def test_accept_route_preserves_review_count(self, config, mock_writer, mock_model):
        """When evaluation route is 'accept', review_count should NOT be reset."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=EVALUATION_RESPONSE
        )

        from moira.workflow.nodes.evaluation import evaluation

        state = _build_state(config, "Test question")
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001",
                conclusion="Something",
                supporting_fact_ids=["f001"],
                status="unverified",
            ),
        ]
        state["execution_state"]["review_count"] = 2

        result = await evaluation(state, _make_run_config(config))

        assert result["execution_state"]["review_count"] == 2

    @pytest.mark.asyncio
    async def test_parse_failure_raises_after_retry(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content="not json")

        from moira.workflow.nodes.evaluation import evaluation

        state = _build_state(config, "Test question")
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001", conclusion="X", supporting_fact_ids=["f001"], status="unverified"
            ),
        ]

        with pytest.raises(RuntimeError, match="unparseable JSON"):
            await evaluation(state, _make_run_config(config))

    @pytest.mark.asyncio
    async def test_fact_gate_blocks_verified_upgrade(self, config, mock_writer, mock_model):
        """When research_review set fact_gate='blocked' on a conclusion, the
        evaluator must not upgrade it to 'verified' even if the model says so.
        The conclusion stays 'unverified' with an explanatory note."""
        import json

        _inject_services(config, mock_model)
        eval_json = json.dumps(
            {
                "conclusion_results": [
                    {
                        "conclusion_id": "c001",
                        "result": "verified",
                        "reason": "Looks good to me",
                    },
                ],
                "goal_met": True,
                "goal_assessment": "Goal met",
                "route": "accept",
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=eval_json)

        from moira.workflow.nodes.evaluation import evaluation

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001",
                conclusion="Blocked conclusion",
                supporting_fact_ids=["f001"],
                status="unverified",
                fact_gate="blocked",
            ),
        ]

        result = await evaluation(state, _make_run_config(config))
        conclusion = result["knowledge"]["conclusions"][0]
        assert conclusion["status"] == "unverified"
        assert "not yet verified" in conclusion["verification_note"]

    @pytest.mark.asyncio
    async def test_fact_gate_allows_verified_when_clear(self, config, mock_writer, mock_model):
        """When fact_gate is not 'blocked', evaluation may upgrade to 'verified'
        normally (the gate does not interfere)."""
        import json

        _inject_services(config, mock_model)
        eval_json = json.dumps(
            {
                "conclusion_results": [
                    {
                        "conclusion_id": "c001",
                        "result": "verified",
                        "reason": "Well supported",
                    },
                ],
                "goal_met": True,
                "goal_assessment": "Goal met",
                "route": "accept",
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=eval_json)

        from moira.workflow.nodes.evaluation import evaluation

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="verified"),
        ]
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001",
                conclusion="Good conclusion",
                supporting_fact_ids=["f001"],
                status="unverified",
            ),
        ]

        result = await evaluation(state, _make_run_config(config))
        conclusion = result["knowledge"]["conclusions"][0]
        assert conclusion["status"] == "verified"

    @pytest.mark.asyncio
    async def test_fact_gate_allows_non_verified_verdicts(self, config, mock_writer, mock_model):
        """The gate only blocks 'verified' upgrades. Other verdicts
        ('unsupported', 'contradicted') pass through even on gated conclusions."""
        import json

        _inject_services(config, mock_model)
        eval_json = json.dumps(
            {
                "conclusion_results": [
                    {
                        "conclusion_id": "c001",
                        "result": "unsupported",
                        "reason": "Overclaims",
                    },
                ],
                "goal_met": False,
                "goal_assessment": "Not met",
                "route": "retry",
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=eval_json)

        from moira.workflow.nodes.evaluation import evaluation

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="unverified"),
        ]
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001",
                conclusion="Overclaiming conclusion",
                supporting_fact_ids=["f001"],
                status="unverified",
                fact_gate="blocked",
            ),
        ]

        result = await evaluation(state, _make_run_config(config))
        conclusion = result["knowledge"]["conclusions"][0]
        # Gate doesn't interfere — "unsupported" is a valid downgrade.
        assert conclusion["status"] == "unsupported"
