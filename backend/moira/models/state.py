from typing import NotRequired, TypedDict


class VerificationReport(TypedDict):
    failed_claims: list[str]
    contradictions: list[str]
    missing_evidence: list[str]
    suggestions: list[str]


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
    budget_consumed: float


class ResearchState(TypedDict):
    question: str
    plan: str
    # active_tools is untyped because the tool representation has not yet
    # been defined. When the tool catalog is implemented, this should be
    # list[Tool] (or whatever the canonical tool type is named).
    active_tools: list
    findings: list[Finding]
    compressed_findings: list[Finding]
    draft: str
    verification: str
    report: ResearchReport
    budget_remaining: float
    budget_limit: float
    verification_history: list[VerificationReport]
    unverified_claims: list[str]
