"""Tests for the report generation node."""

import json

import pytest
from _node_test_helpers import (
    REPORT_RESPONSE,
    REPORT_RESPONSE_NO_CITATIONS,
    _build_state,
    _inject_services,
    _make_run_config,
)

from moira.inference.client import ChatResponse
from moira.models.knowledge import Conclusion, Fact


class TestReportGeneration:
    @pytest.mark.asyncio
    async def test_happy_path_verified(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REPORT_RESPONSE)

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="verified"),
        ]
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001", conclusion="Confirmed", supporting_fact_ids=["f001"], status="verified"
            ),
        ]
        state["knowledge"]["citations"] = [
            {
                "id": "cit001",
                "source": "web_search",
                "url": "https://example.com/result1",
                "title": "First Result",
                "excerpt": "snippet A",
            },
        ]
        state["knowledge"]["evaluation_history"] = [
            {
                "conclusion_results": [],
                "goal_met": True,
                "goal_assessment": "Done",
                "route": "accept",
            },
        ]

        result = await report_generation(state, _make_run_config(config))

        assert result["knowledge"]["report"] is not None
        assert "[1]" in result["knowledge"]["report"]["answer"]
        assert result["knowledge"]["report"]["generation_reason"] == "verified"
        assert result["knowledge"]["generation_reason"] == "verified"

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
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REPORT_RESPONSE)

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["execution_state"]["budget_remaining"] = 0.0

        result = await report_generation(state, _make_run_config(config))

        assert result["knowledge"]["report"] is not None
        assert (
            "error" not in result["execution_state"]
            or result["execution_state"].get("error") == ""
        )

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
    async def test_error_generation_reason(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REPORT_RESPONSE)

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["execution_state"]["error"] = "Something went wrong"

        result = await report_generation(state, _make_run_config(config))

        assert result["knowledge"]["generation_reason"] == "error"

    @pytest.mark.asyncio
    async def test_retries_exhausted_when_evaluation_retry_limit_hit(
        self, config, mock_writer, mock_model
    ):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REPORT_RESPONSE)

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["evaluation_history"] = [
            {
                "conclusion_results": [],
                "goal_met": False,
                "goal_assessment": "Incomplete",
                "route": "retry",
            },
        ]
        # evaluation_count has hit the limit
        state["execution_state"]["evaluation_count"] = 2

        result = await report_generation(state, _make_run_config(config))
        assert result["knowledge"]["generation_reason"] == "retries_exhausted"

    @pytest.mark.asyncio
    async def test_budget_exhausted_when_evaluation_retry_but_budget_low(
        self, config, mock_writer, mock_model
    ):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REPORT_RESPONSE)

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["evaluation_history"] = [
            {
                "conclusion_results": [],
                "goal_met": False,
                "goal_assessment": "Incomplete",
                "route": "retry",
            },
        ]
        # evaluation_count below limit — reason should be budget, not retries
        state["execution_state"]["evaluation_count"] = 0

        result = await report_generation(state, _make_run_config(config))
        assert result["knowledge"]["generation_reason"] == "budget_exhausted"

    @pytest.mark.asyncio
    async def test_no_evaluation_history_is_budget_exhausted(
        self,
        config,
        mock_writer,
        mock_model,
    ):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REPORT_RESPONSE)

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")

        result = await report_generation(state, _make_run_config(config))
        assert result["knowledge"]["generation_reason"] == "budget_exhausted"

    @pytest.mark.asyncio
    async def test_goal_met_verified_even_with_contradicted_conclusion(
        self,
        config,
        mock_writer,
        mock_model,
    ):
        """goal_met=True yields "verified" even when a conclusion is
        contradicted.  The evaluator weighs individual conclusion statuses
        when setting goal_met, so a local guard here would double-count."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REPORT_RESPONSE)

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["conclusions"] = [
            Conclusion(id="c001", conclusion="A", supporting_fact_ids=[], status="contradicted"),
        ]
        state["knowledge"]["evaluation_history"] = [
            {
                "conclusion_results": [],
                "goal_met": True,
                "goal_assessment": "Done",
                "route": "accept",
            },
        ]

        result = await report_generation(state, _make_run_config(config))

        assert result["knowledge"]["generation_reason"] == "verified"

    @pytest.mark.asyncio
    async def test_omitted_conclusions_collects_non_verified(
        self,
        config,
        mock_writer,
        mock_model,
    ):
        """Conclusions with status unsupported, unverified, or contradicted
        are collected into omitted_conclusions for inspectability."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REPORT_RESPONSE)

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["conclusions"] = [
            Conclusion(id="c001", conclusion="Solid", supporting_fact_ids=[], status="verified"),
            Conclusion(id="c002", conclusion="Overclaim", supporting_fact_ids=[], status="unsupported"),
            Conclusion(id="c003", conclusion="Refuted", supporting_fact_ids=[], status="contradicted"),
            Conclusion(id="c004", conclusion="Pending", supporting_fact_ids=[], status="unverified"),
        ]
        state["knowledge"]["evaluation_history"] = [
            {
                "conclusion_results": [],
                "goal_met": True,
                "goal_assessment": "Done",
                "route": "accept",
            },
        ]

        result = await report_generation(state, _make_run_config(config))
        report = result["knowledge"]["report"]

        omitted_ids = {c["id"] for c in report["omitted_conclusions"]}
        assert omitted_ids == {"c002", "c003", "c004"}
        # Verified conclusion is NOT in omitted
        assert "c001" not in omitted_ids

    @pytest.mark.asyncio
    async def test_omitted_conclusions_empty_when_all_verified(
        self,
        config,
        mock_writer,
        mock_model,
    ):
        """When all conclusions are verified, omitted_conclusions is empty."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REPORT_RESPONSE)

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["conclusions"] = [
            Conclusion(id="c001", conclusion="A", supporting_fact_ids=[], status="verified"),
            Conclusion(id="c002", conclusion="B", supporting_fact_ids=[], status="verified"),
        ]
        state["knowledge"]["evaluation_history"] = [
            {
                "conclusion_results": [],
                "goal_met": True,
                "goal_assessment": "Done",
                "route": "accept",
            },
        ]

        result = await report_generation(state, _make_run_config(config))
        report = result["knowledge"]["report"]

        assert report["omitted_conclusions"] == []

    @pytest.mark.asyncio
    async def test_unsupported_does_not_downgrade_verified_when_goal_met(
        self,
        config,
        mock_writer,
        mock_model,
    ):
        """A single unsupported conclusion should not prevent "verified"
        report reason when goal_met is true.  The evaluator's goal_met
        already accounts for unsupported conclusions."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REPORT_RESPONSE)

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["conclusions"] = [
            Conclusion(id="c001", conclusion="Solid", supporting_fact_ids=[], status="verified"),
            Conclusion(id="c002", conclusion="Overclaim", supporting_fact_ids=[], status="unsupported"),
        ]
        state["knowledge"]["evaluation_history"] = [
            {
                "conclusion_results": [],
                "goal_met": True,
                "goal_assessment": "Verified conclusion suffices despite unsupported one",
                "route": "accept",
            },
        ]

        result = await report_generation(state, _make_run_config(config))
        report = result["knowledge"]["report"]

        assert result["knowledge"]["generation_reason"] == "verified"
        # Unsupported conclusion is surfaced in omitted_conclusions
        omitted_ids = {c["id"] for c in report["omitted_conclusions"]}
        assert "c002" in omitted_ids
        assert "c001" not in omitted_ids

    @pytest.mark.asyncio
    async def test_report_includes_tool_cost(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(content=REPORT_RESPONSE)

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["execution_state"]["total_tool_cost_consumed"] = 7.5

        result = await report_generation(state, _make_run_config(config))

        assert result["knowledge"]["report"]["tool_call_total_cost"] == 7.5

    @pytest.mark.asyncio
    async def test_citation_pruning_and_renumbering(
        self,
        config,
        mock_writer,
        mock_model,
    ):
        """Citations not referenced in the answer are moved to
        uncited_sources.  Referenced citations are renumbered sequentially."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "answer": "Result from [1] and also [3].",
                    "citations": [],
                    "verified_facts": [],
                    "verified_conclusions": [],
                    "contradicted": [],
                    "unknown_facts": [],
                    "critiques": [],
                }
            )
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["citations"] = [
            {
                "id": "cit001",
                "source": "web_search",
                "url": "https://a.com",
                "title": "A",
                "excerpt": "aa",
            },
            {
                "id": "cit002",
                "source": "web_search",
                "url": "https://b.com",
                "title": "B",
                "excerpt": "bb",
            },
            {
                "id": "cit003",
                "source": "web_search",
                "url": "https://c.com",
                "title": "C",
                "excerpt": "cc",
            },
            {
                "id": "cit004",
                "source": "web_search",
                "url": "https://d.com",
                "title": "D",
                "excerpt": "dd",
            },
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
        self,
        config,
        mock_writer,
        mock_model,
    ):
        """When the model doesn't use [n] markers, all citations
        go to uncited_sources."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "answer": "Just a plain answer with no markers.",
                    "citations": [],
                    "verified_facts": [],
                    "verified_conclusions": [],
                    "contradicted": [],
                    "unknown_facts": [],
                    "critiques": [],
                }
            )
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["citations"] = [
            {
                "id": "cit001",
                "source": "web_search",
                "url": "https://a.com",
                "title": "A",
                "excerpt": "aa",
            },
            {
                "id": "cit002",
                "source": "web_search",
                "url": "https://b.com",
                "title": "B",
                "excerpt": "bb",
            },
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
            {
                "id": "cit001",
                "source": "web_search",
                "url": "https://a.com",
                "title": "A",
                "excerpt": "aa",
            },
        ]

        with pytest.raises(RuntimeError, match="could not be parsed"):
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
        self,
        config,
        mock_writer,
        mock_model,
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
            {
                "id": "cit001",
                "source": "web_search",
                "url": "https://a.com",
                "title": "A",
                "excerpt": "aa",
            },
        ]

        result = await report_generation(state, _make_run_config(config))
        report = result["knowledge"]["report"]

        assert "line1" in report["answer"]
        assert "line2" in report["answer"]
        assert report["critiques"] == ["c1"]

    @pytest.mark.asyncio
    async def test_citation_retry_succeeds(self, config, mock_writer, mock_model):
        """When the model omits inline [n] markers, a retry should be
        attempted. If the retry includes markers, citations are linked."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(content=REPORT_RESPONSE_NO_CITATIONS),
            ChatResponse(content=REPORT_RESPONSE),
        ]

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="verified"),
        ]
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001", conclusion="Confirmed", supporting_fact_ids=["f001"], status="verified"
            ),
        ]
        state["knowledge"]["citations"] = [
            {
                "id": "cit001",
                "source": "web_search",
                "url": "https://example.com/result1",
                "title": "First Result",
                "excerpt": "snippet A",
            },
        ]
        state["knowledge"]["evaluation_history"] = [
            {"goal_met": True, "route": "accept"},
        ]

        result = await report_generation(state, _make_run_config(config))

        report = result["knowledge"]["report"]
        assert "[1]" in report["answer"]
        assert len(report["citations"]) == 1
        assert report["uncited_sources"] == []

    @pytest.mark.asyncio
    async def test_citation_retry_exhausted_accepts_with_warning(
        self, config, mock_writer, mock_model
    ):
        """When retries are exhausted with no citations, the report is
        still produced with a warning critique appended."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=REPORT_RESPONSE_NO_CITATIONS
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", claim="z", status="verified"),
        ]
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001", conclusion="Confirmed", supporting_fact_ids=["f001"], status="verified"
            ),
        ]
        state["knowledge"]["citations"] = [
            {
                "id": "cit001",
                "source": "web_search",
                "url": "https://example.com/result1",
                "title": "First Result",
                "excerpt": "snippet A",
            },
        ]
        state["knowledge"]["evaluation_history"] = [
            {"goal_met": True, "route": "accept"},
        ]

        result = await report_generation(state, _make_run_config(config))

        report = result["knowledge"]["report"]
        assert report is not None
        assert report["citations"] == []
        assert len(report["uncited_sources"]) == 1
        assert any("without inline citations" in c for c in report["critiques"])

    @pytest.mark.asyncio
    async def test_no_retry_when_no_citations_available(self, config, mock_writer, mock_model):
        """When the knowledge state has zero citations, no retry should
        be attempted even if the answer lacks [n] markers."""
        _inject_services(config, mock_model)
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=REPORT_RESPONSE_NO_CITATIONS
        )

        from moira.workflow.nodes.report_generation import report_generation

        state = _build_state(config, "Test question")
        state["knowledge"]["citations"] = []

        result = await report_generation(state, _make_run_config(config))

        report = result["knowledge"]["report"]
        assert report is not None
        assert mock_model["client"].chat_completion.call_count == 1
