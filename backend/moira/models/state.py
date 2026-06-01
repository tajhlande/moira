from typing import NotRequired, TypedDict

from moira.tools.base import ToolDefinition


class VerificationReport(TypedDict):
    outcome: str  # "accept", "retry_plan", "retry_draft", or "error"
    case: int  # 1-11 matching the verification prompt cases
    assessment: str
    supported_claims: list[str]
    unsupported_claims: list[str]
    contradictions: list[str]
    relevance: str  # "on_topic" or "off_topic"
    depth: str  # "sufficient" or "too_shallow"
    guidance: str


class Finding(TypedDict):
    content: str
    source: str
    citation_url: NotRequired[str]
    type: str


class ResearchReport(TypedDict):
    answer: str
    citations: list[dict]
    support: list[dict]
    critiques: list[str]
    unverified_claims: list[str]
    # Denormalized convenience copy; authoritative source is
    # workflow_runs.budget_consumed
    budget_consumed: float
    # Why report_generation ran: "verified", "budget_exhausted", or "error"
    generation_path: NotRequired[str]


class ResearchState(TypedDict):
    question: str
    plan: str
    active_tools: list[ToolDefinition]
    findings: list[Finding]
    compressed_findings: list[Finding]
    draft: str
    verification: str
    report: ResearchReport
    budget_remaining: float
    budget_limit: float
    verification_history: list[VerificationReport]
    unverified_claims: list[str]
    # Error tracking for node failure path
    error: str
    # Tracks draft-only retries (max 1). Incremented when verification
    # routes to draft_synthesis instead of planning.
    draft_retry_count: int
