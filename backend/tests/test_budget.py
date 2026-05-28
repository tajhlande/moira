import pytest

from moira.config import BudgetConfig, CostWeights, MoiraConfig
from moira.workflow.budget import can_execute, deduct_cost, get_node_cost


@pytest.fixture
def config():
    return MoiraConfig(
        budget=BudgetConfig(
            default_limit=50,
            cost_weights=CostWeights(),
        )
    )


class TestGetNodeCost:
    def test_all_nodes_have_costs(self, config):
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
            assert get_node_cost(config, node) > 0

    def test_unknown_node_has_zero_cost(self, config):
        assert get_node_cost(config, "nonexistent") == 0

    def test_specific_costs(self, config):
        assert get_node_cost(config, "planning") == 2
        assert get_node_cost(config, "tool_discovery") == 1
        assert get_node_cost(config, "tool_selection") == 2
        assert get_node_cost(config, "research_execution") == 5
        assert get_node_cost(config, "compression") == 1
        assert get_node_cost(config, "draft_synthesis") == 3
        assert get_node_cost(config, "verification") == 4
        assert get_node_cost(config, "report_generation") == 3


class TestCanExecute:
    def test_sufficient_budget(self, config):
        assert can_execute(config, "planning", 10.0) is True

    def test_exact_budget(self, config):
        assert can_execute(config, "planning", 2.0) is True

    def test_insufficient_budget(self, config):
        assert can_execute(config, "planning", 1.0) is False

    def test_report_generation_always_executes(self, config):
        assert can_execute(config, "report_generation", 0.0) is True
        assert can_execute(config, "report_generation", -5.0) is True

    def test_zero_budget_blocks_non_exempt(self, config):
        assert can_execute(config, "planning", 0.0) is False
        assert can_execute(config, "verification", 0.0) is False


class TestDeductCost:
    def test_normal_deduction(self, config):
        result = deduct_cost(config, "planning", 10.0)
        assert result == 8.0

    def test_report_generation_still_deducts(self, config):
        result = deduct_cost(config, "report_generation", 5.0)
        assert result == 2.0

    def test_deduction_can_go_negative(self, config):
        result = deduct_cost(config, "research_execution", 3.0)
        assert result == -2.0


class TestFullCycleCost:
    def test_full_cycle_is_18(self, config):
        assert config.full_cycle_cost == 18

    def test_full_cycle_excludes_report_generation(self, config):
        total_all = sum(
            [
                config.budget.cost_weights.planning,
                config.budget.cost_weights.tool_discovery,
                config.budget.cost_weights.tool_selection,
                config.budget.cost_weights.research_execution,
                config.budget.cost_weights.compression,
                config.budget.cost_weights.draft_synthesis,
                config.budget.cost_weights.verification,
                config.budget.cost_weights.report_generation,
            ]
        )
        assert config.full_cycle_cost == total_all - config.budget.cost_weights.report_generation
