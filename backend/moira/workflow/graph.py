import logging

from langgraph.graph import END, START, StateGraph

from moira.config import MoiraConfig
from moira.models.state import ResearchState
from moira.workflow.budget import draft_retry_cost, full_cycle_cost
from moira.workflow.nodes.research_nodes import (
    draft_synthesis,
    planning,
    report_generation,
    research_execution,
    tool_discovery,
    tool_selection,
    verification,
)

logger = logging.getLogger(__name__)


def make_verification_router():
    """Return a routing function that checks verification outcome against
    budget. Reads cost_weights from state so the router uses the same
    resolved values as the nodes themselves."""

    def route_after_verification(state: ResearchState) -> str:
        """Conditional routing after the Verification node based on the
        verification report's explicit outcome field:
        - "accept" -> report_generation
        - "error" -> report_generation (with error flag set in state)
        - "retry_draft" + budget sufficient + retries remaining -> draft_synthesis
        - "retry_plan" + budget sufficient -> planning
        - fallback: report_generation
        """
        cost_weights = state.get("cost_weights", {})
        verification_history = state.get("verification_history", [])
        if not verification_history:
            return "report_generation"

        latest = verification_history[-1]
        outcome = latest.get("outcome", "retry_plan")

        if outcome == "accept":
            logger.info(
                "Verification accepted (case %d), routing to report_generation",
                latest.get("case"),
            )
            return "report_generation"

        if outcome == "error":
            logger.info(
                "Verification error (case %d), routing to report_generation",
                latest.get("case"),
            )
            return "report_generation"

        if outcome == "retry_draft":
            budget_remaining = state.get("budget_remaining", 0.0)
            draft_retries = state.get("draft_retry_count", 0)
            dr_cost = draft_retry_cost(cost_weights)
            if draft_retries < 1 and budget_remaining >= dr_cost:
                logger.info(
                    "Verification retry_draft (case %d), budget sufficient"
                    " (%.1f >= %d), routing to draft_synthesis",
                    latest.get("case"),
                    budget_remaining,
                    dr_cost,
                )
                return "draft_synthesis"
            # Fall through to retry_plan or report
            logger.info(
                "Verification retry_draft declined (retries=%d, budget=%.1f), falling through",
                draft_retries,
                budget_remaining,
            )

        # outcome == "retry_plan" or retry_draft fallback
        budget_remaining = state.get("budget_remaining", 0.0)
        cycle_cost = full_cycle_cost(cost_weights)
        if budget_remaining >= cycle_cost:
            logger.info(
                "Verification retry_plan (case %d), budget sufficient"
                " (%.1f >= %d), routing to planning",
                latest.get("case"),
                budget_remaining,
                cycle_cost,
            )
            return "planning"

        logger.info(
            "Verification retry_plan (case %d), budget insufficient (%.1f < %d),"
            " routing to report_generation",
            latest.get("case"),
            budget_remaining,
            cycle_cost,
        )
        return "report_generation"

    return route_after_verification


def route_after_error(state: ResearchState) -> str:
    """If a node wrote an error to state, route directly to report_generation."""
    if state.get("error"):
        return "report_generation"
    return "continue"


def build_graph(config: MoiraConfig) -> StateGraph:
    """Build the LangGraph research workflow with 7 active nodes and
    conditional routing. Compression is bypassed (findings flow directly
    from research_execution to draft_synthesis). Returns an uncompiled
    StateGraph."""
    graph = StateGraph(ResearchState)

    # 7 active nodes (compression bypassed)
    graph.add_node("planning", planning)
    graph.add_node("tool_discovery", tool_discovery)
    graph.add_node("tool_selection", tool_selection)
    graph.add_node("research_execution", research_execution)
    graph.add_node("draft_synthesis", draft_synthesis)
    graph.add_node("verification", verification)
    graph.add_node("report_generation", report_generation)

    # Linear path: START -> Planning -> ToolDiscovery -> ToolSelection ->
    # ResearchExecution -> DraftSynthesis -> Verification
    graph.add_edge(START, "planning")
    graph.add_edge("planning", "tool_discovery")
    graph.add_edge("tool_discovery", "tool_selection")
    graph.add_edge("tool_selection", "research_execution")
    graph.add_edge("research_execution", "draft_synthesis")
    graph.add_edge("draft_synthesis", "verification")

    # Conditional routing after Verification
    graph.add_conditional_edges(
        "verification",
        make_verification_router(),
        {
            "planning": "planning",
            "draft_synthesis": "draft_synthesis",
            "report_generation": "report_generation",
        },
    )

    # ReportGeneration is the terminal node
    graph.add_edge("report_generation", END)

    logger.info("Research graph built with 7 nodes")
    return graph


def compile_graph(config: MoiraConfig, checkpointer=None):
    """Build and compile the research graph. Optionally provides a
    checkpointer for state persistence."""
    graph = build_graph(config)
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("Research graph compiled")
    return compiled
