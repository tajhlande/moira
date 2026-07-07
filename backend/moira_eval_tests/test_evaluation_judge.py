"""Tests for moira_eval.judge and moira_eval.prompts.

Uses a mock InferenceClient to test:
- Judge prompt construction (system + user message content)
- JSON response parsing (clean, malformed, missing categories)
- JudgeResult properties (total, passed, hard-fail)
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
    POKEMON_JUDGE_SYSTEM,
    build_pokemon_judge_messages,
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


_VALID_JUDGE_RESPONSE = json.dumps(
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


# ---------------------------------------------------------------------------
# Prompt tests
# ---------------------------------------------------------------------------


class TestPrompts:
    def test_system_prompt_contains_all_categories(self):
        """All 8 rubric categories appear in the system prompt."""
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
            assert cat in POKEMON_JUDGE_SYSTEM, f"Category '{cat}' missing from system prompt"

    def test_system_prompt_marks_hard_fail_categories(self):
        """Hard-fail categories are marked with [HARD FAIL]."""
        assert "[HARD FAIL" in POKEMON_JUDGE_SYSTEM
        assert "Type correctness" in POKEMON_JUDGE_SYSTEM
        # Non-hard-fail categories should NOT have the tag
        # (but the category name still appears)
        # Verify "Tool choice" is NOT followed by [HARD FAIL]
        tool_choice_section = POKEMON_JUDGE_SYSTEM.split("Tool choice")[1].split("###")[0]
        assert "[HARD FAIL" not in tool_choice_section

    def test_system_prompt_contains_key_distinctions(self):
        """The unsupported != contradicted distinction is in the prompt."""
        assert "Unsupported != contradicted" in POKEMON_JUDGE_SYSTEM
        assert "Learnset != typical" in POKEMON_JUDGE_SYSTEM
        assert "Legal != common" in POKEMON_JUDGE_SYSTEM

    def test_system_prompt_contains_verification_stance(self):
        """The critique-quality stance is described."""
        assert "verification" in POKEMON_JUDGE_SYSTEM.lower()
        assert "rubber-stamp" in POKEMON_JUDGE_SYSTEM.lower()

    def test_system_prompt_contains_output_schema(self):
        """The JSON output schema is specified."""
        assert '"categories"' in POKEMON_JUDGE_SYSTEM
        assert '"overall_notes"' in POKEMON_JUDGE_SYSTEM

    def test_user_message_contains_question(self, sample_artifacts, sample_metrics):
        """The question text appears in the user message."""
        messages = build_pokemon_judge_messages("Test question?", sample_artifacts, sample_metrics)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "Test question?" in messages[1]["content"]

    def test_user_message_contains_answer(self, sample_artifacts, sample_metrics):
        """The agent's answer appears in the user message."""
        messages = build_pokemon_judge_messages("Q?", sample_artifacts, sample_metrics)
        assert "Tyranitar synergizes with Excadrill" in messages[1]["content"]

    def test_user_message_contains_knowledge(self, sample_artifacts, sample_metrics):
        """Facts and conclusions appear in the user message."""
        messages = build_pokemon_judge_messages("Q?", sample_artifacts, sample_metrics)
        assert "Sand Stream" in messages[1]["content"]
        assert "Excadrill pairs well" in messages[1]["content"]

    def test_user_message_contains_verification_outputs(self, sample_artifacts, sample_metrics):
        """Review and evaluation attempts appear in the user message."""
        messages = build_pokemon_judge_messages("Q?", sample_artifacts, sample_metrics)
        assert "well-supported" in messages[1]["content"]

    def test_user_message_contains_metrics(self, sample_artifacts, sample_metrics):
        """Mechanical metrics appear in the user message."""
        messages = build_pokemon_judge_messages("Q?", sample_artifacts, sample_metrics)
        assert "web_search calls: 4" in messages[1]["content"]
        assert "specialized tool use ratio: 0.5" in messages[1]["content"]

    def test_user_message_contains_grading_instruction(self, sample_artifacts, sample_metrics):
        """The 'grade don't re-answer' instruction is present."""
        messages = build_pokemon_judge_messages("Q?", sample_artifacts, sample_metrics)
        assert "do not re-answer" in messages[1]["content"].lower()
        assert "grade" in messages[1]["content"].lower()


# ---------------------------------------------------------------------------
# JSON parsing tests
# ---------------------------------------------------------------------------


class TestParseJudgeResponse:
    def test_clean_json(self):
        result = _parse_judge_response(_VALID_JUDGE_RESPONSE)
        assert len(result.categories) == 8
        assert result.total == 14  # 2+1+2+2+2+2+1+2
        assert result.max_total == 16
        assert result.passed is True
        assert result.overall_notes == "Solid run with minor synthesis issues."

    def test_markdown_fenced_json(self):
        """JSON wrapped in ```json ... ``` fences."""
        fenced = f"```json\n{_VALID_JUDGE_RESPONSE}\n```"
        result = _parse_judge_response(fenced)
        assert len(result.categories) == 8
        assert result.total == 14

    def test_json_with_think_block(self):
        """JSON preceded by <think>...</think> reasoning."""
        text = f"<think>Let me grade this carefully.</think>\n{_VALID_JUDGE_RESPONSE}"
        result = _parse_judge_response(text)
        assert len(result.categories) == 8

    def test_trailing_commas(self):
        """Trailing commas before closing braces are tolerated."""
        bad = _VALID_JUDGE_RESPONSE.replace('"]}', '"],}')
        result = _parse_judge_response(bad)
        assert len(result.categories) == 8

    def test_missing_category_filled_with_zero(self):
        """Categories the judge omitted default to score 0."""
        partial = json.dumps(
            {
                "categories": [
                    {"name": "Tool choice", "score": 2, "rationale": "Good."},
                ],
                "overall_notes": "Partial.",
            }
        )
        result = _parse_judge_response(partial)
        assert len(result.categories) == 8
        assert result.categories[0].name == "Tool choice"
        assert result.categories[0].score == 2
        # The remaining 7 should be 0 with a note
        for cat in result.categories[1:]:
            assert cat.score == 0
            assert "did not score" in cat.rationale

    def test_invalid_score_clamped(self):
        """Scores outside [0, 2] are clamped."""
        data = json.dumps(
            {
                "categories": [
                    {"name": name, "score": 5, "rationale": "..."}
                    for name in [
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
        result = _parse_judge_response(data)
        for cat in result.categories:
            assert cat.score == 2  # clamped down to max

    def test_negative_score_clamped(self):
        """Negative scores are clamped to 0."""
        data = json.dumps(
            {
                "categories": [
                    {"name": "Tool choice", "score": -1, "rationale": "bad"},
                    {"name": "Search discipline", "score": 1, "rationale": "ok"},
                    {"name": "Type correctness", "score": 0, "rationale": "bad"},
                    {"name": "Ability correctness", "score": 2, "rationale": "good"},
                    {"name": "Typical-move discipline", "score": 2, "rationale": "good"},
                    {"name": "OU legality and metagame", "score": 2, "rationale": "good"},
                    {"name": "Synthesis discipline", "score": 2, "rationale": "good"},
                    {"name": "Verification quality", "score": 2, "rationale": "good"},
                ]
            }
        )
        result = _parse_judge_response(data)
        assert result.categories[0].score == 0  # -1 clamped to 0

    def test_empty_response_raises(self):
        with pytest.raises(JudgeError, match="no parseable JSON"):
            _parse_judge_response("")

    def test_garbage_response_raises(self):
        with pytest.raises(JudgeError, match="no parseable JSON"):
            _parse_judge_response("This is not JSON at all.")

    def test_categories_preserve_rubric_order(self):
        """Result categories are in canonical rubric order, not judge order."""
        reversed_data = json.dumps(
            {
                "categories": [
                    {"name": "Verification quality", "score": 2, "rationale": "..."},
                    {"name": "Synthesis discipline", "score": 2, "rationale": "..."},
                    {"name": "OU legality and metagame", "score": 2, "rationale": "..."},
                    {"name": "Typical-move discipline", "score": 2, "rationale": "..."},
                    {"name": "Ability correctness", "score": 2, "rationale": "..."},
                    {"name": "Type correctness", "score": 2, "rationale": "..."},
                    {"name": "Search discipline", "score": 2, "rationale": "..."},
                    {"name": "Tool choice", "score": 2, "rationale": "..."},
                ]
            }
        )
        result = _parse_judge_response(reversed_data)
        assert result.categories[0].name == "Tool choice"
        assert result.categories[-1].name == "Verification quality"

    def test_hard_fail_detection(self):
        """Failing a hard-fail category makes passed=False."""
        data = json.dumps(
            {
                "categories": [
                    {"name": "Tool choice", "score": 2, "rationale": ""},
                    {"name": "Search discipline", "score": 2, "rationale": ""},
                    {
                        "name": "Type correctness",
                        "score": 1,
                        "rationale": "wrong type",
                    },  # HARD FAIL
                    {"name": "Ability correctness", "score": 2, "rationale": ""},
                    {"name": "Typical-move discipline", "score": 2, "rationale": ""},
                    {"name": "OU legality and metagame", "score": 2, "rationale": ""},
                    {"name": "Synthesis discipline", "score": 2, "rationale": ""},
                    {"name": "Verification quality", "score": 2, "rationale": ""},
                ]
            }
        )
        result = _parse_judge_response(data)
        assert not result.passed
        assert "Type correctness" in result.hard_fail_categories_failed


# ---------------------------------------------------------------------------
# JudgeConfig from env tests
# ---------------------------------------------------------------------------


class TestJudgeConfigFromEnv:
    def test_returns_none_when_endpoint_missing(self, monkeypatch):
        monkeypatch.delenv("MOIRA_EVAL_JUDGE_ENDPOINT", raising=False)
        monkeypatch.setenv("MOIRA_EVAL_JUDGE_MODEL", "gpt-4o")
        monkeypatch.delenv("MOIRA_EVAL_JUDGE_API_KEY", raising=False)
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
        assert config.api_key == "sk-test"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("MOIRA_EVAL_JUDGE_ENDPOINT", "  https://api.openai.com/v1  ")
        monkeypatch.setenv("MOIRA_EVAL_JUDGE_MODEL", "  gpt-4o  ")
        config = judge_config_from_env()
        assert config is not None
        assert config.endpoint == "https://api.openai.com/v1"
        assert config.model == "gpt-4o"


# ---------------------------------------------------------------------------
# JudgeResult property tests
# ---------------------------------------------------------------------------


class TestJudgeResult:
    def test_total_sums_categories(self):
        result = JudgeResult(
            categories=[
                JudgeCategoryScore(name="A", score=2),
                JudgeCategoryScore(name="B", score=1),
                JudgeCategoryScore(name="C", score=0),
            ]
        )
        assert result.total == 3

    def test_max_total(self):
        result = JudgeResult(
            categories=[
                JudgeCategoryScore(name="A", score=0),
                JudgeCategoryScore(name="B", score=0),
            ]
        )
        assert result.max_total == 4

    def test_passed_when_no_hard_fail_below_2(self):
        result = JudgeResult(
            categories=[
                JudgeCategoryScore(name="Type correctness", score=2),
                JudgeCategoryScore(name="Tool choice", score=1),  # not hard-fail
            ]
        )
        assert result.passed

    def test_failed_when_hard_fail_below_2(self):
        result = JudgeResult(
            categories=[
                JudgeCategoryScore(name="Type correctness", score=1),
                JudgeCategoryScore(name="Tool choice", score=2),
            ]
        )
        assert not result.passed
        assert "Type correctness" in result.hard_fail_categories_failed


# ---------------------------------------------------------------------------
# Judge.score_run integration tests (mocked InferenceClient)
# ---------------------------------------------------------------------------


class TestJudgeScoreRun:
    @pytest.mark.asyncio
    async def test_score_run_success(self, sample_artifacts, sample_metrics):
        """score_run builds messages, calls client, parses response."""
        config = JudgeConfig(endpoint="http://test", model="test-model")
        judge = Judge(config)

        mock_response = MagicMock()
        mock_response.content = _VALID_JUDGE_RESPONSE
        mock_response.model = "test-model-actual"

        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(return_value=mock_response)
        mock_client.start = AsyncMock()
        mock_client.stop = AsyncMock()
        judge._client = mock_client

        result = await judge.score_run(CANARY_QUESTION, sample_artifacts, sample_metrics)

        assert len(result.categories) == 8
        assert result.total == 14
        assert result.model == "test-model-actual"
        mock_client.chat_completion.assert_called_once()

        # Verify the messages passed to the client
        call_args = mock_client.chat_completion.call_args
        messages = call_args.kwargs["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert CANARY_QUESTION in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_score_run_model_failure_raises(self, sample_artifacts, sample_metrics):
        """When the model call fails, JudgeError is raised."""
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
    async def test_score_run_unparseable_raises(self, sample_artifacts, sample_metrics):
        """When the response can't be parsed, JudgeError is raised."""
        config = JudgeConfig(endpoint="http://test", model="test-model")
        judge = Judge(config)

        mock_response = MagicMock()
        mock_response.content = "not json at all"
        mock_response.model = "test-model"

        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(return_value=mock_response)
        mock_client.start = AsyncMock()
        mock_client.stop = AsyncMock()
        judge._client = mock_client

        with pytest.raises(JudgeError, match="no parseable JSON"):
            await judge.score_run(CANARY_QUESTION, sample_artifacts, sample_metrics)

    @pytest.mark.asyncio
    async def test_temperature_is_low(self, sample_artifacts, sample_metrics):
        """The judge uses low temperature for deterministic scoring."""
        config = JudgeConfig(endpoint="http://test", model="test-model")
        judge = Judge(config)

        mock_response = MagicMock()
        mock_response.content = _VALID_JUDGE_RESPONSE
        mock_response.model = "test-model"

        mock_client = MagicMock()
        mock_client.chat_completion = AsyncMock(return_value=mock_response)
        mock_client.start = AsyncMock()
        mock_client.stop = AsyncMock()
        judge._client = mock_client

        await judge.score_run(CANARY_QUESTION, sample_artifacts, sample_metrics)

        call_args = mock_client.chat_completion.call_args
        assert call_args.kwargs["temperature"] <= 0.3
