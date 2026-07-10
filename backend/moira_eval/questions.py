"""Benchmark question set for evaluation.

Each question declares its rubric type ("pokemon" or "general") so the
runner knows which judge prompt and scale to use.

The question set is designed to exercise different failure modes identified
in ``agent-docs/claude-assessment.md``: tool-routing/search discipline,
synthesis overreach, grounding under source ambiguity, and multi-entity
decomposition.  See the notes on each question for the specific failure
mode it targets.
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
    "flaming-hot-cheetos": Question(
        id="flaming-hot-cheetos",
        text="How were flaming hot Cheetos created?",
        rubric="general",
        tags=["search-discipline", "competing-narratives"],
        notes=(
            "Creator's narrative and corporate narrative differ, "
            "but evidence preponderates one way"
        ),
    ),
    "jazz-trumpeters": Question(
        id="jazz-trumpeters",
        text="Who are the most influential jazz trumpeters of the 1960s?",
        rubric="general",
        tags=["consensus-opinion", "information-availability"],
        notes="Some artists are hard to find information",
    ),
    # ------------------------------------------------------------------
    # Benchmark questions targeting specific failure modes
    # (selected from recent workflow runs based on claude-assessment.md)
    # ------------------------------------------------------------------
    "telescope-mount-cost": Question(
        id="telescope-mount-cost",
        text=(
            "Why are telescopes with equatorial mounts so much more expensive "
            "than telescopes with alt-azimuth mounts?"
        ),
        rubric="general",
        tags=["search-discipline", "tool-routing", "niche-technical"],
        notes=(
            "Run 8a876feb: 22 web_search calls, 1/9 facts verified. "
            "Classic tool-routing failure — agent burned budget on generic "
            "search instead of finding specific technical specs."
        ),
    ),
    "trade-policy-manufacturing": Question(
        id="trade-policy-manufacturing",
        text=("How does international trade policy affect domestic manufacturing employment?"),
        rubric="general",
        tags=["synthesis-overreach", "causal-reasoning", "economics"],
        notes=(
            "Causal economic claim where correlation != causation. "
            "Tests synthesis discipline — easy to overreach from "
            "correlational data."
        ),
    ),
    "future-nostalgia": Question(
        id="future-nostalgia",
        text=(
            "Investigate the psychological phenomenon of 'Future Nostalgia' "
            "— the experience of feeling nostalgic for a time or moment that "
            "hasn't yet occurred. Provide a thorough investigation into its "
            "possible neurological underpinnings, historical precedents, "
            "potential psychological effects, cultural manifestations, and "
            "implications for future well-being. Clearly characterize your "
            "sources, distinguishing between peer-reviewed scientific "
            "literature, credible cultural analyses, historical accounts, "
            "and speculative hypotheses."
        ),
        rubric="general",
        tags=["grounding-discipline", "source-ambiguity", "speculative"],
        notes=(
            "Speculative topic that blurs science and speculation. "
            "Demands source characterization — directly tests citation "
            "support and fact atomicity under source ambiguity."
        ),
    ),
    "water-blood-pressure": Question(
        id="water-blood-pressure",
        text=("How does daily water consumption correlate with blood pressure levels in adults?"),
        rubric="general",
        tags=["medical-grounding", "health-science"],
        notes=(
            "Clean health-science question. Tests medical grounding "
            "precision — health claims need strong support and precise "
            "characterization of study findings."
        ),
    ),
}


def get_question(question_id: str) -> Question | None:
    """Look up a question by ID. Returns ``None`` if not found."""
    return QUESTIONS.get(question_id)
