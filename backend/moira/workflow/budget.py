"""Budget management for the overhauled research loop.

Step costs (defaults):
- decomposition: 2
- tool_identification: 1
- planning: 2
- research: 10
- synthesis: 5
- research_review: 3
- evaluation: 5
- report_generation: 3 (budget-exempt)

Tool invocation costs are deducted per-call during research.
"""

import logging

logger = logging.getLogger(__name__)

# Nodes in a full research cycle (decomposition through evaluation).
_FULL_CYCLE_NODES = (
    "decomposition",
    "tool_identification",
    "planning",
    "research",
    "synthesis",
    "research_review",
    "evaluation",
)

# Nodes in a research_review retry (research through research_review).
# research_review → retry → research (light retry with existing tools).
_REVIEW_RETRY_NODES = (
    "research",
    "synthesis",
    "research_review",
)

# Nodes in an evaluation retry (tool_identification through evaluation).
# evaluation → retry → tool_identification (heavy retry, full pipeline reset).
_EVALUATION_RETRY_NODES = (
    "tool_identification",
    "planning",
    "research",
    "synthesis",
    "research_review",
    "evaluation",
)


def get_node_cost(step_costs: dict[str, float], node_name: str) -> float:
    return step_costs.get(node_name, 0)


def can_execute(step_costs: dict[str, float], node_name: str, budget_remaining: float) -> bool:
    """Report generation is always executable (budget-exempt)."""
    if node_name == "report_generation":
        return True
    cost = get_node_cost(step_costs, node_name)
    return budget_remaining >= cost


def deduct_cost(step_costs: dict[str, float], node_name: str, budget_remaining: float) -> float:
    """Deduct node cost. report_generation cost is deducted for accounting
    but never prevents execution."""
    cost = get_node_cost(step_costs, node_name)
    new_budget = budget_remaining - cost
    logger.debug(
        "Budget deduction: node=%s, cost=%.1f, before=%.1f, after=%.1f",
        node_name,
        cost,
        budget_remaining,
        new_budget,
    )
    return new_budget


def full_cycle_cost(step_costs: dict[str, float]) -> float:
    """Total step cost of one complete cycle (decomposition through evaluation)."""
    return sum(get_node_cost(step_costs, n) for n in _FULL_CYCLE_NODES)


def review_retry_cost(step_costs: dict[str, float]) -> float:
    """Step cost of a research_review retry (research through research_review)."""
    return sum(get_node_cost(step_costs, n) for n in _REVIEW_RETRY_NODES)


def evaluation_retry_cost(step_costs: dict[str, float]) -> float:
    """Step cost of an evaluation retry (tool_identification through evaluation)."""
    return sum(get_node_cost(step_costs, n) for n in _EVALUATION_RETRY_NODES)
