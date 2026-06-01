import pytest

from moira.config import MoiraConfig
from moira.models.state import ResearchState, VerificationReport
from moira.workflow.graph import build_graph, compile_graph, make_verification_router


@pytest.fixture
def config():
    return MoiraConfig()


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
    def test_accept_routes_to_report(self, config):
        route_after_verification = make_verification_router(config)
        state: ResearchState = {
            "question": "test",
            "plan": "",
            "active_tools": [],
            "findings": [],
            "compressed_findings": [],
            "draft": "A draft",
            "verification": "",
            "report": None,
            "budget_remaining": 50.0,
            "budget_limit": 50.0,
            "verification_history": [
                _make_report(outcome="accept", case=1),
            ],
            "unverified_claims": [],
            "error": "",
            "draft_retry_count": 0,
        }
        result = route_after_verification(state)
        assert result == "report_generation"

    def test_retry_plan_with_budget_routes_to_planning(self, config):
        route_after_verification = make_verification_router(config)
        state: ResearchState = {
            "question": "test",
            "plan": "",
            "active_tools": [],
            "findings": [],
            "compressed_findings": [],
            "draft": "A draft",
            "verification": "",
            "report": None,
            "budget_remaining": 50.0,
            "budget_limit": 50.0,
            "verification_history": [
                _make_report(
                    outcome="retry_plan",
                    case=5,
                    unsupported_claims=["claim 1 is wrong"],
                ),
            ],
            "unverified_claims": ["claim 1 is wrong"],
            "error": "",
            "draft_retry_count": 0,
        }
        result = route_after_verification(state)
        assert result == "planning"

    def test_retry_plan_without_budget_routes_to_report(self, config):
        route_after_verification = make_verification_router(config)
        state: ResearchState = {
            "question": "test",
            "plan": "",
            "active_tools": [],
            "findings": [],
            "compressed_findings": [],
            "draft": "A draft",
            "verification": "",
            "report": None,
            "budget_remaining": 5.0,
            "budget_limit": 50.0,
            "verification_history": [
                _make_report(
                    outcome="retry_plan",
                    case=5,
                    unsupported_claims=["claim 1 is wrong"],
                ),
            ],
            "unverified_claims": ["claim 1 is wrong"],
            "error": "",
            "draft_retry_count": 0,
        }
        result = route_after_verification(state)
        assert result == "report_generation"

    def test_error_outcome_routes_to_report(self, config):
        route_after_verification = make_verification_router(config)
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
            "verification_history": [
                _make_report(outcome="error", case=11, assessment="Technical failure"),
            ],
            "unverified_claims": [],
            "error": "Technical failure",
            "draft_retry_count": 0,
        }
        result = route_after_verification(state)
        assert result == "report_generation"

    def test_empty_history_routes_to_report(self, config):
        route_after_verification = make_verification_router(config)
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
            "verification_history": [],
            "unverified_claims": [],
            "error": "",
            "draft_retry_count": 0,
        }
        result = route_after_verification(state)
        assert result == "report_generation"

    def test_retry_draft_with_budget_routes_to_draft_synthesis(self, config):
        route_after_verification = make_verification_router(config)
        state: ResearchState = {
            "question": "test",
            "plan": "",
            "active_tools": [],
            "findings": [],
            "compressed_findings": [],
            "draft": "Off-topic draft",
            "verification": "",
            "report": None,
            "budget_remaining": 50.0,
            "budget_limit": 50.0,
            "verification_history": [
                _make_report(
                    outcome="retry_draft",
                    case=7,
                    assessment="Draft is off-topic",
                ),
            ],
            "unverified_claims": [],
            "error": "",
            "draft_retry_count": 0,
        }
        result = route_after_verification(state)
        assert result == "draft_synthesis"

    def test_retry_draft_with_max_retries_falls_through_to_planning(self, config):
        route_after_verification = make_verification_router(config)
        state: ResearchState = {
            "question": "test",
            "plan": "",
            "active_tools": [],
            "findings": [],
            "compressed_findings": [],
            "draft": "Off-topic draft",
            "verification": "",
            "report": None,
            "budget_remaining": 50.0,
            "budget_limit": 50.0,
            "verification_history": [
                _make_report(
                    outcome="retry_draft",
                    case=7,
                    assessment="Draft is off-topic",
                ),
            ],
            "unverified_claims": [],
            "error": "",
            "draft_retry_count": 1,
        }
        result = route_after_verification(state)
        assert result == "planning"

    def test_retry_draft_without_budget_routes_to_report(self, config):
        route_after_verification = make_verification_router(config)
        state: ResearchState = {
            "question": "test",
            "plan": "",
            "active_tools": [],
            "findings": [],
            "compressed_findings": [],
            "draft": "Off-topic draft",
            "verification": "",
            "report": None,
            "budget_remaining": 2.0,
            "budget_limit": 50.0,
            "verification_history": [
                _make_report(
                    outcome="retry_draft",
                    case=7,
                    assessment="Draft is off-topic",
                ),
            ],
            "unverified_claims": [],
            "error": "",
            "draft_retry_count": 0,
        }
        result = route_after_verification(state)
        assert result == "report_generation"
