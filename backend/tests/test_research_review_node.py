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
    async def test_parse_failure_raises_after_retry(self, config, mock_writer, mock_model):
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

    @pytest.mark.asyncio
    async def test_corrected_claim_flips_contradicted_to_verified(
        self, config, mock_writer, mock_model
    ):
        """When the reviewer supplies a corrected_claim on a contradicted fact,
        the claim is applied and the fact flips to verified with corrected=True."""
        _inject_services(config, mock_model)
        review_json = json.dumps(
            {
                "fact_results": [
                    {
                        "fact_id": "f001",
                        "result": "contradicted",
                        "evidence": "Source says '1996 or 1997', not just 1997",
                        "corrected_claim": "The event occurred in 1996 or 1997",
                    }
                ],
                "coverage_assessment": "Sufficient after correction",
                "missing_areas": [],
                "route": "continue",
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=review_json)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="event",
                fact_needed="when did the event occur",
                claim="The event happened in 1997",
                status="unverified",
                citation_ids=["cit001"],
            ),
        ]

        result = await research_review(state, _make_run_config(config))

        fact = result["knowledge"]["facts"][0]
        assert fact["status"] == "verified"
        assert fact["claim"] == "The event occurred in 1996 or 1997"
        assert fact["corrected"] is True
        # Original citation_ids are preserved when no corrected_citation_ids given
        assert fact["citation_ids"] == ["cit001"]
        assert fact["verification_note"] == "Source says '1996 or 1997', not just 1997"

    @pytest.mark.asyncio
    async def test_corrected_claim_absent_keeps_contradicted(
        self, config, mock_writer, mock_model
    ):
        """When a contradicted fact has no corrected_claim, it stays
        contradicted and the corrected flag is not set."""
        _inject_services(config, mock_model)
        review_json = json.dumps(
            {
                "fact_results": [
                    {
                        "fact_id": "f001",
                        "result": "contradicted",
                        "evidence": "Source disagrees",
                    }
                ],
                "coverage_assessment": "Missing critical info",
                "missing_areas": ["Need accurate date"],
                "route": "retry",
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=review_json)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="x",
                fact_needed="y",
                claim="original claim",
                status="unverified",
            ),
        ]

        result = await research_review(state, _make_run_config(config))

        fact = result["knowledge"]["facts"][0]
        assert fact["status"] == "contradicted"
        assert fact["claim"] == "original claim"
        assert not fact.get("corrected")

    @pytest.mark.asyncio
    async def test_corrected_claim_with_corrected_citation_ids(
        self, config, mock_writer, mock_model
    ):
        """When the reviewer supplies corrected_citation_ids, the fact's
        citation_ids are updated to the corrected set."""
        _inject_services(config, mock_model)
        review_json = json.dumps(
            {
                "fact_results": [
                    {
                        "fact_id": "f001",
                        "result": "contradicted",
                        "evidence": "Better source",
                        "corrected_claim": "Corrected claim",
                        "corrected_citation_ids": ["cit002", "cit003"],
                    }
                ],
                "coverage_assessment": "ok",
                "missing_areas": [],
                "route": "continue",
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=review_json)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="x",
                fact_needed="y",
                claim="original",
                status="unverified",
                citation_ids=["cit001"],
            ),
        ]

        result = await research_review(state, _make_run_config(config))

        fact = result["knowledge"]["facts"][0]
        assert fact["status"] == "verified"
        assert fact["corrected"] is True
        assert fact["citation_ids"] == ["cit002", "cit003"]

    @pytest.mark.asyncio
    async def test_corrected_claim_ignored_on_non_contradicted_result(
        self, config, mock_writer, mock_model
    ):
        """A corrected_claim supplied with a non-contradicted result is ignored
        — corrections only apply when the reviewer explicitly marks a fact
        contradicted."""
        _inject_services(config, mock_model)
        review_json = json.dumps(
            {
                "fact_results": [
                    {
                        "fact_id": "f001",
                        "result": "verified",
                        "evidence": "Confirmed",
                        "corrected_claim": "Should be ignored",
                    }
                ],
                "coverage_assessment": "ok",
                "missing_areas": [],
                "route": "continue",
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=review_json)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="x",
                fact_needed="y",
                claim="original claim",
                status="unverified",
            ),
        ]

        result = await research_review(state, _make_run_config(config))

        fact = result["knowledge"]["facts"][0]
        assert fact["status"] == "verified"
        assert fact["claim"] == "original claim"
        assert not fact.get("corrected")

    @pytest.mark.asyncio
    async def test_whitespace_corrected_claim_treated_as_empty(
        self, config, mock_writer, mock_model
    ):
        """A whitespace-only corrected_claim is treated as empty — the fact
        stays contradicted."""
        _inject_services(config, mock_model)
        review_json = json.dumps(
            {
                "fact_results": [
                    {
                        "fact_id": "f001",
                        "result": "contradicted",
                        "evidence": "Wrong",
                        "corrected_claim": "   ",
                    }
                ],
                "coverage_assessment": "missing",
                "missing_areas": ["x"],
                "route": "retry",
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=review_json)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="x",
                fact_needed="y",
                claim="original claim",
                status="unverified",
            ),
        ]

        result = await research_review(state, _make_run_config(config))

        fact = result["knowledge"]["facts"][0]
        assert fact["status"] == "contradicted"
        assert fact["claim"] == "original claim"

    # ----------------------------------------------------------------------
    # Phase 3: "unknown" result handling for process-metadata claims
    # ----------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_unknown_result_reverts_fact_and_clears_claim(
        self, config, mock_writer, mock_model
    ):
        """When the reviewer marks a fact "unknown" (process-metadata or other
        non-factual claim), the fact reverts to unknown status with its claim
        and citation_ids cleared so it can be re-researched cleanly."""
        _inject_services(config, mock_model)
        review_json = json.dumps(
            {
                "fact_results": [
                    {
                        "fact_id": "f001",
                        "result": "unknown",
                        "evidence": "Claim describes the research process, not a fact",
                    }
                ],
                "coverage_assessment": "Critical fact needs re-research",
                "missing_areas": ["Actual data on X"],
                "route": "retry",
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=review_json)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="X",
                fact_needed="the value of X",
                claim="Insufficient data found about X",
                status="unverified",
                citation_ids=["cit001", "cit002"],
            ),
        ]

        result = await research_review(state, _make_run_config(config))

        fact = result["knowledge"]["facts"][0]
        assert fact["status"] == "unknown"
        assert fact["claim"] == ""
        assert fact["citation_ids"] == []
        # The reviewer's evidence note is preserved to explain the reversion.
        assert fact["verification_note"] == "Claim describes the research process, not a fact"

    @pytest.mark.asyncio
    async def test_unknown_result_handles_academic_phrasings(
        self, config, mock_writer, mock_model
    ):
        """The reviewer's unknown detection handles phrasings the old regex
        patterns missed — academic variants like 'No peer-reviewed publications
        on X were identified in the literature'. This test documents that the
        reviewer (not regex patterns) is now the sole defense."""
        _inject_services(config, mock_model)
        review_json = json.dumps(
            {
                "fact_results": [
                    {
                        "fact_id": "f001",
                        "result": "unknown",
                        "evidence": "Claim describes absence of research, not a fact",
                    }
                ],
                "coverage_assessment": "missing",
                "missing_areas": ["Empirical data"],
                "route": "retry",
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=review_json)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="topic",
                fact_needed="empirical findings on topic",
                claim=(
                    "No peer-reviewed publications on this topic were identified in the literature"
                ),
                status="unverified",
            ),
        ]

        result = await research_review(state, _make_run_config(config))

        fact = result["knowledge"]["facts"][0]
        assert fact["status"] == "unknown"
        assert fact["claim"] == ""

    @pytest.mark.asyncio
    async def test_unknown_result_takes_precedence_over_corrected_claim(
        self, config, mock_writer, mock_model
    ):
        """If the reviewer supplies both result=unknown and a corrected_claim
        (defensive — shouldn't happen in practice), the unknown result wins:
        the fact is reverted, not corrected. Unknown is checked first because
        a process-metadata claim has no meaningful correction."""
        _inject_services(config, mock_model)
        review_json = json.dumps(
            {
                "fact_results": [
                    {
                        "fact_id": "f001",
                        "result": "unknown",
                        "evidence": "Process metadata",
                        "corrected_claim": "Should be ignored",
                    }
                ],
                "coverage_assessment": "missing",
                "missing_areas": ["x"],
                "route": "retry",
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=review_json)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="x",
                fact_needed="y",
                claim="Insufficient data found",
                status="unverified",
            ),
        ]

        result = await research_review(state, _make_run_config(config))

        fact = result["knowledge"]["facts"][0]
        assert fact["status"] == "unknown"
        assert fact["claim"] == ""
        assert not fact.get("corrected")

    @pytest.mark.asyncio
    async def test_unknown_result_mixed_with_other_verdicts(self, config, mock_writer, mock_model):
        """A batch with mixed verdicts — one unknown, one verified, one
        contradicted-with-correction — applies each correctly. This verifies
        the branch ordering and that unknown handling doesn't interfere with
        the other results."""
        _inject_services(config, mock_model)
        review_json = json.dumps(
            {
                "fact_results": [
                    {
                        "fact_id": "f001",
                        "result": "unknown",
                        "evidence": "Process metadata",
                    },
                    {
                        "fact_id": "f002",
                        "result": "verified",
                        "evidence": "Confirmed",
                    },
                    {
                        "fact_id": "f003",
                        "result": "contradicted",
                        "evidence": "Slightly off",
                        "corrected_claim": "Corrected claim",
                    },
                ],
                "coverage_assessment": "mixed",
                "missing_areas": [],
                "route": "continue",
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=review_json)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="A",
                fact_needed="a",
                claim="No data available",
                status="unverified",
                citation_ids=["cit001"],
            ),
            Fact(
                id="f002",
                subject="B",
                fact_needed="b",
                claim="B is true",
                status="unverified",
            ),
            Fact(
                id="f003",
                subject="C",
                fact_needed="c",
                claim="original",
                status="unverified",
            ),
        ]

        result = await research_review(state, _make_run_config(config))

        facts = {f["id"]: f for f in result["knowledge"]["facts"]}
        assert facts["f001"]["status"] == "unknown"
        assert facts["f001"]["claim"] == ""
        assert facts["f001"]["citation_ids"] == []
        assert facts["f002"]["status"] == "verified"
        assert facts["f003"]["status"] == "verified"
        assert facts["f003"]["claim"] == "Corrected claim"
        assert facts["f003"]["corrected"] is True

    @pytest.mark.asyncio
    async def test_unknown_result_clears_citations_even_when_empty_claim_guard_would_fire(
        self, config, mock_writer, mock_model
    ):
        """After the unknown result clears the claim, the post-loop empty-claim
        guard would also fire (it reverts empty-claim verified/unverified
        facts to unknown). This test verifies the two mechanisms compose
        without error — the unknown branch runs first, the guard sees the
        already-unknown fact and skips it (guard only acts on verified/
        unverified)."""
        _inject_services(config, mock_model)
        review_json = json.dumps(
            {
                "fact_results": [
                    {
                        "fact_id": "f001",
                        "result": "unknown",
                        "evidence": "Process metadata",
                    }
                ],
                "coverage_assessment": "missing",
                "missing_areas": ["x"],
                "route": "retry",
            }
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(content=review_json)

        from moira.workflow.nodes.research_review import research_review

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="x",
                fact_needed="y",
                claim="Insufficient data found",
                status="unverified",
                citation_ids=["cit001"],
            ),
        ]

        result = await research_review(state, _make_run_config(config))

        fact = result["knowledge"]["facts"][0]
        # No crash, fact is unknown with cleared claim and citations.
        assert fact["status"] == "unknown"
        assert fact["claim"] == ""
        assert fact["citation_ids"] == []
