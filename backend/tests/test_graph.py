import pytest
from langgraph.graph import START

from moira.config import CostWeights, MoiraConfig
from moira.workflow.graph import build_graph, compile_graph, make_verification_router


@pytest.fixture
def config():
    return MoiraConfig()


def _make_router_state(
    route="accept",
    budget_remaining=60.0,
    synthesis_retry_count=0,
    research_retry_count=0,
):
    cw = CostWeights()
    step_costs = {
        "decomposition": cw.decomposition,
        "tool_identification": cw.tool_identification,
        "planning": cw.planning,
        "research": cw.research,
        "synthesis": cw.synthesis,
        "verification": cw.verification,
        "report_generation": cw.report_generation,
    }
    return {
        "knowledge": {
            "question": "test",
            "verification_history": [
                {
                    "route": route,
                    "goal_met": route == "accept",
                    "goal_assessment": "",
                    "fact_results": [],
                    "conclusion_results": [],
                    "new_unknown_facts": [],
                }
            ],
        },
        "execution_state": {
            "budget_remaining": budget_remaining,
            "budget_limit": 60.0,
            "step_costs": step_costs,
            "synthesis_retry_count": synthesis_retry_count,
            "research_retry_count": research_retry_count,
            "tool_costs": {},
            "tool_call_counts": {},
            "candidate_tools": [],
            "tool_call_plan": [],
            "total_tool_cost_consumed": 0.0,
            "error": "",
            "verification_attempts": 1,
        },
    }


def test_graph_has_seven_nodes(config):
    graph = build_graph(config)
    expected = {
        "decomposition",
        "tool_identification",
        "planning",
        "research",
        "synthesis",
        "verification",
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
    assert ("synthesis", "verification") in edges


def test_graph_verification_routes_to_report_generation_on_accept():
    router = make_verification_router()
    state = _make_router_state(route="accept")
    assert router(state) == "report_generation"


def test_graph_verification_routes_to_synthesis_on_retry():
    router = make_verification_router()
    state = _make_router_state(
        route="retry_synthesis", budget_remaining=60.0,
        synthesis_retry_count=0,
    )
    assert router(state) == "synthesis"


def test_graph_verification_routes_to_tool_identification_on_retry_research():
    router = make_verification_router()
    state = _make_router_state(route="retry_research", budget_remaining=60.0)
    assert router(state) == "tool_identification"


def test_graph_verification_falls_to_report_generation_on_insufficient_budget_retry_synthesis():
    router = make_verification_router()
    state = _make_router_state(
        route="retry_synthesis", budget_remaining=0.0,
        synthesis_retry_count=0,
    )
    assert router(state) == "report_generation"


def test_graph_verification_falls_to_report_generation_on_insufficient_budget_retry_research():
    router = make_verification_router()
    state = _make_router_state(route="retry_research", budget_remaining=0.0)
    assert router(state) == "report_generation"


def test_graph_verification_falls_to_report_generation_on_max_synthesis_retries():
    router = make_verification_router()
    state = _make_router_state(
        route="retry_synthesis", budget_remaining=60.0,
        synthesis_retry_count=2,
    )
    assert router(state) == "report_generation"


def test_graph_verification_falls_to_report_generation_on_max_research_retries():
    router = make_verification_router()
    state = _make_router_state(
        route="retry_research", budget_remaining=60.0,
        research_retry_count=2,
    )
    assert router(state) == "report_generation"


def test_graph_no_verification_history_routes_to_report_generation():
    router = make_verification_router()
    state = _make_router_state()
    state["knowledge"]["verification_history"] = []
    assert router(state) == "report_generation"


def test_graph_compiles_successfully(config):
    compiled = compile_graph(config)
    assert compiled is not None
