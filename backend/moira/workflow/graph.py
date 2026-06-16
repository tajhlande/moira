"""Research workflow graph for the overhauled research loop.

Nodes: decomposition -> tool_identification -> planning -> research ->
synthesis -> research_review -> [conditional] -> evaluation ->
[conditional] -> report_generation -> END

Research review routes:
- continue -> evaluation
- retry -> research (light retry, max 3 per evaluation cycle)
- budget exhausted -> evaluation

Evaluation routes:
- accept -> report_generation
- retry -> tool_identification (heavy retry, full pipeline reset, max 2)
- retry declined -> report_generation
"""

import logging

from langgraph.graph import END, START, StateGraph

from moira.config import MoiraConfig
from moira.models.knowledge import ResearchState
from moira.workflow.budget import (
    evaluation_retry_cost,
    review_retry_cost,
)
from moira.workflow.nodes.decomposition import decomposition
from moira.workflow.nodes.evaluation import evaluation
from moira.workflow.nodes.planning import planning
from moira.workflow.nodes.report_generation import report_generation
from moira.workflow.nodes.research import research
from moira.workflow.nodes.research_review import research_review
from moira.workflow.nodes.synthesis import synthesis
from moira.workflow.nodes.tool_identification import tool_identification

logger = logging.getLogger(__name__)

# Default retry attempt limits (configurable via system/conversation settings).
DEFAULT_REVIEW_MAX_ATTEMPTS = 3
DEFAULT_EVALUATION_MAX_ATTEMPTS = 2


def make_review_router():
    """Return a routing function that checks research_review outcome against
    budget and retry limits."""

    def route_after_review(state: ResearchState) -> str:
        es = state.get("execution_state", {})
        knowledge = state.get("knowledge", {})
        step_costs = es.get("step_costs", {})
        review_history = knowledge.get("review_history", [])

        if not review_history:
            return "evaluation"

        latest = review_history[-1]
        route = latest.get("route", "continue")

        if route == "continue":
            logger.info("Research review passed, routing to evaluation")
            return "evaluation"

        if route == "retry":
            review_count = es.get("review_count", 0)
            budget_remaining = es.get("budget_remaining", 0.0)
            rr_cost = review_retry_cost(step_costs)

            # Default limit: 3 attempts (initial + 2 retries within an
            # evaluation cycle). review_count is reset to 0 by the
            # evaluation node when it triggers a retry.
            if review_count < DEFAULT_REVIEW_MAX_ATTEMPTS and budget_remaining >= rr_cost:
                logger.info(
                    "review retry: budget sufficient (%.1f >= %.1f), count=%d/%d",
                    budget_remaining,
                    rr_cost,
                    review_count,
                    DEFAULT_REVIEW_MAX_ATTEMPTS,
                )
                return "research"

            logger.info(
                "review retry declined (count=%d/%d, budget=%.1f, cost=%.1f)",
                review_count,
                DEFAULT_REVIEW_MAX_ATTEMPTS,
                budget_remaining,
                rr_cost,
            )
            # Fall through to evaluation — let evaluation decide
            return "evaluation"

        return "evaluation"

    return route_after_review


def make_evaluation_router():
    """Return a routing function that checks evaluation outcome against
    budget and retry limits."""

    def route_after_evaluation(state: ResearchState) -> str:
        es = state.get("execution_state", {})
        knowledge = state.get("knowledge", {})
        step_costs = es.get("step_costs", {})
        evaluation_history = knowledge.get("evaluation_history", [])

        if not evaluation_history:
            return "report_generation"

        latest = evaluation_history[-1]
        route = latest.get("route", "accept")

        if route == "accept":
            logger.info("Evaluation accepted, routing to report_generation")
            return "report_generation"

        if route == "retry":
            evaluation_count = es.get("evaluation_count", 0)
            budget_remaining = es.get("budget_remaining", 0.0)
            er_cost = evaluation_retry_cost(step_costs)

            # Default limit: 2 attempts (initial + 1 retry).
            if evaluation_count < DEFAULT_EVALUATION_MAX_ATTEMPTS and budget_remaining >= er_cost:
                logger.info(
                    "evaluation retry: budget sufficient (%.1f >= %.1f), count=%d/%d",
                    budget_remaining,
                    er_cost,
                    evaluation_count,
                    DEFAULT_EVALUATION_MAX_ATTEMPTS,
                )
                return "tool_identification"

            logger.info(
                "evaluation retry declined (count=%d/%d, budget=%.1f, cost=%.1f)",
                evaluation_count,
                DEFAULT_EVALUATION_MAX_ATTEMPTS,
                budget_remaining,
                er_cost,
            )
            return "report_generation"

        return "report_generation"

    return route_after_evaluation


def build_graph(config: MoiraConfig) -> StateGraph:
    """Build the LangGraph research workflow with 8 nodes and conditional
    routing after research_review and evaluation."""
    graph = StateGraph(ResearchState)

    graph.add_node("decomposition", decomposition)
    graph.add_node("tool_identification", tool_identification)
    graph.add_node("planning", planning)
    graph.add_node("research", research)
    graph.add_node("synthesis", synthesis)
    graph.add_node("research_review", research_review)
    graph.add_node("evaluation", evaluation)
    graph.add_node("report_generation", report_generation)

    graph.add_edge(START, "decomposition")
    graph.add_edge("decomposition", "tool_identification")
    graph.add_edge("tool_identification", "planning")
    graph.add_edge("planning", "research")
    graph.add_edge("research", "synthesis")
    graph.add_edge("synthesis", "research_review")

    # Conditional routing after research_review
    graph.add_conditional_edges(
        "research_review",
        make_review_router(),
        {
            "research": "research",
            "evaluation": "evaluation",
            "report_generation": "report_generation",
        },
    )

    # Conditional routing after evaluation
    graph.add_conditional_edges(
        "evaluation",
        make_evaluation_router(),
        {
            "tool_identification": "tool_identification",
            "report_generation": "report_generation",
        },
    )

    graph.add_edge("report_generation", END)

    logger.info("Research graph built with 8 nodes")
    return graph


def compile_graph(config: MoiraConfig, checkpointer=None):
    """Build and compile the research graph."""
    graph = build_graph(config)
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("Research graph compiled")
    return compiled
