"""Tests for moira_eval.metrics.

Pure-function tests over fixture artifacts dicts — no database, no LLM.
Includes explicit test cases for hallucinated fact IDs, uncited conclusions,
and the specialized tool use ratio.
"""

from moira_eval.metrics import compute_metrics

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_artifacts(
    *,
    facts: list[dict] | None = None,
    conclusions: list[dict] | None = None,
    tool_trace: list[dict] | None = None,
    budget_consumed: float = 0.0,
    budget_limit: float = 60.0,
    review_attempts: list[dict] | None = None,
    evaluation_attempts: list[dict] | None = None,
    steps_summary: list[dict] | None = None,
    knowledge: dict | None = None,
) -> dict:
    """Build a minimal artifacts dict for metric tests.

    If ``knowledge`` is provided directly, it's used as-is.  Otherwise a
    knowledge dict is built from ``facts`` and ``conclusions``.
    """
    if knowledge is None:
        knowledge = {
            "facts": facts or [],
            "conclusions": conclusions or [],
        }

    trace = tool_trace or []
    return {
        "knowledge": knowledge,
        "tool_trace": trace,
        "web_search_calls": sum(1 for t in trace if t.get("tool") == "web_search"),
        "url_content_calls": sum(1 for t in trace if t.get("tool") == "url_content"),
        "total_tool_calls": len(trace),
        "tools_used": sorted({t.get("tool", "") for t in trace}),
        "budget_consumed": budget_consumed,
        "budget_limit": budget_limit,
        "review_attempts": review_attempts or [],
        "evaluation_attempts": evaluation_attempts or [],
        "steps_summary": steps_summary or [],
    }


# ---------------------------------------------------------------------------
# Tool metrics
# ---------------------------------------------------------------------------


class TestToolMetrics:
    def test_empty_tool_trace(self):
        metrics = compute_metrics(_make_artifacts())
        assert metrics["total_tool_calls"] == 0
        assert metrics["web_search_calls"] == 0
        assert metrics["specialized_tool_use_ratio"] == 0.0

    def test_all_generic_tools(self):
        trace = [
            {"tool": "web_search"},
            {"tool": "web_search"},
            {"tool": "url_content"},
        ]
        metrics = compute_metrics(_make_artifacts(tool_trace=trace))
        assert metrics["web_search_calls"] == 2
        assert metrics["url_content_calls"] == 1
        assert metrics["specialized_tool_use_ratio"] == 0.0

    def test_all_specialized_tools(self):
        trace = [{"tool": "pokeapi"}, {"tool": "pokemon_db"}]
        metrics = compute_metrics(_make_artifacts(tool_trace=trace))
        assert metrics["specialized_tool_use_ratio"] == 1.0

    def test_mixed_tool_usage(self):
        trace = [
            {"tool": "web_search"},
            {"tool": "pokeapi"},
            {"tool": "pokeapi"},
            {"tool": "url_content"},
        ]
        metrics = compute_metrics(_make_artifacts(tool_trace=trace))
        # 2 specialized / 4 total = 0.5
        assert metrics["specialized_tool_use_ratio"] == 0.5


# ---------------------------------------------------------------------------
# Fact status metrics
# ---------------------------------------------------------------------------


class TestFactStatusMetrics:
    def test_counts_by_status(self):
        facts = [
            {"id": "f1", "status": "verified"},
            {"id": "f2", "status": "verified"},
            {"id": "f3", "status": "unknown"},
            {"id": "f4", "status": "unverified"},
            {"id": "f5", "status": "contradicted"},
        ]
        metrics = compute_metrics(_make_artifacts(facts=facts))
        assert metrics["verified_fact_count"] == 2
        assert metrics["unknown_fact_count"] == 1
        assert metrics["unverified_fact_count"] == 1
        assert metrics["contradicted_fact_count"] == 1

    def test_no_facts(self):
        metrics = compute_metrics(_make_artifacts(facts=[]))
        assert metrics["unknown_fact_count"] == 0
        assert metrics["verified_fact_count"] == 0

    def test_missing_status_defaults_to_unknown(self):
        facts = [{"id": "f1"}]  # no status field
        metrics = compute_metrics(_make_artifacts(facts=facts))
        assert metrics["unknown_fact_count"] == 1


# ---------------------------------------------------------------------------
# Conclusion metrics
# ---------------------------------------------------------------------------


class TestConclusionMetrics:
    def test_unsupported_conclusion_count(self):
        conclusions = [
            {"id": "c1", "status": "unsupported", "supporting_fact_ids": []},
            {"id": "c2", "status": "verified", "supporting_fact_ids": ["f1"]},
            {"id": "c3", "status": "unsupported", "supporting_fact_ids": []},
        ]
        metrics = compute_metrics(_make_artifacts(conclusions=conclusions))
        assert metrics["unsupported_conclusion_count"] == 2
        assert metrics["verified_conclusion_count"] == 1

    def test_uncited_conclusion_count(self):
        conclusions = [
            {
                "id": "c1",
                "status": "verified",
                "supporting_fact_ids": [],
                "citation_ids": ["cit1"],
            },
            {
                "id": "c2",
                "status": "verified",
                "supporting_fact_ids": [],
                "citation_ids": [],
            },
            {
                "id": "c3",
                "status": "verified",
                "supporting_fact_ids": [],
                # no citation_ids key at all
            },
        ]
        metrics = compute_metrics(_make_artifacts(conclusions=conclusions))
        # Only c2 has citation_ids explicitly present and empty.
        # c3 has no citation_ids key — can't determine uncited status.
        assert metrics["uncited_conclusion_count"] == 1


# ---------------------------------------------------------------------------
# Hallucinated fact ID — the highest-signal mechanical metric
# ---------------------------------------------------------------------------


class TestHallucinatedFactIds:
    """The hallucinated fact ID check is the same graph-integrity check that
    research_review performs at runtime — a non-zero count means synthesis
    fabricated a reference."""

    def test_no_hallucinated_ids_when_all_valid(self):
        artifacts = _make_artifacts(
            facts=[{"id": "f001"}, {"id": "f002"}],
            conclusions=[
                {
                    "id": "c1",
                    "supporting_fact_ids": ["f001", "f002"],
                    "status": "verified",
                }
            ],
        )
        metrics = compute_metrics(artifacts)
        assert metrics["hallucinated_fact_id_count"] == 0
        assert metrics["hallucinated_fact_ids"] == []

    def test_detects_hallucinated_id(self):
        artifacts = _make_artifacts(
            facts=[{"id": "f001"}],
            conclusions=[
                {
                    "id": "c1",
                    "supporting_fact_ids": ["f001", "f999"],
                    "status": "verified",
                }
            ],
        )
        metrics = compute_metrics(artifacts)
        assert metrics["hallucinated_fact_id_count"] == 1
        assert "f999" in metrics["hallucinated_fact_ids"]

    def test_detects_multiple_hallucinated_ids(self):
        artifacts = _make_artifacts(
            facts=[{"id": "f001"}],
            conclusions=[
                {
                    "id": "c1",
                    "supporting_fact_ids": ["f001", "f998", "f999"],
                    "status": "verified",
                },
                {
                    "id": "c2",
                    "supporting_fact_ids": ["f997"],
                    "status": "verified",
                },
            ],
        )
        metrics = compute_metrics(artifacts)
        assert metrics["hallucinated_fact_id_count"] == 3
        assert set(metrics["hallucinated_fact_ids"]) == {"f997", "f998", "f999"}

    def test_no_conclusions_means_no_hallucination(self):
        artifacts = _make_artifacts(
            facts=[{"id": "f001"}],
            conclusions=[],
        )
        metrics = compute_metrics(artifacts)
        assert metrics["hallucinated_fact_id_count"] == 0


# ---------------------------------------------------------------------------
# Budget metrics
# ---------------------------------------------------------------------------


class TestBudgetMetrics:
    def test_ratio_calculation(self):
        metrics = compute_metrics(_make_artifacts(budget_consumed=45.0, budget_limit=60.0))
        assert metrics["budget_consumed"] == 45.0
        assert metrics["budget_limit"] == 60.0
        assert metrics["budget_consumed_ratio"] == 0.75

    def test_zero_budget_limit(self):
        metrics = compute_metrics(_make_artifacts(budget_consumed=0.0, budget_limit=0.0))
        assert metrics["budget_consumed_ratio"] == 0.0


# ---------------------------------------------------------------------------
# Retry metrics
# ---------------------------------------------------------------------------


class TestRetryMetrics:
    def test_counts_from_attempts(self):
        artifacts = _make_artifacts(
            review_attempts=[{"route": "retry"}, {"route": "continue"}],
            evaluation_attempts=[{"route": "accept"}],
            steps_summary=[
                {"node_name": "research"},
                {"node_name": "research"},
            ],
        )
        metrics = compute_metrics(artifacts)
        assert metrics["review_count"] == 2
        assert metrics["evaluation_count"] == 1
        assert metrics["research_count"] == 2

    def test_no_retries(self):
        metrics = compute_metrics(_make_artifacts())
        assert metrics["review_count"] == 0
        assert metrics["evaluation_count"] == 0
        assert metrics["research_count"] == 0


# ---------------------------------------------------------------------------
# Null knowledge handling
# ---------------------------------------------------------------------------


class TestNullKnowledge:
    def test_metrics_with_null_knowledge(self):
        """When knowledge snapshot is missing, fact/conclusion metrics
        should default to zero."""
        metrics = compute_metrics(_make_artifacts(knowledge=None))
        assert metrics["unknown_fact_count"] == 0
        assert metrics["unsupported_conclusion_count"] == 0
        assert metrics["hallucinated_fact_id_count"] == 0
        assert metrics["uncited_conclusion_count"] == 0
