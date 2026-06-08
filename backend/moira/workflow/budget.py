import logging

logger = logging.getLogger(__name__)

# Nodes that participate in a full research cycle (planning through
# verification). Report generation is excluded because it is budget-exempt
# and always runs as the terminal node. Compression is bypassed but
# retained in cost mapping for backward compatibility.
_FULL_CYCLE_NODES = (
    "planning",
    "tool_discovery",
    "tool_selection",
    "research_execution",
    "draft_synthesis",
    "verification",
)

# Nodes participating in a draft-only retry (re-synthesis + re-verification).
_DRAFT_RETRY_NODES = (
    "draft_synthesis",
    "verification",
)


def get_node_cost(cost_weights: dict[str, int], node_name: str) -> int:
    """Return the cost weight for a given node name."""
    return cost_weights.get(node_name, 0)


def can_execute(cost_weights: dict[str, int], node_name: str, budget_remaining: float) -> bool:
    """Check whether a node can execute given remaining budget.
    report_generation is always executable (budget-exempt)."""
    if node_name == "report_generation":
        return True
    cost = get_node_cost(cost_weights, node_name)
    return budget_remaining >= cost


def deduct_cost(cost_weights: dict[str, int], node_name: str, budget_remaining: float) -> float:
    """Deduct the node's cost weight from budget_remaining. Returns the
    new budget_remaining. report_generation's cost is deducted for
    accounting but never prevents execution."""
    cost = get_node_cost(cost_weights, node_name)
    new_budget = budget_remaining - cost
    logger.debug(
        "Budget deduction: node=%s, cost=%d, before=%.1f, after=%.1f",
        node_name,
        cost,
        budget_remaining,
        new_budget,
    )
    return new_budget


def full_cycle_cost(cost_weights: dict[str, int]) -> int:
    """Return the total budget cost of one complete research cycle
    (planning through verification). Used by the verification router to
    decide whether a retry loop is affordable."""
    return sum(get_node_cost(cost_weights, n) for n in _FULL_CYCLE_NODES)


def draft_retry_cost(cost_weights: dict[str, int]) -> int:
    """Return the budget cost of a draft-only retry (draft_synthesis +
    verification). Used when verification identifies a synthesis-specific
    problem (cases 7-8) that doesn't require re-running research."""
    return sum(get_node_cost(cost_weights, n) for n in _DRAFT_RETRY_NODES)
