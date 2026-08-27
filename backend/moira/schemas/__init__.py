"""JSON Schema definitions and validation for workflow step detail payloads.

Every workflow node writes a ``detail`` JSON blob into ``workflow_steps.detail``
(see ``step_detail.schema.json``). This module is the single place that maps a
node name to the schema definition that documents its payload, and provides
``validate_detail`` for test-level and offline batch validation.

Per the design decision, payloads are NOT validated at write time — the schema
documents intent and catches drift in tests and offline forensics. That is why
``additionalProperties`` stays true everywhere: unknown keys are tolerated, but
a missing required key fails validation.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

_SCHEMA_PATH = Path(__file__).parent / "step_detail.schema.json"

# Maps node_name -> (success def, [error-variant defs]).
# Validation picks the first def under which the payload validates cleanly;
# if none does, the success-def errors are reported. research has two
# legitimate short-circuit/error shapes (budget exit, round error) alongside
# the full payload; the LLM nodes write the same base keys when a run errors
# before structured output is produced.
NODE_DETAIL_DEFS: dict[str, tuple[str, list[str]]] = {
    "decomposition": ("decomposition_detail", ["decomposition_error_detail"]),
    "synthesis": ("synthesis_detail", ["synthesis_error_detail"]),
    "tool_identification": ("tool_identification_detail", []),
    "planning": ("planning_detail", ["planning_error_detail"]),
    "research": (
        "research_detail",
        ["research_exit_detail", "research_round_error_detail"],
    ),
    "research_review": ("research_review_detail", ["research_review_error_detail"]),
    "evaluation": ("evaluation_detail", ["evaluation_error_detail"]),
    "report_generation": (
        "report_generation_detail",
        ["report_generation_legacy_detail"],
    ),
    # Retired node — kept so historical runs sweep clean.
    "verification": ("verification_detail", []),
}


@lru_cache(maxsize=1)
def load_schema() -> dict:
    """Load and cache the step-detail schema document."""
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _validator_for(ref: str) -> Draft202012Validator:
    schema = load_schema()
    return Draft202012Validator(
        {
            "$ref": f"#/$defs/{ref}",
            "$defs": schema["$defs"],
        }
    )


def _iter_errors(validator: Draft202012Validator, detail: dict) -> list[str]:
    return [
        f"{list(err.absolute_path) or '<root>'}: {err.message}"
        for err in validator.iter_errors(detail)
    ]


def validate_detail(node_name: str, detail: dict) -> tuple[list[str], str | None]:
    """Validate a step detail payload against its node's schema.

    Returns (errors, matched_def). ``errors`` is empty when the payload
    matches the success def or one of the error-variant defs; ``matched_def``
    names the definition it validated against (useful when a node has several
    legitimate shapes). Unknown nodes validate against nothing and are
    reported as such so new nodes can't bypass the schema silently.
    """
    if node_name not in NODE_DETAIL_DEFS:
        return ([f"<root>: no schema definition registered for node '{node_name}'"], None)

    success_ref, error_refs = NODE_DETAIL_DEFS[node_name]
    success_validator = _validator_for(success_ref)
    success_errors = _iter_errors(success_validator, detail)
    if not success_errors:
        return ([], success_ref)

    for ref in error_refs:
        validator = _validator_for(ref)
        errors = _iter_errors(validator, detail)
        if not errors:
            return ([], ref)

    # Nothing matched cleanly: report the success-def errors with matched=None
    # so callers can distinguish "valid (some variant)" from "invalid".
    return (success_errors, None)
