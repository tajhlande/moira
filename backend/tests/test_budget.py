from moira.config import CostWeights
from moira.workflow.budget import (
    can_execute,
    deduct_cost,
    estimated_tool_cost_per_research,
    evaluation_retry_cost,
    full_cycle_cost,
    get_node_cost,
    review_retry_cost,
)


def _default_step_costs():
    cw = CostWeights()
    return {
        "decomposition": cw.decomposition,
        "tool_identification": cw.tool_identification,
        "planning": cw.planning,
        "research": cw.research,
        "synthesis": cw.synthesis,
        "research_review": cw.research_review,
        "evaluation": cw.evaluation,
        "report_generation": cw.report_generation,
    }


def test_get_node_cost_returns_correct_cost():
    sc = _default_step_costs()
    assert get_node_cost(sc, "decomposition") == 2
    assert get_node_cost(sc, "tool_identification") == 1
    assert get_node_cost(sc, "planning") == 2
    assert get_node_cost(sc, "research") == 10
    assert get_node_cost(sc, "synthesis") == 5
    assert get_node_cost(sc, "research_review") == 3
    assert get_node_cost(sc, "evaluation") == 5
    assert get_node_cost(sc, "report_generation") == 3


def test_get_node_cost_returns_zero_for_unknown_node():
    assert get_node_cost(_default_step_costs(), "nonexistent") == 0


def test_can_execute_returns_true_when_sufficient_budget():
    assert can_execute(_default_step_costs(), "research", 15.0)


def test_can_execute_returns_false_when_insufficient_budget():
    assert not can_execute(_default_step_costs(), "research", 5.0)


def test_can_execute_always_true_for_report_generation():
    assert can_execute(_default_step_costs(), "report_generation", 0.0)


def test_can_execute_exact_budget():
    assert can_execute(_default_step_costs(), "research", 10.0)


def test_deduct_cost_reduces_budget():
    assert deduct_cost(_default_step_costs(), "research", 20.0) == 10.0


def test_deduct_cost_can_go_negative():
    assert deduct_cost(_default_step_costs(), "research", 5.0) == -5.0


def test_full_cycle_cost():
    # decomposition(2) + tool_identification(1) + planning(2) + research(10)
    # + synthesis(5) + research_review(3) + evaluation(5) = 28
    assert full_cycle_cost(_default_step_costs()) == 28


def test_review_retry_cost():
    # planning(2) + research(10) + synthesis(5) + research_review(3) = 20
    assert review_retry_cost(_default_step_costs()) == 20


def test_evaluation_retry_cost():
    # tool_identification(1) + planning(2) + research(10) + synthesis(5)
    # + research_review(3) + evaluation(5) = 26
    assert evaluation_retry_cost(_default_step_costs()) == 26


def test_deduct_cost_with_zero_cost_node():
    assert deduct_cost({}, "unknown_node", 10.0) == 10.0


def test_estimated_tool_cost_per_research_zero_when_no_research():
    assert estimated_tool_cost_per_research(0.0, 0) == 0.0
    assert estimated_tool_cost_per_research(10.0, 0) == 0.0


def test_estimated_tool_cost_per_research_averages():
    # 2 research cycles, 12 total tool cost → avg 6
    assert estimated_tool_cost_per_research(12.0, 2) == 6.0
    # 1 research cycle, 8 total tool cost → avg 8
    assert estimated_tool_cost_per_research(8.0, 1) == 8.0
