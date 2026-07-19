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
    excerpt: NotRequired[str]  # primary snippet (first search result)
    snippets: NotRequired[list[str]]  # all snippets from different searches
    # Source text available to the research model (formatted tool output per
    # citation). Populated in research; used for cross-referencing in review
    # and evaluation.
    content: NotRequired[str]


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
    # True when research_review supplied a corrected_claim that flipped this
    # fact from "contradicted" to "verified". Lets downstream nodes and the UI
    # surface that the claim was refined during review.
    corrected: NotRequired[bool]


class Conclusion(TypedDict):
    id: str
    conclusion: str
    supporting_fact_ids: list[str]
    citation_ids: NotRequired[list[str]]
    status: str  # "unverified" | "verified" | "contradicted" | "unsupported"
    reasoning: NotRequired[str]
    verification_note: NotRequired[str]


class ToolCallPlan(TypedDict):
    tool: str
    args: dict
    target_fact_ids: list[str]
    cost: float


class ReviewOutcome(TypedDict):
    """Outcome of the research_review node — evaluates evidence coverage."""

    fact_results: list[dict]  # [{fact_id, result, evidence}]
    coverage_assessment: str
    missing_areas: list[str]
    route: str  # "continue" | "retry"


class EvaluationOutcome(TypedDict):
    """Outcome of the evaluation node — evaluates conclusion logic and goal."""

    conclusion_results: list[dict]  # [{conclusion_id, result, reason}]
    goal_met: bool
    goal_assessment: str
    route: str  # "accept" | "retry"


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
    review_history: list[ReviewOutcome]
    evaluation_history: list[EvaluationOutcome]
    report: NotRequired["ResearchReport"]
    generation_reason: NotRequired[str]


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
    tool_call_limits: dict[str, int]
    tool_call_step_limits: dict[str, int]
    tool_call_counts: dict[str, int]
    total_tool_cost_consumed: float
    error: str
    research_retry_count: int
    research_count: int
    review_count: int
    evaluation_count: int
    retry_limits: dict[str, int]


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
    uncited_sources: list[dict]
    verified_facts: list[dict]
    verified_conclusions: list[dict]
    contradicted: list[dict]
    unknown_facts: list[dict]
    critiques: list[str]
    total_cost: float
    tool_call_total_cost: float
    generation_reason: str
    omitted_conclusions: list[dict]


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


def knowledge_summary(knowledge: Knowledge) -> dict:
    """Produce a JSON-serializable summary of the knowledge model.

    Intended for inclusion in run snapshots and the knowledge API endpoint.
    Facts are grouped by subject, conclusions by status, with full structured
    fields for the KnowledgePanel to render.
    """
    facts_by_subject: dict[str, list[dict]] = {}
    for f in knowledge.get("facts", []):
        subject = f.get("subject", "(unspecified)")
        facts_by_subject.setdefault(subject, []).append(
            {
                "id": f["id"],
                "subject": subject,
                "fact_needed": f.get("fact_needed", ""),
                "claim": f.get("claim", ""),
                "relation": f.get("relation"),
                "value": f.get("value"),
                "status": f.get("status", "unknown"),
                "verification_note": f.get("verification_note"),
                "citation_ids": f.get("citation_ids", []),
                "corrected": f.get("corrected", False),
            }
        )

    conclusions_by_status: dict[str, list[dict]] = {}
    for c in knowledge.get("conclusions", []):
        status = c.get("status", "unverified")
        conclusions_by_status.setdefault(status, []).append(
            {
                "id": c["id"],
                "conclusion": c.get("conclusion", ""),
                "supporting_fact_ids": c.get("supporting_fact_ids", []),
                "reasoning": c.get("reasoning"),
                "status": status,
            }
        )

    return {
        "question": knowledge.get("question", ""),
        "user_goal": knowledge.get("user_goal", ""),
        "topic": knowledge.get("topic", ""),
        "entities": knowledge.get("entities", []),
        "concepts": knowledge.get("concepts", []),
        "facts": facts_by_subject,
        "conclusions": conclusions_by_status,
        "citations": [
            {
                "id": c["id"],
                "source": c.get("source", ""),
                "url": c.get("url"),
                "title": c.get("title"),
                "excerpt": c.get("excerpt"),
                "snippets": c.get("snippets"),
                # Content is capped at 5000 chars upstream (research.py
                # _CITATION_CONTENT_LIMIT and url_content.py
                # _METADATA_CONTENT_LENGTH). The [:5000] here is a safety net
                # so post-hoc analysis can see what the reviewer saw without
                # risking unbounded growth in the persisted snapshot.
                "content": (c.get("content") or "")[:5000],
            }
            for c in knowledge.get("citations", [])
        ],
    }
