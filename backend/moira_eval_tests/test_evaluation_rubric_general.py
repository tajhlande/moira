"""Tests for moira_eval.rubric_general.

Verifies shape parity with rubric_pokemon: same dataclass structure,
scale, scorecard factory, and pass/fail threshold logic.
"""

from moira_eval.rubric_general import (
    CATEGORY_CITATION_SUPPORT,
    CATEGORY_CRITIQUE_QUALITY,
    CATEGORY_DESCRIPTIONS,
    CATEGORY_FACT_ATOMICITY,
    CATEGORY_GOAL_ALIGNMENT,
    CATEGORY_GROUNDING,
    HARD_FAIL_CATEGORIES,
    HARD_FAIL_CATEGORY_MAX,
    HARD_FAIL_MIN_TOTAL,
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

    def test_no_named_hard_fail_categories(self):
        assert HARD_FAIL_CATEGORIES == set()

    def test_hard_fail_category_max_is_2(self):
        assert HARD_FAIL_CATEGORY_MAX == 2

    def test_hard_fail_min_total_is_15(self):
        assert HARD_FAIL_MIN_TOTAL == 15

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

    def test_passed_fails_when_any_category_at_or_below_threshold(self):
        """Any single category scoring ≤2 should trigger a fail."""
        scorecard = create_empty_scorecard()
        for cat in scorecard:
            cat.score = 4
        scorecard[2].score = 2  # one category at threshold
        rs = RunScore(categories=scorecard)
        assert rs.passed is False

    def test_passed_fails_when_total_below_minimum(self):
        """Total below 15 should trigger a fail even if all categories >2."""
        scorecard = create_empty_scorecard()
        for cat in scorecard:
            cat.score = 3  # total = 15, passes
        rs = RunScore(categories=scorecard)
        assert rs.passed is True

        scorecard[0].score = 2  # total drops to 14, and category ≤2
        rs = RunScore(categories=scorecard)
        assert rs.passed is False

    def test_passed_when_all_above_threshold_and_total_ok(self):
        scorecard = create_empty_scorecard()
        for cat in scorecard:
            cat.score = 3
        rs = RunScore(categories=scorecard)
        assert rs.passed is True
        assert rs.hard_fail_categories_failed == []
