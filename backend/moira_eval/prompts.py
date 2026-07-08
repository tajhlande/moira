"""Judge prompt construction for evaluation.

Builds the system and user messages sent to the frontier-model judge.
Supports two rubrics:

- **Pokemon** (``rubric_pokemon.py``): 8 categories, 0-2 scale, hard-fail
  categories, domain-specific distinctions.
- **General** (``rubric_general.py``): 5 criteria, 1-5 scale, no hard-fail
  categories, domain-agnostic grounding discipline.

Both prompts share the same evidence payload (question, answer, knowledge
graph, verification outputs, metrics, tool catalog) and the same
critique-quality scoring stance.

**Critique-quality scoring stance** (Design Decision from
evaluation-harness.md): the judge grades whether verification *did* catch
the weaknesses visible in the run — not whether it *should* have caught
weaknesses the judge identifies independently.

**Unsupported != contradicted**: a conclusion that lacks supporting facts
is "unsupported" (weak), not "contradicted" (wrong).
"""

import json
from typing import Any

from moira_eval.rubric_general import (
    CATEGORY_DESCRIPTIONS as _GENERAL_DESCRIPTIONS,
)
from moira_eval.rubric_general import (
    HARD_FAIL_CATEGORIES as _GENERAL_HARD_FAIL,
)
from moira_eval.rubric_general import (
    SCALE_MAX as _GENERAL_SCALE_MAX,
)
from moira_eval.rubric_general import create_empty_scorecard as _general_scorecard
from moira_eval.rubric_pokemon import (
    CATEGORY_DESCRIPTIONS as _POKEMON_DESCRIPTIONS,
)
from moira_eval.rubric_pokemon import (
    HARD_FAIL_CATEGORIES as _POKEMON_HARD_FAIL,
)
from moira_eval.rubric_pokemon import (
    SCALE_MAX as _POKEMON_SCALE_MAX,
)
from moira_eval.rubric_pokemon import create_empty_scorecard as _pokemon_scorecard

# ---------------------------------------------------------------------------
# Category anchor formatting (shared)
# ---------------------------------------------------------------------------


def _format_category_anchors(
    scale_max: int,
    hard_fail_categories: set[str],
    create_scorecard,
    descriptions: dict,
) -> str:
    """Build the rubric category list with score anchors.

    Works for any rubric that exports ``SCALE_MAX``, ``HARD_FAIL_CATEGORIES``,
    ``create_empty_scorecard``, and ``CATEGORY_DESCRIPTIONS``.
    """
    scorecard = create_scorecard()
    lines: list[str] = []
    for cat in scorecard:
        name = cat.name
        descs = descriptions[name]
        if name in hard_fail_categories:
            hard_fail_tag = f" [HARD FAIL — scoring below {scale_max} fails the entire run]"
        else:
            hard_fail_tag = ""
        lines.append(f"### {name}{hard_fail_tag}")
        # Build anchor lines from the descriptions dict.
        # Keys are sorted descending so the "good" anchor appears first.
        for score_val in sorted(descs.keys(), reverse=True):
            lines.append(f"  - Score {score_val}: {descs[score_val]}")
        # Fill in implied intermediate scores for the 0-2 rubric.
        if scale_max == 2:
            lines.insert(len(lines) - 1, "  - Score 1: Partially meets the criterion.")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Evidence section formatting (shared)
# ---------------------------------------------------------------------------


def _format_tool_catalog(artifacts: dict) -> str:
    """Format the available tool catalog for the judge.

    Without this list the judge may invent tools it thinks the agent
    should have used. The catalog is captured from the ``tools`` table
    by ``capture.py``.
    """
    catalog = artifacts.get("tool_catalog", [])
    if not catalog:
        return "(tool catalog not captured)"
    lines = [f"### Available Tools ({len(catalog)} total)"]
    for tool in catalog:
        name = tool.get("name", "?")
        desc = tool.get("description", "")
        lines.append(f"- **{name}**: {desc}")
    return "\n".join(lines)


def _format_knowledge_section(knowledge: dict | None) -> str:
    """Format the knowledge graph: facts, conclusions."""
    if not knowledge:
        return "(no knowledge snapshot captured)"

    facts = knowledge.get("facts", [])
    conclusions = knowledge.get("conclusions", [])

    sections: list[str] = []
    sections.append(f"### Facts ({len(facts)} total)")
    if facts:
        sections.append("```json")
        sections.append(json.dumps(facts, indent=2, ensure_ascii=False))
        sections.append("```")
    else:
        sections.append("(none)")

    sections.append(f"\n### Conclusions ({len(conclusions)} total)")
    if conclusions:
        sections.append("```json")
        sections.append(json.dumps(conclusions, indent=2, ensure_ascii=False))
        sections.append("```")
    else:
        sections.append("(none)")

    return "\n".join(sections)


def _format_verification_section(artifacts: dict) -> str:
    """Format the review and evaluation outputs from the verification loop."""
    review = artifacts.get("review_attempts", [])
    evaluation = artifacts.get("evaluation_attempts", [])
    critiques = artifacts.get("critiques", [])

    sections: list[str] = []

    sections.append(f"### Research Review Attempts ({len(review)} total)")
    if review:
        sections.append("```json")
        sections.append(json.dumps(review, indent=2, ensure_ascii=False))
        sections.append("```")
    else:
        sections.append("(no review attempts)")

    sections.append(f"\n### Evaluation Attempts ({len(evaluation)} total)")
    if evaluation:
        sections.append("```json")
        sections.append(json.dumps(evaluation, indent=2, ensure_ascii=False))
        sections.append("```")
    else:
        sections.append("(no evaluation attempts)")

    sections.append(f"\n### Report Critiques ({len(critiques)} total)")
    if critiques:
        for c in critiques:
            sections.append(f"- {c}")
    else:
        sections.append("(no critiques surfaced)")

    return "\n".join(sections)


def _format_metrics_section(metrics: dict[str, Any]) -> str:
    """Format the mechanical metrics for the judge to reference."""
    lines: list[str] = [
        f"- web_search calls: {metrics.get('web_search_calls', 0)}",
        f"- url_content calls: {metrics.get('url_content_calls', 0)}",
        f"- total tool calls: {metrics.get('total_tool_calls', 0)}",
        f"- specialized tool use ratio: {metrics.get('specialized_tool_use_ratio', 0)}",
        f"- unknown facts: {metrics.get('unknown_fact_count', 0)}",
        f"- unverified facts: {metrics.get('unverified_fact_count', 0)}",
        f"- verified facts: {metrics.get('verified_fact_count', 0)}",
        f"- contradicted facts: {metrics.get('contradicted_fact_count', 0)}",
        f"- unsupported conclusions: {metrics.get('unsupported_conclusion_count', 0)}",
        f"- hallucinated fact IDs: {metrics.get('hallucinated_fact_id_count', 0)}",
    ]
    halluc_ids = metrics.get("hallucinated_fact_ids", [])
    if halluc_ids:
        lines.append(f"  hallucinated IDs: {', '.join(halluc_ids)}")
    return "\n".join(lines)


def _format_report_section(report: dict | None) -> str:
    """Extract the answer text from the report for the judge to grade."""
    if not report:
        return "(no report captured)"
    answer = report.get("answer", "")
    if not answer:
        return "(report has no answer field)"
    return answer


def _build_user_message(question: str, artifacts: dict, metrics: dict[str, Any]) -> str:
    """Build the shared user message content for both rubrics."""
    return f"""\
## Question

{question}

## Agent's Answer

{_format_report_section(artifacts.get("report"))}

## Available Tools

{_format_tool_catalog(artifacts)}

## Knowledge Graph (facts, conclusions)

{_format_knowledge_section(artifacts.get("knowledge"))}

## Verification Outputs (review + evaluation)

{_format_verification_section(artifacts)}

## Mechanical Metrics

{_format_metrics_section(metrics)}

---

Grade the agent's work against the rubric. Remember: grade what the agent \
produced, do not re-answer the question. Assess whether verification pushed \
back on what was actually weak in this run. Return the JSON object as \
specified in the system instructions.
"""


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_POKEMON_ANCHORS = _format_category_anchors(
    _POKEMON_SCALE_MAX,
    _POKEMON_HARD_FAIL,
    _pokemon_scorecard,
    _POKEMON_DESCRIPTIONS,
)

POKEMON_JUDGE_SYSTEM = (
    """\
You are an expert evaluator grading a research agent's response to a \
competitive Pokemon (Gen 9 OU) question. You grade the agent's work \
against a structured rubric. You do NOT re-answer the question yourself.

## Scoring rules

Each category is scored 0, 1, or 2:
- **2** = fully meets the criterion
- **1** = partially meets the criterion (some issues, directionally correct)
- **0** = fails the criterion

Categories marked **[HARD FAIL]** must score 2 for the run to pass. \
Scoring below 2 on any hard-fail category fails the entire run.

## Important distinctions

- **Unsupported != contradicted.** A conclusion lacking supporting facts is \
*unsupported* (weak synthesis), not *contradicted* (factually wrong). Do not \
conflate these when grading synthesis discipline.
- **Learnset != typical.** A species can *learn* many moves, but only a subset \
are *typical* in competitive OU. Overclaiming from learnset data alone is a \
synthesis error.
- **Legal != common.** A Pokemon legal in OU is not necessarily common or \
relevant. Treating legality as relevance is an OU-legality error.

## Verification quality stance

You are grading whether the agent's own verification loop (review + evaluation) \
*did* catch the weaknesses visible in this specific run — not whether it *should* \
have caught weaknesses you discover independently. Look at the review_attempts \
and evaluation_attempts in the evidence: did the agent push back on claims that \
were actually weak, or did it rubber-stamp a shaky draft?

## Tool awareness

The "Available Tools" section lists the tools the agent actually had access \
to during this run. Judge tool choice and search discipline relative to what \
was available — do not penalize the agent for not using tools it never had.

## Rubric categories

"""
    + _POKEMON_ANCHORS
    + """
## Output format

Return a single JSON object with this exact structure:

```json
{
  "categories": [
    {"name": "Tool choice", "score": 2, "rationale": "..."},
    {"name": "Search discipline", "score": 1, "rationale": "..."},
    {"name": "Type correctness", "score": 2, "rationale": "..."},
    {"name": "Ability correctness", "score": 2, "rationale": "..."},
    {"name": "Typical-move discipline", "score": 2, "rationale": "..."},
    {"name": "OU legality and metagame", "score": 2, "rationale": "..."},
    {"name": "Synthesis discipline", "score": 1, "rationale": "..."},
    {"name": "Verification quality", "score": 2, "rationale": "..."}
  ],
  "overall_notes": "Brief summary of strengths and weaknesses."
}
```

Score every category (all 8). Do not omit any. Keep rationales concise \
(1-3 sentences each).
"""
)

_GENERAL_ANCHORS = _format_category_anchors(
    _GENERAL_SCALE_MAX,
    _GENERAL_HARD_FAIL,
    _general_scorecard,
    _GENERAL_DESCRIPTIONS,
)

GENERAL_JUDGE_SYSTEM = (
    """\
You are an expert evaluator grading a research agent's response to a \
question. You grade the agent's work against a structured rubric measuring \
grounding and reasoning discipline. You do NOT re-answer the question yourself.

## Scoring rules

Each criterion is scored 1-5:
- **5** = fully meets the criterion
- **3** = partially meets the criterion (some issues, directionally correct)
- **1** = fails the criterion

There are no hard-fail criteria in this rubric.

## Important distinctions

- **Unsupported != contradicted.** A conclusion lacking supporting facts is \
*unsupported* (weak synthesis), not *contradicted* (factually wrong). Do not \
conflate these when grading grounding.
- **Atomic != vague.** A fact like "Python is a popular programming language \
widely used for data science" is compound and judgment-laden. "Python was \
released in 1991" is atomic and verifiable. Prefer the latter.
- **Cited != supported.** A citation existing on a claim does not mean the \
source actually supports that specific claim. Check whether the citation \
matches the assertion.

## Critique quality stance

You are grading whether the agent's own verification loop (review + evaluation) \
*did* catch the weaknesses visible in this specific run — not whether it *should* \
have caught weaknesses you discover independently. Look at the review_attempts \
and evaluation_attempts in the evidence: did the agent push back on claims that \
were actually weak, or did it rubber-stamp a shaky draft?

## Tool awareness

The "Available Tools" section lists the tools the agent actually had access \
to during this run. Judge tool choice and search discipline relative to what \
was available — do not penalize the agent for not using tools it never had.

## Rubric criteria

"""
    + _GENERAL_ANCHORS
    + """
## Output format

Return a single JSON object with this exact structure:

```json
{
  "categories": [
    {"name": "Grounding", "score": 4, "rationale": "..."},
    {"name": "Fact atomicity", "score": 5, "rationale": "..."},
    {"name": "Citation support", "score": 4, "rationale": "..."},
    {"name": "Critique quality", "score": 3, "rationale": "..."},
    {"name": "Goal alignment", "score": 5, "rationale": "..."}
  ],
  "overall_notes": "Brief summary of strengths and weaknesses."
}
```

Score every criterion (all 5). Do not omit any. Keep rationales concise \
(1-3 sentences each).
"""
)


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


def build_judge_messages(
    question: str,
    artifacts: dict,
    metrics: dict[str, Any],
    rubric: str,
) -> list[dict[str, str]]:
    """Build the full message list for the judge.

    Args:
        question: The question text.
        artifacts: The artifacts dict from ``capture_artifacts``.
        metrics: The metrics dict from ``compute_metrics``.
        rubric: Rubric type — ``"pokemon"`` or ``"general"``.

    Returns:
        List of ``{"role": ..., "content": ...}`` dicts.
    """
    if rubric == "pokemon":
        system_prompt = POKEMON_JUDGE_SYSTEM
    elif rubric == "general":
        system_prompt = GENERAL_JUDGE_SYSTEM
    else:
        raise ValueError(f"Unknown rubric type: {rubric!r}")

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": _build_user_message(question, artifacts, metrics)},
    ]
