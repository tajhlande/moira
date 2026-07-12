"""General-purpose scoring rubric for research loop evaluation.

Five criteria derived from ``claude-assessment.md``, each scored 1-5
(total 5-25).

Pass/fail policy (all three must pass):

- **Category threshold**: any single category scoring 2/5 or below
  triggers a hard fail. A run with one broken dimension is not
  acceptable even if the overall total is high.
- **Minimum total**: a total below 15/25 triggers a hard fail. This
  catches uniformly mediocre runs that avoid the category threshold
  but don't excel anywhere.
- **Named hard-fail categories**: none (the general rubric does not
  designate any individual category as mandatory-perfect).

The five criteria:
- **Grounding** — conclusions follow from cited facts
- **Fact atomicity** — facts are atomic, not judgment-laden
- **Citation support** — citations actually support their claims
- **Critique quality** — verification identifies real weaknesses
- **Goal alignment** — the answer addresses the user's actual question

Mirrors the dataclass shape of ``rubric_pokemon.py`` so the judge and
runner are rubric-agnostic.
"""

from dataclasses import dataclass, field


@dataclass
class CategoryScore:
    """Score for a single evaluation category."""

    name: str
    score: int  # 1-5
    hard_fail: bool  # always False for general rubric
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
        return len(self.categories) * SCALE_MAX

    @property
    def hard_fail_categories_failed(self) -> list[str]:
        return [c.name for c in self.categories if c.hard_fail and c.score < SCALE_MAX]

    @property
    def passed(self) -> bool:
        """General rubric passes if no category is ≤2 and total ≥15."""
        if any(c.score <= HARD_FAIL_CATEGORY_MAX for c in self.categories):
            return False
        return self.total >= HARD_FAIL_MIN_TOTAL


# Maximum score per category (1-5 scale).
SCALE_MAX = 5

# Any category at or below this score triggers a hard fail.
HARD_FAIL_CATEGORY_MAX = 2

# Total score below this triggers a hard fail.
HARD_FAIL_MIN_TOTAL = 15

# Category names as constants.
CATEGORY_GROUNDING = "Grounding"
CATEGORY_FACT_ATOMICITY = "Fact atomicity"
CATEGORY_CITATION_SUPPORT = "Citation support"
CATEGORY_CRITIQUE_QUALITY = "Critique quality"
CATEGORY_GOAL_ALIGNMENT = "Goal alignment"

# No named hard-fail categories in the general rubric; pass/fail is
# driven by HARD_FAIL_CATEGORY_MAX and HARD_FAIL_MIN_TOTAL instead.
HARD_FAIL_CATEGORIES: set[str] = set()


def create_empty_scorecard() -> list[CategoryScore]:
    """Return a scorecard with all 5 categories initialized to 0."""
    categories = [
        CATEGORY_GROUNDING,
        CATEGORY_FACT_ATOMICITY,
        CATEGORY_CITATION_SUPPORT,
        CATEGORY_CRITIQUE_QUALITY,
        CATEGORY_GOAL_ALIGNMENT,
    ]
    return [CategoryScore(name=name, score=0, hard_fail=False) for name in categories]


CATEGORY_DESCRIPTIONS = {
    CATEGORY_GROUNDING: {
        5: "Every conclusion follows directly and explicitly from the cited facts",
        3: "Most conclusions are grounded; one or two stretch beyond what facts support",
        1: "Conclusions are asserted without factual support or contradict the facts",
    },
    CATEGORY_FACT_ATOMICITY: {
        5: "Facts are narrowly scoped, verifiable, and free of embedded judgments",
        3: "Some facts are compound or contain interpretive language",
        1: "Facts are vague, merged, or opinion-laden — hard to verify independently",
    },
    CATEGORY_CITATION_SUPPORT: {
        5: "Every citation genuinely supports the claim it's attached to",
        3: "Most citations are relevant; one or two are tangential or mismatched",
        1: "Citations don't support the claims, or key claims lack citations",
    },
    CATEGORY_CRITIQUE_QUALITY: {
        5: "Verification flags the real weaknesses in the run; pushes back on shaky claims",
        3: "Verification catches some issues but misses or rubber-stamps others",
        1: "Verification rubber-stamps a draft with visible weaknesses",
    },
    CATEGORY_GOAL_ALIGNMENT: {
        5: "The answer directly addresses the user's actual question with relevant depth",
        3: "The answer is mostly on-target but misses part of the question or overreaches",
        1: "The answer misses the question, answers a different one, or is too shallow",
    },
}
