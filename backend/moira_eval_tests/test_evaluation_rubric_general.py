"""Tests for moira_eval.rubric_general.

Verifies shape parity with rubric_pokemon: same dataclass structure,
scale, scorecard factory, and no hard-fail categories.
"""

from moira_eval.rubric_general import (
    CATEGORY_CITATION_SUPPORT,
    CATEGORY_CRITIQUE_QUALITY,
    CATEGORY_DESCRIPTIONS,
    CATEGORY_FACT_ATOMICITY,
    CATEGORY_GOAL_ALIGNMENT,
    CATEGORY_GROUNDING,
    HARD_FAIL_CATEGORIES,
    SCALE_MAX,
    RunScore,
    create_empty_scorecard,
)


class TestRubricShape:
    def test_scale_max_is_5(self):
        assert SCALE_MAX == 5

    def test_has_five_categories(self):
        scorecard = create_empty_scorecard()
        assert len(scorecard) == 5

    def test_no_hard_fail_categories(self):
        assert HARD_FAIL_CATEGORIES == set()

    def test_all_categories_have_descriptions(self):
        scorecard = create_empty_scorecard()
        for cat in scorecard:
            assert cat.name in CATEGORY_DESCRIPTIONS
            descs = CATEGORY_DESCRIPTIONS[cat.name]
            assert 5 in descs
            assert 1 in descs

    def test_scorecard_names_in_order(self):
        scorecard = create_empty_scorecard()
        names = [c.name for c in scorecard]
        assert names == [
            CATEGORY_GROUNDING,
            CATEGORY_FACT_ATOMICITY,
            CATEGORY_CITATION_SUPPORT,
            CATEGORY_CRITIQUE_QUALITY,
            CATEGORY_GOAL_ALIGNMENT,
        ]

    def test_scorecard_all_zero(self):
        scorecard = create_empty_scorecard()
        for cat in scorecard:
            assert cat.score == 0
            assert cat.hard_fail is False

    def test_category_names_are_stable_constants(self):
        assert CATEGORY_GROUNDING == "Grounding"
        assert CATEGORY_FACT_ATOMICITY == "Fact atomicity"
        assert CATEGORY_CITATION_SUPPORT == "Citation support"
        assert CATEGORY_CRITIQUE_QUALITY == "Critique quality"
        assert CATEGORY_GOAL_ALIGNMENT == "Goal alignment"


class TestRunScore:
    def test_max_total_is_25(self):
        scorecard = create_empty_scorecard()
        rs = RunScore(categories=scorecard)
        assert rs.max_total == 25

    def test_total_sums_scores(self):
        scorecard = create_empty_scorecard()
        for i, cat in enumerate(scorecard):
            cat.score = i + 1  # 1, 2, 3, 4, 5
        rs = RunScore(categories=scorecard)
        assert rs.total == 15

    def test_passed_always_true_no_hard_fail(self):
        scorecard = create_empty_scorecard()
        for cat in scorecard:
            cat.score = 1  # minimum score
        rs = RunScore(categories=scorecard)
        assert rs.passed is True
        assert rs.hard_fail_categories_failed == []
