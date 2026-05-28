import logging

from moira.config import MoiraConfig

logger = logging.getLogger(__name__)


def get_node_cost(config: MoiraConfig, node_name: str) -> int:
    """Return the cost weight for a given node name."""
    cw = config.budget.cost_weights
    mapping = {
        "planning": cw.planning,
        "tool_discovery": cw.tool_discovery,
        "tool_selection": cw.tool_selection,
        "research_execution": cw.research_execution,
        "compression": cw.compression,
        "draft_synthesis": cw.draft_synthesis,
        "verification": cw.verification,
        "report_generation": cw.report_generation,
    }
    return mapping.get(node_name, 0)


def can_execute(config: MoiraConfig, node_name: str, budget_remaining: float) -> bool:
    """Check whether a node can execute given remaining budget.
    ReportGeneration is always executable (budget-exempt)."""
    if node_name == "report_generation":
        return True
    cost = get_node_cost(config, node_name)
    return budget_remaining >= cost


def deduct_cost(config: MoiraConfig, node_name: str, budget_remaining: float) -> float:
    """Deduct the node's cost weight from budget_remaining. Returns the
    new budget_remaining. ReportGeneration's cost is deducted for
    accounting but never prevents execution."""
    cost = get_node_cost(config, node_name)
    new_budget = budget_remaining - cost
    logger.debug(
        "Budget deduction: node=%s, cost=%d, before=%.1f, after=%.1f",
        node_name,
        cost,
        budget_remaining,
        new_budget,
    )
    return new_budget
