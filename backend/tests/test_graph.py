import pytest

from moira.config import CostWeights, MoiraConfig
from moira.models.state import ResearchState, VerificationReport
from moira.workflow.graph import build_graph, compile_graph, make_verification_router


@pytest.fixture
def config():
    return MoiraConfig()


@pytest.fixture
def cost_weights():
    cw = CostWeights()
    return {
        "planning": cw.planning,
        "tool_discovery": cw.tool_discovery,
        "tool_selection": cw.tool_selection,
        "research_execution": cw.research_execution,
        "compression": cw.compression,
        "draft_synthesis": cw.draft_synthesis,
        "verification": cw.verification,
        "report_generation": cw.report_generation,
    }


def _make_report(**overrides) -> VerificationReport:
    defaults = {
        "outcome": "accept",
        "case": 1,
        "assessment": "",
        "supported_claims": [],
        "unsupported_claims": [],
        "contradictions": [],
        "relevance": "on_topic",
        "depth": "sufficient",
        "guidance": "",
    }
    defaults.update(overrides)
    return VerificationReport(**defaults)


def _base_state(cost_weights, **overrides) -> ResearchState:
    state: ResearchState = {
        "question": "test",
        "plan": "",
        "active_tools": [],
        "findings": [],
        "compressed_findings": [],
        "draft": "",
        "verification": "",
        "report": None,
        "budget_remaining": 50.0,
        "budget_limit": 50.0,
        "cost_weights": cost_weights,
        "verification_history": [],
        "unverified_claims": [],
        "error": "",
        "draft_retry_count": 0,
    }
    state.update(overrides)
    return state


class TestGraphStructure:
    def test_build_graph(self, config):
        graph = build_graph(config)
        assert graph is not None

    def test_compile_graph_without_checkpointer(self, config):
        compiled = compile_graph(config)
        assert compiled is not None

    def test_compile_graph_with_in_memory_checkpointer(self, config):
        from langgraph.checkpoint.memory import InMemorySaver

        saver = InMemorySaver()
        compiled = compile_graph(config, checkpointer=saver)
        assert compiled is not None


class TestVerificationRouting:
    def test_accept_routes_to_report(self, cost_weights):
        route_after_verification = make_verification_router()
        state = _base_state(
            cost_weights,
            verification_history=[_make_report(outcome="accept", case=1)],
        )
        result = route_after_verification(state)
        assert result == "report_generation"

    def test_retry_plan_with_budget_routes_to_planning(self, cost_weights):
        route_after_verification = make_verification_router()
        state = _base_state(
            cost_weights,
            budget_remaining=50.0,
            verification_history=[
                _make_report(
                    outcome="retry_plan",
                    case=5,
                    unsupported_claims=["claim 1 is wrong"],
                ),
            ],
            unverified_claims=["claim 1 is wrong"],
        )
        result = route_after_verification(state)
        assert result == "planning"

    def test_retry_plan_without_budget_routes_to_report(self, cost_weights):
        route_after_verification = make_verification_router()
        state = _base_state(
            cost_weights,
            budget_remaining=5.0,
            verification_history=[
                _make_report(
                    outcome="retry_plan",
                    case=5,
                    unsupported_claims=["claim 1 is wrong"],
                ),
            ],
            unverified_claims=["claim 1 is wrong"],
        )
        result = route_after_verification(state)
        assert result == "report_generation"

    def test_error_outcome_routes_to_report(self, cost_weights):
        route_after_verification = make_verification_router()
        state = _base_state(
            cost_weights,
            verification_history=[
                _make_report(outcome="error", case=11, assessment="Technical failure"),
            ],
            error="Technical failure",
        )
        result = route_after_verification(state)
        assert result == "report_generation"

    def test_empty_history_routes_to_report(self, cost_weights):
        route_after_verification = make_verification_router()
        state = _base_state(cost_weights)
        result = route_after_verification(state)
        assert result == "report_generation"

    def test_retry_draft_with_budget_routes_to_draft_synthesis(self, cost_weights):
        route_after_verification = make_verification_router()
        state = _base_state(
            cost_weights,
            draft="Off-topic draft",
            budget_remaining=50.0,
            verification_history=[
                _make_report(
                    outcome="retry_draft",
                    case=7,
                    assessment="Draft is off-topic",
                ),
            ],
        )
        result = route_after_verification(state)
        assert result == "draft_synthesis"

    def test_retry_draft_with_max_retries_falls_through_to_planning(self, cost_weights):
        route_after_verification = make_verification_router()
        state = _base_state(
            cost_weights,
            draft="Off-topic draft",
            budget_remaining=50.0,
            verification_history=[
                _make_report(
                    outcome="retry_draft",
                    case=7,
                    assessment="Draft is off-topic",
                ),
            ],
            draft_retry_count=1,
        )
        result = route_after_verification(state)
        assert result == "planning"

    def test_retry_draft_without_budget_routes_to_report(self, cost_weights):
        route_after_verification = make_verification_router()
        state = _base_state(
            cost_weights,
            draft="Off-topic draft",
            budget_remaining=2.0,
            verification_history=[
                _make_report(
                    outcome="retry_draft",
                    case=7,
                    assessment="Draft is off-topic",
                ),
            ],
        )
        result = route_after_verification(state)
        assert result == "report_generation"
