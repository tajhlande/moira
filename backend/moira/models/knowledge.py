"""Knowledge model types for the overhauled research loop.

This module defines the complete state model carried through the research
graph. It replaces the old ResearchState from moira.models.state.

The graph state is a single TypedDict with two sub-objects:
- ``knowledge``: the research artifact (question, facts, conclusions, etc.)
- ``execution_state``: orchestration mechanics (budget, routing, tool plans)

See agent-docs/research-loop-overhaul-plan.md sections 1.1-1.4 for the
full schema specification.
"""

from typing import Annotated, NotRequired, TypedDict

from moira.tools.base import ToolDefinition


def _shallow_merge(old: dict, new: dict) -> dict:
    """LangGraph reducer: shallow-merge node updates into state so partial
    returns don't lose keys set by earlier nodes."""
    return {**old, **new}


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------


class Citation(TypedDict):
    id: str
    source: str
    url: NotRequired[str]
    title: NotRequired[str]
    excerpt: NotRequired[str]


class Fact(TypedDict):
    id: str
    subject: str
    fact_needed: str
    claim: NotRequired[str]
    relation: NotRequired[str]
    value: NotRequired[str]
    citation_ids: NotRequired[list[str]]
    status: str  # "unknown" | "unverified" | "verified" | "contradicted"
    verification_note: NotRequired[str]


class Conclusion(TypedDict):
    id: str
    conclusion: str
    supporting_fact_ids: list[str]
    citation_ids: NotRequired[list[str]]
    status: str  # "unverified" | "verified" | "contradicted"
    reasoning: NotRequired[str]
    verification_note: NotRequired[str]


class ToolCallPlan(TypedDict):
    tool: str
    args: dict
    target_fact_ids: list[str]
    cost: float


class VerificationOutcome(TypedDict):
    fact_results: list[dict]
    conclusion_results: list[dict]
    new_unknown_facts: list[str]
    goal_met: bool
    goal_assessment: str
    route: str  # "accept" | "retry_research" | "retry_synthesis"


# ---------------------------------------------------------------------------
# Knowledge model (research artifact)
# ---------------------------------------------------------------------------


class Knowledge(TypedDict):
    question: str
    user_goal: str
    topic: str
    entities: list[str]
    concepts: list[str]
    facts: list[Fact]
    conclusions: list[Conclusion]
    citations: list[Citation]
    verification_history: list[VerificationOutcome]
    report: NotRequired["ResearchReport"]
    generation_path: NotRequired[str]


# ---------------------------------------------------------------------------
# Execution state (orchestration mechanics)
# ---------------------------------------------------------------------------


class ExecutionState(TypedDict):
    candidate_tools: list[ToolDefinition]
    tool_call_plan: list[ToolCallPlan]
    budget_remaining: float
    budget_limit: float
    step_costs: dict[str, float]
    step_temperatures: dict[str, float]
    tool_costs: dict[str, float]
    tool_call_counts: dict[str, int]
    total_tool_cost_consumed: float
    error: str
    synthesis_retry_count: int
    verification_attempts: int


# ---------------------------------------------------------------------------
# Graph state (top-level)
# ---------------------------------------------------------------------------


class ResearchState(TypedDict):
    knowledge: Annotated[Knowledge, _shallow_merge]
    execution_state: Annotated[ExecutionState, _shallow_merge]


# ---------------------------------------------------------------------------
# Report output (what the user sees)
# ---------------------------------------------------------------------------


class ResearchReport(TypedDict):
    answer: str
    citations: list[dict]
    verified_facts: list[dict]
    verified_conclusions: list[dict]
    contradicted: list[dict]
    unknown_facts: list[dict]
    critiques: list[str]
    total_cost: float
    tool_call_total_cost: float
    generation_path: str


# ---------------------------------------------------------------------------
# ID management
# ---------------------------------------------------------------------------


def next_id(prefix: str, existing_list: list) -> str:
    """Generate the next sequential ID with the given prefix.

    Format: ``{prefix}{NNN}`` — three-digit zero-padded.
    Example: ``next_id("f", [...])`` returns ``"f001"`` for an empty list,
    ``"f013"`` for a list with 12 items.
    """
    return f"{prefix}{len(existing_list) + 1:03d}"
