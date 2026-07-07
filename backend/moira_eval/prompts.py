"""Judge prompt construction for evaluation.

Builds the system and user messages sent to the frontier-model judge.
The system prompt wraps the rubric categories with their 0/2 anchors and
hard-fail markers. The user message assembles the full evidence payload:
question, report answer, knowledge graph, verification outputs, and
mechanical metrics.

**Critique-quality scoring stance** (Design Decision from
evaluation-harness.md): the judge grades whether verification *did* catch
the weaknesses visible in the run — not whether it *should* have caught
weaknesses the judge identifies independently. The judge sees the agent's
own review/evaluation output and assesses whether it pushed back
appropriately on what was actually weak in that specific run.

**Unsupported != contradicted**: a conclusion that lacks supporting facts
is "unsupported" (weak), not "contradicted" (wrong). The rubric and prompt
preserve this distinction throughout.
"""

import json
from typing import Any

from moira_eval.rubric_pokemon import (
    CATEGORY_DESCRIPTIONS,
    create_empty_scorecard,
)


def _format_category_anchors() -> str:
    """Build the rubric category list with 0/2 score anchors.

    Each category shows its "score 2" (good) and "score 0" (bad) anchor,
    and is marked as [HARD FAIL] if scoring below 2 is an automatic failure.
    Score 1 is implied: partially meets the criterion.
    """
    scorecard = create_empty_scorecard()
    lines: list[str] = []
    for cat in scorecard:
        name = cat.name
        descs = CATEGORY_DESCRIPTIONS[name]
        if cat.hard_fail:
            hard_fail_tag = " [HARD FAIL — scoring below 2 fails the entire run]"
        else:
            hard_fail_tag = ""
        lines.append(f"### {name}{hard_fail_tag}")
        lines.append(f"  - Score 2: {descs[2]}")
        lines.append(
            "  - Score 1: Partially meets the criterion — some issues but directionally correct."
        )
        lines.append(f"  - Score 0: {descs[0]}")
        lines.append("")
    return "\n".join(lines)


def _format_knowledge_section(knowledge: dict | None) -> str:
    """Format the knowledge graph: facts, conclusions, citations.

    The knowledge snapshot has been normalized by ``capture.py`` to flat
    lists. We present it verbatim (pretty-printed JSON) so the judge can
    inspect the citation graph and fact/conclusion integrity.
    """
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
    """Format the review and evaluation outputs from the verification loop.

    This is what the judge uses to score the Verification Quality
    category: did the agent's own review push back on weak claims?
    """
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
    """Format the mechanical metrics for the judge to reference.

    The judge doesn't score these (they're deterministic), but seeing them
    helps calibrate: e.g. high web_search_calls with low
    specialized_tool_use_ratio suggests poor tool routing.
    """
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
    # The report dict has an 'answer' key with the main response text.
    answer = report.get("answer", "")
    if not answer:
        return "(report has no answer field)"
    return answer


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

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

## Rubric categories

"""
    + _format_category_anchors()
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


def build_pokemon_judge_messages(
    question: str,
    artifacts: dict,
    metrics: dict[str, Any],
) -> list[dict[str, str]]:
    """Build the full message list for the Pokemon judge.

    Args:
        question: The benchmark question text.
        artifacts: The artifacts dict from ``capture_artifacts``.
        metrics: The metrics dict from ``compute_metrics``.

    Returns:
        List of ``{"role": ..., "content": ...}`` dicts suitable for
        ``InferenceClient.chat_completion``.
    """
    user_content = f"""\
## Question

{question}

## Agent's Answer

{_format_report_section(artifacts.get("report"))}

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
    return [
        {"role": "system", "content": POKEMON_JUDGE_SYSTEM},
        {"role": "user", "content": user_content},
    ]
