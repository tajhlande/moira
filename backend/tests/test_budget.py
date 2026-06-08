import pytest

from moira.config import CostWeights
from moira.workflow.budget import (
    can_execute,
    deduct_cost,
    draft_retry_cost,
    full_cycle_cost,
    get_node_cost,
)


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


class TestGetNodeCost:
    def test_all_nodes_have_costs(self, cost_weights):
        nodes = [
            "planning",
            "tool_discovery",
            "tool_selection",
            "research_execution",
            "compression",
            "draft_synthesis",
            "verification",
            "report_generation",
        ]
        for node in nodes:
            assert get_node_cost(cost_weights, node) > 0

    def test_unknown_node_has_zero_cost(self, cost_weights):
        assert get_node_cost(cost_weights, "nonexistent") == 0

    def test_specific_costs(self, cost_weights):
        assert get_node_cost(cost_weights, "planning") == 2
        assert get_node_cost(cost_weights, "tool_discovery") == 1
        assert get_node_cost(cost_weights, "tool_selection") == 2
        assert get_node_cost(cost_weights, "research_execution") == 5
        assert get_node_cost(cost_weights, "compression") == 1
        assert get_node_cost(cost_weights, "draft_synthesis") == 3
        assert get_node_cost(cost_weights, "verification") == 4
        assert get_node_cost(cost_weights, "report_generation") == 3


class TestCanExecute:
    def test_sufficient_budget(self, cost_weights):
        assert can_execute(cost_weights, "planning", 10.0) is True

    def test_exact_budget(self, cost_weights):
        assert can_execute(cost_weights, "planning", 2.0) is True

    def test_insufficient_budget(self, cost_weights):
        assert can_execute(cost_weights, "planning", 1.0) is False

    def test_report_generation_always_executes(self, cost_weights):
        assert can_execute(cost_weights, "report_generation", 0.0) is True
        assert can_execute(cost_weights, "report_generation", -5.0) is True

    def test_zero_budget_blocks_non_exempt(self, cost_weights):
        assert can_execute(cost_weights, "planning", 0.0) is False
        assert can_execute(cost_weights, "verification", 0.0) is False


class TestDeductCost:
    def test_normal_deduction(self, cost_weights):
        result = deduct_cost(cost_weights, "planning", 10.0)
        assert result == 8.0

    def test_report_generation_still_deducts(self, cost_weights):
        result = deduct_cost(cost_weights, "report_generation", 5.0)
        assert result == 2.0

    def test_deduction_can_go_negative(self, cost_weights):
        result = deduct_cost(cost_weights, "research_execution", 3.0)
        assert result == -2.0


class TestFullCycleCost:
    def test_full_cycle_excludes_compression_and_report_generation(self, cost_weights):
        total_all = sum(cost_weights.values())
        assert full_cycle_cost(cost_weights) == (
            total_all
            - cost_weights["report_generation"]
            - cost_weights["compression"]
        )


class TestDraftRetryCost:
    def test_draft_retry_cost(self, cost_weights):
        assert draft_retry_cost(cost_weights) == (
            cost_weights["draft_synthesis"] + cost_weights["verification"]
        )
