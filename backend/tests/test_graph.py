import pytest
from langgraph.graph import START

from moira.config import CostWeights, MoiraConfig
from moira.workflow.graph import (
    build_graph,
    compile_graph,
    make_evaluation_router,
    make_review_router,
)


@pytest.fixture
def config():
    return MoiraConfig()


def _make_review_router_state(
    route="continue",
    budget_remaining=60.0,
    review_count=1,
    max_review=3,
):
    cw = CostWeights()
    step_costs = {
        "decomposition": cw.decomposition,
        "tool_identification": cw.tool_identification,
        "planning": cw.planning,
        "research": cw.research,
        "synthesis": cw.synthesis,
        "research_review": cw.research_review,
        "evaluation": cw.evaluation,
        "report_generation": cw.report_generation,
    }
    return {
        "knowledge": {
            "question": "test",
            "review_history": [
                {
                    "route": route,
                    "fact_results": [],
                    "coverage_assessment": "",
                    "missing_areas": [],
                }
            ],
        },
        "execution_state": {
            "budget_remaining": budget_remaining,
            "budget_limit": 60.0,
            "step_costs": step_costs,
            "retry_limits": {"max_review": max_review, "max_evaluation": 2},
            "review_count": review_count,
            "research_retry_count": 0,
            "evaluation_count": 0,
            "tool_costs": {},
            "tool_call_counts": {},
            "candidate_tools": [],
            "tool_call_plan": [],
            "total_tool_cost_consumed": 0.0,
            "error": "",
        },
    }


def _make_evaluation_router_state(
    route="accept",
    budget_remaining=60.0,
    evaluation_count=1,
    max_evaluation=2,
):
    cw = CostWeights()
    step_costs = {
        "decomposition": cw.decomposition,
        "tool_identification": cw.tool_identification,
        "planning": cw.planning,
        "research": cw.research,
        "synthesis": cw.synthesis,
        "research_review": cw.research_review,
        "evaluation": cw.evaluation,
        "report_generation": cw.report_generation,
    }
    return {
        "knowledge": {
            "question": "test",
            "evaluation_history": [
                {
                    "route": route,
                    "goal_met": route == "accept",
                    "goal_assessment": "",
                    "conclusion_results": [],
                }
            ],
        },
        "execution_state": {
            "budget_remaining": budget_remaining,
            "budget_limit": 60.0,
            "step_costs": step_costs,
            "retry_limits": {"max_review": 3, "max_evaluation": max_evaluation},
            "evaluation_count": evaluation_count,
            "review_count": 0,
            "research_retry_count": 0,
            "tool_costs": {},
            "tool_call_counts": {},
            "candidate_tools": [],
            "tool_call_plan": [],
            "total_tool_cost_consumed": 0.0,
            "error": "",
        },
    }


def test_graph_has_eight_nodes(config):
    graph = build_graph(config)
    expected = {
        "decomposition",
        "tool_identification",
        "planning",
        "research",
        "synthesis",
        "research_review",
        "evaluation",
        "report_generation",
    }
    assert set(graph.nodes) == expected


def test_graph_linear_path(config):
    graph = build_graph(config)
    edges = graph.edges

    assert (START, "decomposition") in edges
    assert ("decomposition", "tool_identification") in edges
    assert ("tool_identification", "planning") in edges
    assert ("planning", "research") in edges
    assert ("research", "synthesis") in edges
    assert ("synthesis", "research_review") in edges


def test_review_router_continue_routes_to_evaluation():
    router = make_review_router()
    state = _make_review_router_state(route="continue")
    assert router(state) == "evaluation"


def test_review_router_retry_routes_to_research():
    router = make_review_router()
    state = _make_review_router_state(route="retry", budget_remaining=60.0, review_count=1)
    assert router(state) == "research"


def test_review_router_retry_falls_to_evaluation_on_insufficient_budget():
    router = make_review_router()
    state = _make_review_router_state(route="retry", budget_remaining=0.0, review_count=1)
    assert router(state) == "evaluation"


def test_review_router_retry_falls_to_evaluation_on_max_attempts():
    router = make_review_router()
    state = _make_review_router_state(route="retry", budget_remaining=60.0, review_count=3)
    assert router(state) == "evaluation"


def test_evaluation_router_accept_routes_to_report_generation():
    router = make_evaluation_router()
    state = _make_evaluation_router_state(route="accept")
    assert router(state) == "report_generation"


def test_evaluation_router_retry_routes_to_tool_identification():
    router = make_evaluation_router()
    state = _make_evaluation_router_state(route="retry", budget_remaining=60.0, evaluation_count=1)
    assert router(state) == "tool_identification"


def test_evaluation_router_retry_falls_to_report_on_insufficient_budget():
    router = make_evaluation_router()
    state = _make_evaluation_router_state(route="retry", budget_remaining=0.0, evaluation_count=1)
    assert router(state) == "report_generation"


def test_evaluation_router_retry_falls_to_report_on_max_attempts():
    router = make_evaluation_router()
    state = _make_evaluation_router_state(route="retry", budget_remaining=60.0, evaluation_count=2)
    assert router(state) == "report_generation"


def test_review_router_custom_max_review_allows_more_retries():
    """When retry_limits.max_review is raised, the router should allow
    more retries than the default of 3."""
    router = make_review_router()
    state = _make_review_router_state(
        route="retry",
        budget_remaining=60.0,
        review_count=4,
        max_review=5,
    )
    assert router(state) == "research"


def test_evaluation_router_custom_max_evaluation_allows_more_retries():
    """When retry_limits.max_evaluation is raised, the router should allow
    more retries than the default of 2."""
    router = make_evaluation_router()
    state = _make_evaluation_router_state(
        route="retry",
        budget_remaining=60.0,
        evaluation_count=2,
        max_evaluation=4,
    )
    assert router(state) == "tool_identification"


def test_review_router_falls_back_to_default_without_retry_limits():
    """When retry_limits is absent from state, the router should fall
    back to the default limit of 3."""
    router = make_review_router()
    state = _make_review_router_state(route="retry", budget_remaining=60.0, review_count=2)
    # Remove retry_limits to test fallback
    del state["execution_state"]["retry_limits"]
    # review_count=2 < default 3 → should route to research
    assert router(state) == "research"
    # review_count=3 >= default 3 → should fall to evaluation
    state["execution_state"]["review_count"] = 3
    assert router(state) == "evaluation"


def test_graph_compiles_successfully(config):
    compiled = compile_graph(config)
    assert compiled is not None
