"""Test-level validation of workflow step detail payloads against the schema.

The schema (backend/moira/schemas/step_detail.schema.json) documents the
``detail`` JSON each workflow node persists into ``workflow_steps.detail``.
Per the design decision there is no validate-at-write enforcement; these
tests pin the contract instead: node-shaped payloads validate, missing
required keys fail, extra keys are tolerated, and the eval capture layer
stays aligned with the schema's tool_result definition.
"""

import pytest

from moira.schemas import NODE_DETAIL_DEFS, load_schema, validate_detail

LLM_BASE = {"prompt": "p", "response": "r", "model": "test-model"}


def _llm_detail(structured_output=None, **extra):
    detail = dict(LLM_BASE)
    if structured_output is not None:
        detail["structured_output"] = structured_output
    detail.update(extra)
    return detail


def _research_detail(**extra):
    detail = {
        "facts_resolved": ["f001"],
        "facts_newly_unknown": ["f002"],
        "evidence_requests_size": 3,
        "executor_available": True,
        "rounds": 2,
        "tool_calling_mode": "native",
        "prompt": "p",
        "model": "test-model",
        "request_attempt_counts": {"req0001": 2},
        "issued_query_count": 4,
        "new_facts": 2,
        "stalled": False,
    }
    detail.update(extra)
    return detail


def _tool_entry(**extra):
    entry = {
        "tool": "web_search",
        "args": {"query": "q"},
        "result": "snippet",
        "duration_ms": 12.0,
        "success": True,
    }
    entry.update(extra)
    return entry


def test_schema_loads_and_every_registered_def_exists():
    schema = load_schema()
    assert "$defs" in schema
    for success_ref, error_refs in NODE_DETAIL_DEFS.values():
        assert success_ref in schema["$defs"], success_ref
        for ref in error_refs:
            assert ref in schema["$defs"], ref


def test_all_workflow_nodes_registered():
    # "verification" is a retired node kept so historical runs sweep clean.
    assert set(NODE_DETAIL_DEFS) == {
        "decomposition",
        "synthesis",
        "tool_identification",
        "planning",
        "research",
        "research_review",
        "evaluation",
        "report_generation",
        "verification",
    }


@pytest.mark.parametrize(
    ("node", "detail", "expected_def"),
    [
        ("decomposition", _llm_detail(structured_output={}), "decomposition_detail"),
        (
            "synthesis",
            _llm_detail(structured_output={"conclusions": []}),
            "synthesis_detail",
        ),
        (
            "tool_identification",
            {
                "queries": [{"fact_id": "f001", "query": "q", "top_results": ["web_search"]}],
                "candidate_tools": ["web_search"],
                "default_tools_included": [],
            },
            "tool_identification_detail",
        ),
        (
            "planning",
            _llm_detail(
                structured_output={
                    "evidence_requests": [
                        {
                            "id": "req0001",
                            "target_fact_ids": ["f001"],
                            "evidence_needed": "cost data",
                            "candidate_tools": ["web_search"],
                            "fallback": False,
                        }
                    ]
                }
            ),
            "planning_detail",
        ),
        ("research", _research_detail(), "research_detail"),
        (
            "research",
            {
                "tool_results": [],
                "facts_resolved": [],
                "facts_newly_unknown": ["f001"],
                "evidence_requests_size": 2,
                "executor_available": False,
                "rounds": 0,
            },
            "research_exit_detail",
        ),
        (
            "research",
            {
                "tool_results": [_tool_entry(output="round log entry")],
                "prompt": "p",
                "response": "",
                "model": "test-model",
                "round": 3,
                "thinking": "t",
            },
            "research_round_error_detail",
        ),
        (
            "research_review",
            _llm_detail(
                structured_output={
                    "fact_results": [{"fact_id": "f001", "result": "verified"}],
                    "coverage_assessment": "good",
                    "missing_areas": [],
                    "route": "continue",
                }
            ),
            "research_review_detail",
        ),
        (
            "evaluation",
            _llm_detail(
                structured_output={
                    "conclusion_results": [],
                    "goal_met": True,
                    "goal_assessment": "answered",
                    "route": "accept",
                }
            ),
            "evaluation_detail",
        ),
        (
            "report_generation",
            {
                "prompt": "p",
                "response": "r",
                "model": "test-model",
                "generation_reason": "budget_exhausted",
                "thinking": "t",
                "raw_response": "unparseable",
            },
            "report_generation_detail",
        ),
        # Legacy minimal shapes (June spike era): reason-only or path-only.
        (
            "report_generation",
            {"generation_reason": "retries_exhausted"},
            "report_generation_legacy_detail",
        ),
        (
            "report_generation",
            {"generation_path": "verified"},
            "report_generation_legacy_detail",
        ),
        # Retired node, kept so historical runs sweep clean.
        (
            "verification",
            _llm_detail(
                structured_output={
                    "fact_results": [],
                    "conclusion_results": [],
                    "new_unknown_facts": [],
                    "goal_met": False,
                    "goal_assessment": "g",
                    "route": "retry",
                },
                tool_results=[],
            ),
            "verification_detail",
        ),
    ],
)
def test_success_shapes_validate(node, detail, expected_def):
    errors, matched = validate_detail(node, detail)
    assert errors == []
    assert matched == expected_def


@pytest.mark.parametrize(
    ("node", "expected_def"),
    [
        ("decomposition", "decomposition_error_detail"),
        ("synthesis", "synthesis_error_detail"),
        ("planning", "planning_error_detail"),
        ("research_review", "research_review_error_detail"),
        ("evaluation", "evaluation_error_detail"),
    ],
)
def test_error_variants_base_keys_only(node, expected_def):
    errors, matched = validate_detail(node, dict(LLM_BASE))
    assert errors == []
    assert matched == expected_def


def test_malformed_structured_output_not_excused_by_error_variant():
    """A payload carrying structured_output must validate against the success
    def, not fall back to the base-keys error variant."""
    detail = _llm_detail(structured_output={"evidence_requests": [{"target_fact_ids": ["f001"]}]})
    errors, matched = validate_detail("planning", detail)
    assert matched is None
    assert any("evidence_needed" in err for err in errors)


def test_progress_signal_keys_required_on_research():
    detail = _research_detail()
    del detail["new_facts"]
    del detail["stalled"]
    errors, matched = validate_detail("research", detail)
    assert matched is None
    assert any("new_facts" in err for err in errors)
    assert any("stalled" in err for err in errors)


def test_extra_keys_are_tolerated():
    detail = _research_detail(mystery_future_key={"a": 1})
    errors, matched = validate_detail("research", detail)
    assert errors == []
    assert matched == "research_detail"


def test_fact_id_pattern_enforced():
    detail = _research_detail(facts_resolved=["not-a-fact-id"])
    errors, _ = validate_detail("research", detail)
    assert any("does not match" in err for err in errors)


def test_request_id_pattern_enforced():
    detail = _research_detail(
        tool_results=[_tool_entry(request_id="bad-id")],
    )
    errors, _ = validate_detail("research", detail)
    assert any("request_id" in err for err in errors)


def test_tool_result_requires_result_or_output():
    detail = _research_detail(tool_results=[_tool_entry()])
    errors, matched = validate_detail("research", detail)
    assert errors == []
    assert matched == "research_detail"

    bad = _tool_entry()
    del bad["result"]
    detail = _research_detail(tool_results=[bad])
    errors, matched = validate_detail("research", detail)
    assert matched is None
    assert any("not valid under any of the given schemas" in err for err in errors)


def test_route_enum_enforced():
    detail = _llm_detail(
        structured_output={
            "fact_results": [],
            "coverage_assessment": "c",
            "missing_areas": [],
            "route": "bogus",
        }
    )
    errors, matched = validate_detail("research_review", detail)
    assert matched is None
    assert any("route" in err for err in errors)


def test_unknown_node_reported():
    errors, matched = validate_detail("future_node", {})
    assert matched is None
    assert any("no schema definition" in err for err in errors)


def test_capture_tool_trace_aligned_with_schema():
    """The eval capture layer must read the same tool_result keys the schema
    documents — 'result' canonically, 'output' on round-error payloads."""

    from moira_eval.capture import _extract_tool_trace

    round_log_entry = _tool_entry()
    del round_log_entry["result"]
    round_log_entry["output"] = "round-log"
    steps = [
        {
            "node_name": "research",
            "label": "Research",
            "detail": {
                "tool_results": [
                    _tool_entry(result="x" * 600, request_id="req0001"),
                    round_log_entry,
                ]
            },
        }
    ]
    trace = _extract_tool_trace(steps)
    assert [t["tool"] for t in trace] == ["web_search", "web_search"]
    assert trace[0]["output_preview"] == "x" * 500
    assert trace[1]["output_preview"] == "round-log"
