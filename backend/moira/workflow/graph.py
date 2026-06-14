"""Research workflow graph for the overhauled research loop.

Nodes: decomposition -> tool_identification -> planning -> research ->
synthesis -> verification -> [conditional] -> report_generation -> END

Verification routes:
- accept -> report_generation
- retry_synthesis -> synthesis (budget check)
- retry_research -> tool_identification (budget check)
- budget exhausted -> report_generation
"""

import logging

from langgraph.graph import END, START, StateGraph

from moira.config import MoiraConfig
from moira.models.knowledge import ResearchState
from moira.workflow.budget import (
    research_retry_cost,
    synthesis_retry_cost,
)
from moira.workflow.nodes.decomposition import decomposition
from moira.workflow.nodes.planning import planning
from moira.workflow.nodes.report_generation import report_generation
from moira.workflow.nodes.research import research
from moira.workflow.nodes.synthesis import synthesis
from moira.workflow.nodes.tool_identification import tool_identification
from moira.workflow.nodes.verification import verification

logger = logging.getLogger(__name__)


def make_verification_router():
    """Return a routing function that checks verification outcome against
    budget and retry limits."""

    def route_after_verification(state: ResearchState) -> str:
        es = state.get("execution_state", {})
        knowledge = state.get("knowledge", {})
        step_costs = es.get("step_costs", {})
        verification_history = knowledge.get("verification_history", [])

        if not verification_history:
            return "report_generation"

        latest = verification_history[-1]
        route = latest.get("route", "accept")

        if route == "accept":
            logger.info("Verification accepted, routing to report_generation")
            return "report_generation"

        budget_remaining = es.get("budget_remaining", 0.0)

        if route == "retry_synthesis":
            synthesis_retries = es.get("synthesis_retry_count", 0)
            sr_cost = synthesis_retry_cost(step_costs)
            # Entry-count semantics: synthesis increments on every entry.
            # Allow retry when fewer than 2 entries (initial + 1 retry).
            if synthesis_retries < 2 and budget_remaining >= sr_cost:
                logger.info(
                    "retry_synthesis: budget sufficient (%.1f >= %.1f)",
                    budget_remaining,
                    sr_cost,
                )
                return "synthesis"
            logger.info(
                "retry_synthesis declined (retries=%d, budget=%.1f)",
                synthesis_retries,
                budget_remaining,
            )
            # Fall through to report_generation
            return "report_generation"

        if route == "retry_research":
            research_retries = es.get("research_retry_count", 0)
            rr_cost = research_retry_cost(step_costs)
            # Entry-count semantics: planning increments on every entry.
            # Allow retry when fewer than 2 entries (initial + 1 retry).
            if research_retries < 2 and budget_remaining >= rr_cost:
                logger.info(
                    "retry_research: budget sufficient (%.1f >= %.1f)",
                    budget_remaining,
                    rr_cost,
                )
                return "tool_identification"
            logger.info(
                "retry_research: budget insufficient (%.1f < %.1f)",
                budget_remaining,
                rr_cost,
            )
            return "report_generation"

        # Unknown route or fallback
        return "report_generation"

    return route_after_verification


def build_graph(config: MoiraConfig) -> StateGraph:
    """Build the LangGraph research workflow with 7 nodes and conditional
    routing after verification."""
    graph = StateGraph(ResearchState)

    graph.add_node("decomposition", decomposition)
    graph.add_node("tool_identification", tool_identification)
    graph.add_node("planning", planning)
    graph.add_node("research", research)
    graph.add_node("synthesis", synthesis)
    graph.add_node("verification", verification)
    graph.add_node("report_generation", report_generation)

    # Linear path — node errors propagate as exceptions and are caught by
    # ActiveRun._run_graph, which persists the step detail and stops the run.
    graph.add_edge(START, "decomposition")
    graph.add_edge("decomposition", "tool_identification")
    graph.add_edge("tool_identification", "planning")
    graph.add_edge("planning", "research")
    graph.add_edge("research", "synthesis")
    graph.add_edge("synthesis", "verification")

    # Conditional routing after verification
    graph.add_conditional_edges(
        "verification",
        make_verification_router(),
        {
            "tool_identification": "tool_identification",
            "synthesis": "synthesis",
            "report_generation": "report_generation",
        },
    )

    graph.add_edge("report_generation", END)

    logger.info("Research graph built with 7 nodes")
    return graph


def compile_graph(config: MoiraConfig, checkpointer=None):
    """Build and compile the research graph."""
    graph = build_graph(config)
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("Research graph compiled")
    return compiled
