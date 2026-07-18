"""Shared helpers and canned model responses for node unit tests.

Imported explicitly by individual test files as needed.
"""

import json
from typing import cast

from langgraph.graph.state import RunnableConfig

from moira.models.knowledge import ResearchState
from moira.service_setup import _services


def _inject_services(config, mock_model):
    """Reset ``_services`` and inject config + mock model registry."""
    _services.clear()
    _services["config"] = config
    _services["model_registry"] = mock_model["registry"]


def _make_run_config(config) -> RunnableConfig:
    """Wrap config into the LangGraph runnable-config dict."""
    return {"configurable": {"moira_config": config}}


def _build_state(config, question="Test question", facts=None, conclusions=None) -> ResearchState:
    """Build the canonical research-loop state dict used by every node test."""
    cw = config.budget.cost_weights
    step_costs = {
        "decomposition": cw.decomposition,
        "tool_identification": cw.tool_identification,
        "planning": cw.planning,
        "research": cw.research,
        "synthesis": cw.synthesis,
        "research_review": cw.research_review,
        "evaluation": cw.evaluation,
        "report_generation": cw.report_generation,
    }
    return cast(
        ResearchState,
        {
            "knowledge": {
                "question": question,
                "user_goal": "",
                "topic": "",
                "entities": [],
                "concepts": [],
                "facts": facts or [],
                "conclusions": conclusions or [],
                "citations": [],
                "review_history": [],
                "evaluation_history": [],
            },
            "execution_state": {
                "candidate_tools": [],
                "tool_call_plan": [],
                "budget_remaining": float(config.budget.default_limit),
                "budget_limit": float(config.budget.default_limit),
                "step_costs": step_costs,
                "tool_costs": {},
                "tool_call_limits": {},
                "tool_call_step_limits": {},
                "tool_call_counts": {},
                "total_tool_cost_consumed": 0.0,
                "error": "",
                "research_retry_count": 0,
                "research_count": 0,
                "review_count": 0,
                "evaluation_count": 0,
                "retry_limits": {"max_review": 3, "max_evaluation": 2},
            },
        },
    )


# ---------------------------------------------------------------------------
# Canned model JSON responses
# ---------------------------------------------------------------------------

DECOMPOSITION_RESPONSE = json.dumps(
    {
        "user_goal": "Find information about test topic",
        "topic": "testing",
        "entities": ["entity1", "entity2"],
        "concepts": ["concept1"],
        "unknown_facts": [
            {"subject": "entity1", "fact_needed": "fact about entity1"},
            {"subject": "entity2", "fact_needed": "fact about entity2"},
        ],
    }
)

PLANNING_RESPONSE = json.dumps(
    {
        "calls": [
            {
                "tool": "web_search",
                "args": {"query": "entity1 fact"},
                "target_fact_ids": ["f001"],
                "rationale": "Search for entity1 information",
            },
            {
                "tool": "web_search",
                "args": {"query": "entity2 fact"},
                "target_fact_ids": ["f002"],
                "rationale": "Search for entity2 information",
            },
        ],
    }
)

SYNTHESIS_RESPONSE = json.dumps(
    {
        "conclusions": [
            {
                "conclusion": "Entity1 has been confirmed",
                "supporting_fact_ids": ["f001"],
                "reasoning": "Based on verified data",
            },
        ],
    }
)

REVIEW_RESPONSE = json.dumps(
    {
        "fact_results": [
            {"fact_id": "f001", "result": "verified", "evidence": "Confirmed by source"},
        ],
        "coverage_assessment": "Research sufficiently covered the question",
        "missing_areas": [],
        "route": "continue",
    }
)

REVIEW_RETRY_RESPONSE = json.dumps(
    {
        "fact_results": [
            {"fact_id": "f001", "result": "contradicted", "evidence": "Wrong"},
        ],
        "coverage_assessment": "Missing critical info",
        "missing_areas": ["Need more data on X"],
        "route": "retry",
    }
)

EVALUATION_RESPONSE = json.dumps(
    {
        "conclusion_results": [
            {"conclusion_id": "c001", "result": "verified", "reason": "Supported by facts"},
        ],
        "goal_met": True,
        "goal_assessment": "Goal fully met",
        "route": "accept",
    }
)

EVALUATION_RETRY_RESPONSE = json.dumps(
    {
        "conclusion_results": [
            {"conclusion_id": "c001", "result": "contradicted", "reason": "Based on wrong fact"},
        ],
        "goal_met": False,
        "goal_assessment": "Conclusions flawed",
        "route": "retry",
    }
)

REPORT_RESPONSE = json.dumps(
    {
        "answer": "Entity1 is confirmed based on research. [1]",
        "citations": [],
        "verified_facts": [],
        "verified_conclusions": [],
        "contradicted": [],
        "unknown_facts": [],
        "critiques": [],
        "total_cost": 0,
        "tool_call_total_cost": 0,
        "generation_reason": "verified",
    }
)

REPORT_RESPONSE_NO_CITATIONS = json.dumps(
    {
        "answer": "Entity1 is confirmed based on research.",
        "citations": [],
        "verified_facts": [],
        "verified_conclusions": [],
        "contradicted": [],
        "unknown_facts": [],
        "critiques": [],
        "total_cost": 0,
        "tool_call_total_cost": 0,
        "generation_reason": "verified",
    }
)
