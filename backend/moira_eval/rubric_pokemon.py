"""Scoring rubric for research loop evaluation.

Each run is scored on 8 categories (0/1/2 each, total 16).
Hard-fail categories must score 2 for a run to count as successful.

Category definitions and pass/fail criteria are derived from the Phase 0
specification in agent-docs/research-loop-overhaul-plan.md.
"""

from dataclasses import dataclass, field


@dataclass
class CategoryScore:
    """Score for a single evaluation category."""

    name: str
    score: int  # 0, 1, or 2
    hard_fail: bool
    notes: str = ""


@dataclass
class RunScore:
    """Aggregate score for a single benchmark run."""

    categories: list[CategoryScore] = field(default_factory=list)
    run_id: str = ""
    model: str = ""
    question: str = ""

    @property
    def total(self) -> int:
        return sum(c.score for c in self.categories)

    @property
    def max_total(self) -> int:
        return len(self.categories) * 2

    @property
    def hard_fail_categories_failed(self) -> list[str]:
        return [c.name for c in self.categories if c.hard_fail and c.score < 2]

    @property
    def passed(self) -> bool:
        """A run passes if no hard-fail category scored below 2."""
        return len(self.hard_fail_categories_failed) == 0


# Maximum score per category (0-2 scale).
SCALE_MAX = 2

# Category names as constants for reference in fixtures and scripts.
CATEGORY_TOOL_CHOICE = "Tool choice"
CATEGORY_SEARCH_DISCIPLINE = "Search discipline"
CATEGORY_TYPE_CORRECTNESS = "Type correctness"
CATEGORY_ABILITY_CORRECTNESS = "Ability correctness"
CATEGORY_TYPICAL_MOVE_DISCIPLINE = "Typical-move discipline"
CATEGORY_OU_LEGALITY = "OU legality and metagame"
CATEGORY_SYNTHESIS_DISCIPLINE = "Synthesis discipline"
CATEGORY_VERIFICATION_QUALITY = "Verification quality"

# Categories where scoring below 2 is a hard failure.
HARD_FAIL_CATEGORIES = {
    CATEGORY_TYPE_CORRECTNESS,
    CATEGORY_ABILITY_CORRECTNESS,
    CATEGORY_OU_LEGALITY,
    CATEGORY_VERIFICATION_QUALITY,
}


def create_empty_scorecard() -> list[CategoryScore]:
    """Return a scorecard with all 8 categories initialized to 0."""

    categories = [
        CATEGORY_TOOL_CHOICE,
        CATEGORY_SEARCH_DISCIPLINE,
        CATEGORY_TYPE_CORRECTNESS,
        CATEGORY_ABILITY_CORRECTNESS,
        CATEGORY_TYPICAL_MOVE_DISCIPLINE,
        CATEGORY_OU_LEGALITY,
        CATEGORY_SYNTHESIS_DISCIPLINE,
        CATEGORY_VERIFICATION_QUALITY,
    ]
    return [
        CategoryScore(
            name=name,
            score=0,
            hard_fail=name in HARD_FAIL_CATEGORIES,
        )
        for name in categories
    ]


CATEGORY_DESCRIPTIONS = {
    CATEGORY_TOOL_CHOICE: {
        2: "Pokemon-specific tools used first for species, abilities, moves, legality",
        0: "web_search used first for facts specialized tools should cover",
    },
    CATEGORY_SEARCH_DISCIPLINE: {
        2: "Generic search limited, only for facts structured tools don't cover",
        0: "Most effort on generic search",
    },
    CATEGORY_TYPE_CORRECTNESS: {
        2: "No material type matchup errors",
        0: "Recommendation depends on incorrect type claim",
    },
    CATEGORY_ABILITY_CORRECTNESS: {
        2: "Abilities correctly attributed and described",
        0: "Wrong ability, wrong description, or niche treated as typical",
    },
    CATEGORY_TYPICAL_MOVE_DISCIPLINE: {
        2: "Distinguishes learnset from typical OU moves",
        0: "Overclaims from species data alone",
    },
    CATEGORY_OU_LEGALITY: {
        2: "Separates legal from credible/common in OU",
        0: "Treats legality as relevance",
    },
    CATEGORY_SYNTHESIS_DISCIPLINE: {
        2: "Synergy claims are modest and fact-backed",
        0: "Jumps from isolated facts to broad team advice",
    },
    CATEGORY_VERIFICATION_QUALITY: {
        2: "Flags weak support, contradictions, overreach",
        0: "Rubber-stamps a draft with shaky claims",
    },
}

CANARY_QUESTION = (
    "What Pokemon synergize well with Tyranitar in Gen9 OU? Please pay special "
    "attention to verifying the type strengths and weaknesses, typical abilities, "
    "typical moves, and OU eligibility."
)
