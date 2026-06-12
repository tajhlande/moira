"""Budget management for the overhauled research loop.

Step costs match the overhaul plan (section 2.3):
- decomposition: 2
- tool_identification: 1
- planning: 2
- research: 10
- synthesis: 5
- verification: 8
- report_generation: 3 (budget-exempt)

Tool invocation costs are deducted per-call during research and verification.
"""

import logging

logger = logging.getLogger(__name__)

# Nodes in a full research cycle (decomposition through verification).
_FULL_CYCLE_NODES = (
    "decomposition",
    "tool_identification",
    "planning",
    "research",
    "synthesis",
    "verification",
)

# Nodes in a synthesis-only retry (synthesis + verification).
_SYNTHESIS_RETRY_NODES = (
    "synthesis",
    "verification",
)

# Nodes in a research retry (tool_identification through verification).
_RESEARCH_RETRY_NODES = (
    "tool_identification",
    "planning",
    "research",
    "synthesis",
    "verification",
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
    """Total step cost of one complete cycle (decomposition through verification)."""
    return sum(get_node_cost(step_costs, n) for n in _FULL_CYCLE_NODES)


def research_retry_cost(step_costs: dict[str, float]) -> float:
    """Step cost of a research retry (tool_identification through verification)."""
    return sum(get_node_cost(step_costs, n) for n in _RESEARCH_RETRY_NODES)


def synthesis_retry_cost(step_costs: dict[str, float]) -> float:
    """Step cost of a synthesis retry (synthesis + verification)."""
    return sum(get_node_cost(step_costs, n) for n in _SYNTHESIS_RETRY_NODES)
