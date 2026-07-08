"""Benchmark question set for evaluation.

Each question declares its rubric type ("pokemon" or "general") so the
runner knows which judge prompt and scale to use.

The starter set ships 4 questions. ``tyranitar-ou`` is the existing
canary. The other three are placeholders — their text needs user input
(see ``agent-docs/QUESTIONS.md``). Placeholders are marked with
``placeholder=True`` so the runner can warn if used.
"""

from dataclasses import dataclass, field

from moira_eval.rubric_pokemon import CANARY_QUESTION


@dataclass
class Question:
    """A benchmark question with its rubric assignment.

    Attributes:
        id: Stable identifier used in ``--question-id`` and result filenames.
        text: The question text presented to the agent and judge.
        rubric: Rubric type — ``"pokemon"`` or ``"general"``.
        tags: Free-form labels for filtering and grouping.
        notes: Implementation or usage notes.
        placeholder: If True, the question text is a placeholder awaiting
            user input. The runner warns when scoring a placeholder.
    """

    id: str
    text: str
    rubric: str
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    placeholder: bool = False


QUESTIONS: dict[str, Question] = {
    "tyranitar-ou": Question(
        id="tyranitar-ou",
        text=CANARY_QUESTION,
        rubric="pokemon",
        tags=["pokemon", "gen9-ou"],
        notes="Existing canary from rubric_pokemon.py",
    ),
    "oversearch-bait": Question(
        id="oversearch-bait",
        text=(
            "[PLACEHOLDER] A question where the agent historically burned "
            "web_search instead of using specialized tools. "
            "Needs user input — see agent-docs/QUESTIONS.md."
        ),
        rubric="general",
        tags=["search-discipline", "tool-routing"],
        placeholder=True,
    ),
    "synthesis-trap": Question(
        id="synthesis-trap",
        text=(
            "[PLACEHOLDER] A question where individually-true facts tempt "
            "an unsupported conclusion. "
            "Needs user input — see agent-docs/QUESTIONS.md."
        ),
        rubric="general",
        tags=["synthesis-overreach", "verification"],
        placeholder=True,
    ),
    "multi-entity": Question(
        id="multi-entity",
        text=(
            "[PLACEHOLDER] A question requiring many named entities with "
            "interacting facts. "
            "Needs user input — see agent-docs/QUESTIONS.md."
        ),
        rubric="general",
        tags=["decomposition", "id-discipline"],
        placeholder=True,
    ),
}


def get_question(question_id: str) -> Question | None:
    """Look up a question by ID. Returns ``None`` if not found."""
    return QUESTIONS.get(question_id)
