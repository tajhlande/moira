"""Tests for moira_eval.judge and moira_eval.prompts.

Uses a mock InferenceClient to test:
- Judge prompt construction for both pokemon and general rubrics
- JSON response parsing (clean, malformed, missing categories)
- JudgeResult properties (total, passed, hard-fail, scale_max)
- JudgeError on unparseable output
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from moira_eval.judge import (
    Judge,
    JudgeCategoryScore,
    JudgeConfig,
    JudgeError,
    JudgeResult,
    _parse_judge_response,
    judge_config_from_env,
)
from moira_eval.prompts import (
    GENERAL_JUDGE_SYSTEM,
    POKEMON_JUDGE_SYSTEM,
    build_judge_messages,
)
from moira_eval.rubric_pokemon import CANARY_QUESTION

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_artifacts() -> dict:
    """Minimal artifacts dict for prompt construction."""
    return {
        "run_id": "test-run-123",
        "report": {
            "answer": "Tyranitar synergizes with Excadrill in sand.",
        },
        "knowledge": {
            "facts": [
                {
                    "id": "f1",
                    "subject": "Tyranitar",
                    "predicate": "has ability",
                    "object": "Sand Stream",
                    "status": "verified",
                },
                {
                    "id": "f2",
                    "subject": "Excadrill",
                    "predicate": "has ability",
                    "object": "Sand Rush",
                    "status": "verified",
                },
            ],
            "conclusions": [
                {
                    "id": "c1",
                    "text": "Excadrill pairs well with Tyranitar",
                    "status": "verified",
                    "supporting_fact_ids": ["f1", "f2"],
                },
            ],
        },
        "review_attempts": [{"decision": "approve", "issues": []}],
        "evaluation_attempts": [{"decision": "approve", "assessment": "well-supported"}],
        "critiques": [],
        "tool_catalog": [
            {"name": "web_search", "description": "Search the web"},
            {"name": "pokemon_species", "description": "Look up species data"},
        ],
    }


@pytest.fixture
def sample_metrics() -> dict:
    """Minimal metrics dict for prompt construction."""
    return {
        "web_search_calls": 4,
        "url_content_calls": 2,
        "total_tool_calls": 12,
        "specialized_tool_use_ratio": 0.5,
        "unknown_fact_count": 1,
        "unverified_fact_count": 0,
        "verified_fact_count": 5,
        "contradicted_fact_count": 0,
        "unsupported_conclusion_count": 0,
        "hallucinated_fact_id_count": 0,
        "hallucinated_fact_ids": [],
    }


_VALID_POKEMON_RESPONSE = json.dumps(
    {
        "categories": [
            {"name": "Tool choice", "score": 2, "rationale": "Used Pokemon tools first."},
            {"name": "Search discipline", "score": 1, "rationale": "Some unnecessary web_search."},
            {"name": "Type correctness", "score": 2, "rationale": "No type errors."},
            {"name": "Ability correctness", "score": 2, "rationale": "Abilities correct."},
            {"name": "Typical-move discipline", "score": 2, "rationale": "Good move sourcing."},
            {"name": "OU legality and metagame", "score": 2, "rationale": "Legal and relevant."},
            {
                "name": "Synthesis discipline",
                "score": 1,
                "rationale": "Some unsupported synergy claims.",
            },
            {"name": "Verification quality", "score": 2, "rationale": "Review caught key issues."},
        ],
        "overall_notes": "Solid run with minor synthesis issues.",
    }
)


_VALID_GENERAL_RESPONSE = json.dumps(
    {
        "categories": [
            {"name": "Grounding", "score": 4, "rationale": "Mostly grounded."},
            {"name": "Fact atomicity", "score": 5, "rationale": "Atomic and verifiable."},
            {"name": "Citation support", "score": 4, "rationale": "Citations match."},
            {"name": "Critique quality", "score": 3, "rationale": "Caught some issues."},
            {"name": "Goal alignment", "score": 5, "rationale": "Directly answers the question."},
        ],
        "overall_notes": "Strong grounding, verification could be deeper.",
    }
)


# ---------------------------------------------------------------------------
# Pokemon prompt tests
# ---------------------------------------------------------------------------


class TestPokemonPrompts:
    def test_system_prompt_contains_all_categories(self):
        categories = [
            "Tool choice",
            "Search discipline",
            "Type correctness",
            "Ability correctness",
            "Typical-move discipline",
            "OU legality and metagame",
            "Synthesis discipline",
            "Verification quality",
        ]
        for cat in categories:
            assert cat in POKEMON_JUDGE_SYSTEM, f"Category '{cat}' missing"

    def test_system_prompt_marks_hard_fail_categories(self):
        assert "[HARD FAIL" in POKEMON_JUDGE_SYSTEM
        tool_choice_section = POKEMON_JUDGE_SYSTEM.split("Tool choice")[1].split("###")[0]
        assert "[HARD FAIL" not in tool_choice_section

    def test_system_prompt_contains_key_distinctions(self):
        assert "Unsupported != contradicted" in POKEMON_JUDGE_SYSTEM
        assert "Learnset != typical" in POKEMON_JUDGE_SYSTEM
        assert "Legal != common" in POKEMON_JUDGE_SYSTEM

    def test_system_prompt_contains_tool_awareness(self):
        assert "Available Tools" in POKEMON_JUDGE_SYSTEM

    def test_user_message_contains_question(self, sample_artifacts, sample_metrics):
        messages = build_judge_messages(
            "Test question?", sample_artifacts, sample_metrics, "pokemon"
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "Test question?" in messages[1]["content"]

    def test_user_message_contains_answer(self, sample_artifacts, sample_metrics):
        messages = build_judge_messages("Q?", sample_artifacts, sample_metrics, "pokemon")
        assert "Tyranitar synergizes with Excadrill" in messages[1]["content"]

    def test_user_message_contains_tool_catalog(self, sample_artifacts, sample_metrics):
        messages = build_judge_messages("Q?", sample_artifacts, sample_metrics, "pokemon")
        assert "web_search" in messages[1]["content"]
        assert "pokemon_species" in messages[1]["content"]

    def test_user_message_contains_knowledge(self, sample_artifacts, sample_metrics):
        messages = build_judge_messages("Q?", sample_artifacts, sample_metrics, "pokemon")
        assert "Sand Stream" in messages[1]["content"]

    def test_user_message_contains_verification(self, sample_artifacts, sample_metrics):
        messages = build_judge_messages("Q?", sample_artifacts, sample_metrics, "pokemon")
        assert "well-supported" in messages[1]["content"]

    def test_user_message_contains_metrics(self, sample_artifacts, sample_metrics):
        messages = build_judge_messages("Q?", sample_artifacts, sample_metrics, "pokemon")
        assert "web_search calls: 4" in messages[1]["content"]

    def test_user_message_contains_grading_instruction(self, sample_artifacts, sample_metrics):
        messages = build_judge_messages("Q?", sample_artifacts, sample_metrics, "pokemon")
        assert "do not re-answer" in messages[1]["content"].lower()


# ---------------------------------------------------------------------------
# General prompt tests
# ---------------------------------------------------------------------------


class TestGeneralPrompts:
    def test_system_prompt_contains_all_criteria(self):
        criteria = [
            "Grounding",
            "Fact atomicity",
            "Citation support",
            "Critique quality",
            "Goal alignment",
        ]
        for c in criteria:
            assert c in GENERAL_JUDGE_SYSTEM, f"Criterion '{c}' missing"

    def test_system_prompt_uses_1_5_scale(self):
        assert "1-5" in GENERAL_JUDGE_SYSTEM
        assert "Score 5" in GENERAL_JUDGE_SYSTEM

    def test_system_prompt_no_hard_fail(self):
        assert "[HARD FAIL" not in GENERAL_JUDGE_SYSTEM

    def test_system_prompt_contains_key_distinctions(self):
        assert "Unsupported != contradicted" in GENERAL_JUDGE_SYSTEM
        assert "Atomic != vague" in GENERAL_JUDGE_SYSTEM
        assert "Cited != supported" in GENERAL_JUDGE_SYSTEM

    def test_system_prompt_contains_tool_awareness(self):
        assert "Available Tools" in GENERAL_JUDGE_SYSTEM

    def test_general_user_message(self, sample_artifacts, sample_metrics):
        messages = build_judge_messages(
            "What is Python?", sample_artifacts, sample_metrics, "general"
        )
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "What is Python?" in messages[1]["content"]


# ---------------------------------------------------------------------------
# JSON parsing tests — Pokemon
# ---------------------------------------------------------------------------


class TestParsePokemonResponse:
    def test_clean_json(self):
        result = _parse_judge_response(_VALID_POKEMON_RESPONSE, "pokemon")
        assert len(result.categories) == 8
        assert result.total == 14
        assert result.max_total == 16
        assert result.scale_max == 2
        assert result.rubric == "pokemon"
        assert result.passed is True
        assert result.overall_notes == "Solid run with minor synthesis issues."

    def test_markdown_fenced_json(self):
        fenced = f"```json\n{_VALID_POKEMON_RESPONSE}\n```"
        result = _parse_judge_response(fenced, "pokemon")
        assert len(result.categories) == 8

    def test_trailing_commas(self):
        bad = _VALID_POKEMON_RESPONSE.replace('"]}', '"],}')
        result = _parse_judge_response(bad, "pokemon")
        assert len(result.categories) == 8

    def test_missing_category_filled_with_zero(self):
        partial = json.dumps(
            {"categories": [{"name": "Tool choice", "score": 2, "rationale": "Good."}]}
        )
        result = _parse_judge_response(partial, "pokemon")
        assert len(result.categories) == 8
        assert result.categories[0].score == 2
        for cat in result.categories[1:]:
            assert cat.score == 0
            assert "did not score" in cat.rationale

    def test_invalid_score_clamped_to_2(self):
        data = json.dumps(
            {
                "categories": [
                    {"name": n, "score": 5}
                    for n in [
                        "Tool choice",
                        "Search discipline",
                        "Type correctness",
                        "Ability correctness",
                        "Typical-move discipline",
                        "OU legality and metagame",
                        "Synthesis discipline",
                        "Verification quality",
                    ]
                ]
            }
        )
        result = _parse_judge_response(data, "pokemon")
        for cat in result.categories:
            assert cat.score == 2

    def test_hard_fail_detection(self):
        data = json.dumps(
            {
                "categories": [
                    {"name": "Tool choice", "score": 2},
                    {"name": "Search discipline", "score": 2},
                    {"name": "Type correctness", "score": 1},  # HARD FAIL
                    {"name": "Ability correctness", "score": 2},
                    {"name": "Typical-move discipline", "score": 2},
                    {"name": "OU legality and metagame", "score": 2},
                    {"name": "Synthesis discipline", "score": 2},
                    {"name": "Verification quality", "score": 2},
                ]
            }
        )
        result = _parse_judge_response(data, "pokemon")
        assert not result.passed
        assert "Type correctness" in result.hard_fail_categories_failed

    def test_categories_preserve_rubric_order(self):
        reversed_data = json.dumps(
            {
                "categories": [
                    {"name": "Verification quality", "score": 2},
                    {"name": "Synthesis discipline", "score": 2},
                    {"name": "OU legality and metagame", "score": 2},
                    {"name": "Typical-move discipline", "score": 2},
                    {"name": "Ability correctness", "score": 2},
                    {"name": "Type correctness", "score": 2},
                    {"name": "Search discipline", "score": 2},
                    {"name": "Tool choice", "score": 2},
                ]
            }
        )
        result = _parse_judge_response(reversed_data, "pokemon")
        assert result.categories[0].name == "Tool choice"
        assert result.categories[-1].name == "Verification quality"

    def test_empty_response_raises(self):
        with pytest.raises(JudgeError, match="no parseable JSON"):
            _parse_judge_response("", "pokemon")


# ---------------------------------------------------------------------------
# JSON parsing tests — General
# ---------------------------------------------------------------------------


class TestParseGeneralResponse:
    def test_clean_json(self):
        result = _parse_judge_response(_VALID_GENERAL_RESPONSE, "general")
        assert len(result.categories) == 5
        assert result.total == 21  # 4+5+4+3+5
        assert result.max_total == 25
        assert result.scale_max == 5
        assert result.rubric == "general"
        assert result.passed is True  # no hard-fail in general

    def test_invalid_score_clamped_to_5(self):
        data = json.dumps(
            {
                "categories": [
                    {"name": n, "score": 99}
                    for n in [
                        "Grounding",
                        "Fact atomicity",
                        "Citation support",
                        "Critique quality",
                        "Goal alignment",
                    ]
                ]
            }
        )
        result = _parse_judge_response(data, "general")
        for cat in result.categories:
            assert cat.score == 5

    def test_missing_category_filled_with_zero(self):
        partial = json.dumps(
            {"categories": [{"name": "Grounding", "score": 4, "rationale": "Good."}]}
        )
        result = _parse_judge_response(partial, "general")
        assert len(result.categories) == 5
        assert result.categories[0].score == 4
        for cat in result.categories[1:]:
            assert cat.score == 0

    def test_no_hard_fail_categories(self):
        result = _parse_judge_response(_VALID_GENERAL_RESPONSE, "general")
        assert result.hard_fail_categories_failed == []

    def test_categories_preserve_rubric_order(self):
        reversed_data = json.dumps(
            {
                "categories": [
                    {"name": "Goal alignment", "score": 5},
                    {"name": "Critique quality", "score": 5},
                    {"name": "Citation support", "score": 5},
                    {"name": "Fact atomicity", "score": 5},
                    {"name": "Grounding", "score": 5},
                ]
            }
        )
        result = _parse_judge_response(reversed_data, "general")
        assert result.categories[0].name == "Grounding"
        assert result.categories[-1].name == "Goal alignment"

    def test_unknown_rubric_raises(self):
        with pytest.raises(JudgeError, match="Unknown rubric"):
            _parse_judge_response("{}", "nonexistent")


# ---------------------------------------------------------------------------
# JudgeConfig from env tests
# ---------------------------------------------------------------------------


class TestJudgeConfigFromEnv:
    def test_returns_none_when_endpoint_missing(self, monkeypatch):
        monkeypatch.delenv("MOIRA_EVAL_JUDGE_ENDPOINT", raising=False)
        monkeypatch.setenv("MOIRA_EVAL_JUDGE_MODEL", "gpt-4o")
        assert judge_config_from_env() is None

    def test_returns_none_when_model_missing(self, monkeypatch):
        monkeypatch.setenv("MOIRA_EVAL_JUDGE_ENDPOINT", "https://api.openai.com/v1")
        monkeypatch.delenv("MOIRA_EVAL_JUDGE_MODEL", raising=False)
        assert judge_config_from_env() is None

    def test_returns_config_when_both_set(self, monkeypatch):
        monkeypatch.setenv("MOIRA_EVAL_JUDGE_ENDPOINT", "https://api.openai.com/v1")
        monkeypatch.setenv("MOIRA_EVAL_JUDGE_MODEL", "gpt-4o")
        monkeypatch.setenv("MOIRA_EVAL_JUDGE_API_KEY", "sk-test")
        config = judge_config_from_env()
        assert config is not None
        assert config.endpoint == "https://api.openai.com/v1"
        assert config.model == "gpt-4o"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("MOIRA_EVAL_JUDGE_ENDPOINT", "  https://api.openai.com/v1  ")
        monkeypatch.setenv("MOIRA_EVAL_JUDGE_MODEL", "  gpt-4o  ")
        config = judge_config_from_env()
        assert config.endpoint == "https://api.openai.com/v1"
        assert config.model == "gpt-4o"


# ---------------------------------------------------------------------------
# JudgeResult property tests
# ---------------------------------------------------------------------------


class TestJudgeResult:
    def test_pokemon_max_total(self):
        result = JudgeResult(
            categories=[JudgeCategoryScore(name="A", score=0)] * 8,
            scale_max=2,
        )
        assert result.max_total == 16

    def test_general_max_total(self):
        result = JudgeResult(
            categories=[JudgeCategoryScore(name="A", score=0)] * 5,
            scale_max=5,
        )
        assert result.max_total == 25

    def test_passed_no_hard_fail(self):
        result = JudgeResult(
            categories=[JudgeCategoryScore(name="A", score=1)],
            scale_max=2,
            hard_fail_categories=frozenset(),
        )
        assert result.passed

    def test_failed_with_hard_fail(self):
        result = JudgeResult(
            categories=[
                JudgeCategoryScore(name="Type correctness", score=1),
            ],
            scale_max=2,
            hard_fail_categories=frozenset({"Type correctness"}),
        )
        assert not result.passed
        assert "Type correctness" in result.hard_fail_categories_failed


# ---------------------------------------------------------------------------
# Judge.score_run integration tests (mocked InferenceClient)
# ---------------------------------------------------------------------------


class TestJudgeScoreRun:
    @pytest.mark.asyncio
    async def test_pokemon_score_run_success(self, sample_artifacts, sample_metrics):
        config = JudgeConfig(endpoint="http://test", model="test-model")
        judge = Judge(config)

        mock_response = MagicMock()
        mock_response.content = _VALID_POKEMON_RESPONSE
        mock_response.model = "test-model-actual"

        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(return_value=mock_response)
        mock_client.start = AsyncMock()
        mock_client.stop = AsyncMock()
        judge._client = mock_client

        result = await judge.score_run(
            CANARY_QUESTION, sample_artifacts, sample_metrics, rubric="pokemon"
        )

        assert len(result.categories) == 8
        assert result.total == 14
        assert result.scale_max == 2
        assert result.model == "test-model-actual"
        mock_client.chat_completion.assert_called_once()

    @pytest.mark.asyncio
    async def test_general_score_run_success(self, sample_artifacts, sample_metrics):
        config = JudgeConfig(endpoint="http://test", model="test-model")
        judge = Judge(config)

        mock_response = MagicMock()
        mock_response.content = _VALID_GENERAL_RESPONSE
        mock_response.model = "test-model"

        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(return_value=mock_response)
        mock_client.start = AsyncMock()
        mock_client.stop = AsyncMock()
        judge._client = mock_client

        result = await judge.score_run(
            "What is Python?", sample_artifacts, sample_metrics, rubric="general"
        )

        assert len(result.categories) == 5
        assert result.total == 21
        assert result.scale_max == 5
        assert result.rubric == "general"

    @pytest.mark.asyncio
    async def test_score_run_model_failure_raises(self, sample_artifacts, sample_metrics):
        config = JudgeConfig(endpoint="http://test", model="test-model")
        judge = Judge(config)

        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(side_effect=Exception("API timeout"))
        mock_client.start = AsyncMock()
        mock_client.stop = AsyncMock()
        judge._client = mock_client

        with pytest.raises(JudgeError, match="Judge model call failed"):
            await judge.score_run(CANARY_QUESTION, sample_artifacts, sample_metrics)

    @pytest.mark.asyncio
    async def test_temperature_is_low(self, sample_artifacts, sample_metrics):
        config = JudgeConfig(endpoint="http://test", model="test-model")
        judge = Judge(config)

        mock_response = MagicMock()
        mock_response.content = _VALID_POKEMON_RESPONSE
        mock_response.model = "test-model"

        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(return_value=mock_response)
        mock_client.start = AsyncMock()
        mock_client.stop = AsyncMock()
        judge._client = mock_client

        await judge.score_run(CANARY_QUESTION, sample_artifacts, sample_metrics)

        call_args = mock_client.chat_completion.call_args
        assert call_args.kwargs["temperature"] <= 0.3
