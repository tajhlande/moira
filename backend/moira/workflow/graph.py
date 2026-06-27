"""Research workflow graph for the overhauled research loop.

Nodes: decomposition -> tool_identification -> planning -> research ->
synthesis -> research_review -> [conditional] -> evaluation ->
[conditional] -> report_generation -> END

Research review routes:
- continue -> evaluation
- retry -> planning (re-plan for remaining unknowns, then research)
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
    estimated_tool_cost_per_research,
    evaluation_retry_cost,
    get_node_cost,
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


def make_review_router():
    """Return a routing function that checks research_review outcome against
    budget and retry limits.

    Reads ``retry_limits`` from ``execution_state`` (populated from system
    settings at run start). Falls back to 3 if absent.
    """

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
            # Reserve enough budget for evaluation after the retry cycle,
            # so the retry doesn't consume the last of the budget and
            # leave evaluation unable to run.
            eval_cost = get_node_cost(step_costs, "evaluation")
            # Also reserve an estimate for tool-call costs that research
            # will incur on top of its base step cost.  Without this, the
            # router under-counts and may start a cycle that can't finish.
            tool_cost_estimate = estimated_tool_cost_per_research(
                es.get("total_tool_cost_consumed", 0.0),
                es.get("research_count", 0),
            )
            threshold = rr_cost + eval_cost + tool_cost_estimate
            max_review = es.get("retry_limits", {}).get("max_review", 3)

            if review_count < max_review and budget_remaining >= threshold:
                logger.info(
                    "review retry: budget sufficient (%.1f >= %.1f), count=%d/%d",
                    budget_remaining,
                    threshold,
                    review_count,
                    max_review,
                )
                return "planning"

            logger.info(
                "review retry declined (count=%d/%d, budget=%.1f, threshold=%.1f)",
                review_count,
                max_review,
                budget_remaining,
                threshold,
            )
            # Fall through to evaluation — let evaluation decide
            return "evaluation"

        return "evaluation"

    return route_after_review


def make_evaluation_router():
    """Return a routing function that checks evaluation outcome against
    budget and retry limits.

    Reads ``retry_limits`` from ``execution_state`` (populated from system
    settings at run start). Falls back to 2 if absent.
    """

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
            # Add estimated tool-call costs that research will incur on
            # top of its base step cost.  Without this, the router may
            # allow a retry cycle that starves evaluation.
            tool_cost_estimate = estimated_tool_cost_per_research(
                es.get("total_tool_cost_consumed", 0.0),
                es.get("research_count", 0),
            )
            threshold = er_cost + tool_cost_estimate
            max_evaluation = es.get("retry_limits", {}).get("max_evaluation", 2)

            if evaluation_count < max_evaluation and budget_remaining >= threshold:
                logger.info(
                    "evaluation retry: budget sufficient (%.1f >= %.1f), count=%d/%d",
                    budget_remaining,
                    threshold,
                    evaluation_count,
                    max_evaluation,
                )
                return "tool_identification"

            logger.info(
                "evaluation retry declined (count=%d/%d, budget=%.1f, threshold=%.1f)",
                evaluation_count,
                max_evaluation,
                budget_remaining,
                threshold,
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
            "planning": "planning",
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
