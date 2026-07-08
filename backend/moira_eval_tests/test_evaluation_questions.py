"""Tests for moira_eval.questions.

Verifies the question set loads, rubric assignments resolve, and the
tyranitar-ou canary text matches CANARY_QUESTION.
"""

from moira_eval.questions import QUESTIONS, get_question
from moira_eval.rubric_pokemon import CANARY_QUESTION


class TestQuestionSet:
    def test_has_four_starter_questions(self):
        assert len(QUESTIONS) == 4

    def test_tyranitar_ou_exists(self):
        assert "tyranitar-ou" in QUESTIONS

    def test_tyranitar_ou_uses_canary_text(self):
        q = get_question("tyranitar-ou")
        assert q is not None
        assert q.text == CANARY_QUESTION

    def test_tyranitar_ou_rubric_is_pokemon(self):
        q = get_question("tyranitar-ou")
        assert q.rubric == "pokemon"

    def test_tyranitar_ou_is_not_placeholder(self):
        q = get_question("tyranitar-ou")
        assert q.placeholder is False

    def test_placeholder_questions_exist(self):
        for qid in ("oversearch-bait", "synthesis-trap", "multi-entity"):
            q = get_question(qid)
            assert q is not None, f"Missing question: {qid}"
            assert q.placeholder is True
            assert q.rubric == "general"

    def test_get_question_returns_none_for_unknown(self):
        assert get_question("nonexistent") is None

    def test_every_question_has_required_fields(self):
        for qid, q in QUESTIONS.items():
            assert q.id == qid, f"Question id mismatch: {qid}"
            assert q.text, f"Empty text for {qid}"
            assert q.rubric in ("pokemon", "general"), f"Bad rubric for {qid}"
            assert isinstance(q.tags, list)
            assert isinstance(q.notes, str)
            assert isinstance(q.placeholder, bool)
