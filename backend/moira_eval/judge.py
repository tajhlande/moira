"""Frontier-model judge for evaluation.

Stands up an ``InferenceClient`` directly (no DB, no registry) using env
vars for endpoint/model/key configuration. The judge calls the frontier
model with a rubric prompt, parses the structured JSON response, and
returns a ``JudgeResult``.

The judge is calibrated to itself, not to truth: it catches
reasoning/grounding regressions reliably, but may miss subtle factual
errors in domains it doesn't know well. A periodic hand-graded pass
keeps it honest.

Configuration via environment variables:

- ``MOIRA_EVAL_JUDGE_ENDPOINT`` — base URL (e.g. ``https://api.openai.com/v1``)
- ``MOIRA_EVAL_JUDGE_MODEL`` — model ID (e.g. ``gpt-4o``)
- ``MOIRA_EVAL_JUDGE_API_KEY`` — API key (bearer token)
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from moira.inference.client import InferenceClient
from moira.workflow.nodes._helpers import _parse_json_object
from moira_eval.prompts import build_pokemon_judge_messages
from moira_eval.rubric_pokemon import HARD_FAIL_CATEGORIES, create_empty_scorecard

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class JudgeCategoryScore:
    """Score for a single rubric category from the judge."""

    name: str
    score: int  # 0, 1, or 2
    rationale: str = ""


@dataclass
class JudgeResult:
    """Parsed judge output for a single run.

    ``categories`` follows the order of the rubric's scorecard. ``model``
    is the actual model string returned by the API (may differ from the
    requested model ID). ``raw_response`` is kept for debugging but is
    not written to result files.
    """

    categories: list[JudgeCategoryScore] = field(default_factory=list)
    overall_notes: str = ""
    model: str = ""
    raw_response: str = ""

    @property
    def total(self) -> int:
        return sum(c.score for c in self.categories)

    @property
    def max_total(self) -> int:
        return len(self.categories) * 2

    @property
    def hard_fail_categories_failed(self) -> list[str]:
        """Names of hard-fail categories that scored below 2."""
        return [c.name for c in self.categories if c.name in HARD_FAIL_CATEGORIES and c.score < 2]

    @property
    def passed(self) -> bool:
        return len(self.hard_fail_categories_failed) == 0


@dataclass
class JudgeConfig:
    """Connection parameters for the judge model.

    Created from environment variables via :func:`judge_config_from_env`.
    """

    endpoint: str
    model: str
    api_key: str = ""


class JudgeError(Exception):
    """Raised when the judge cannot produce a valid result."""


# ---------------------------------------------------------------------------
# Config from env
# ---------------------------------------------------------------------------

# Env var names — documented in module docstring and README.
_ENV_ENDPOINT = "MOIRA_EVAL_JUDGE_ENDPOINT"
_ENV_MODEL = "MOIRA_EVAL_JUDGE_MODEL"
_ENV_API_KEY = "MOIRA_EVAL_JUDGE_API_KEY"


def judge_config_from_env() -> JudgeConfig | None:
    """Build a ``JudgeConfig`` from environment variables.

    Returns ``None`` if the endpoint or model is not set, signalling the
    caller to fall back to metrics-only mode.
    """
    endpoint = os.environ.get(_ENV_ENDPOINT, "").strip()
    model = os.environ.get(_ENV_MODEL, "").strip()
    if not endpoint or not model:
        return None
    api_key = os.environ.get(_ENV_API_KEY, "").strip()
    return JudgeConfig(endpoint=endpoint, model=model, api_key=api_key)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

# All valid category names — used to validate / fill the judge's response.
_VALID_CATEGORY_NAMES = {c.name for c in create_empty_scorecard()}


def _parse_judge_response(raw_text: str) -> JudgeResult:
    """Parse the judge's raw text response into a ``JudgeResult``.

    Uses the multi-strategy JSON pipeline from ``_helpers`` to tolerate
    markdown fences, trailing commas, and other common model-output issues.
    """
    parsed = _parse_json_object(raw_text)
    if not parsed:
        raise JudgeError(f"Judge returned no parseable JSON. First 200 chars: {raw_text[:200]}")

    raw_categories = parsed.get("categories", [])
    if not isinstance(raw_categories, list):
        raise JudgeError("Judge response 'categories' is not a list")

    # Build a lookup of valid category names to fill defaults for any
    # the judge omitted. We preserve the rubric's canonical ordering.
    result = JudgeResult(
        overall_notes=str(parsed.get("overall_notes", "")),
        raw_response=raw_text,
    )

    # Index the judge's scores by name for lookup.
    judge_scores: dict[str, dict[str, Any]] = {}
    for entry in raw_categories:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if name:
            judge_scores[name] = entry

    # Build categories in canonical rubric order, filling defaults for
    # any the judge skipped or returned with an invalid name.
    for template in create_empty_scorecard():
        entry = judge_scores.get(template.name)
        if entry is not None:
            score = entry.get("score", 0)
            try:
                score = int(score)
            except (ValueError, TypeError):
                score = 0
            # Clamp to valid range [0, 2]
            score = max(0, min(2, score))
            rationale = str(entry.get("rationale", ""))
        else:
            score = 0
            rationale = "(judge did not score this category)"
            logger.warning("Judge omitted category: %s", template.name)
        result.categories.append(
            JudgeCategoryScore(name=template.name, score=score, rationale=rationale)
        )

    return result


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------


class Judge:
    """Frontier-model judge that scores a run against a rubric.

    Two-phase lifecycle mirroring ``InferenceClient``: construct with a
    ``JudgeConfig``, then call :meth:`start` before use and :meth:`stop`
    to release the HTTP client.

    For Iteration 2, only the Pokemon rubric is supported. Iteration 3
    generalizes via rubric-specific prompts.
    """

    def __init__(self, config: JudgeConfig):
        self._config = config
        self._client = InferenceClient(
            base_url=config.endpoint,
            api_key=config.api_key,
        )

    async def start(self) -> None:
        await self._client.start()

    async def stop(self) -> None:
        await self._client.stop()

    async def score_run(
        self,
        question: str,
        artifacts: dict,
        metrics: dict[str, Any],
    ) -> JudgeResult:
        """Score a single run against the Pokemon rubric.

        Args:
            question: The benchmark question text.
            artifacts: The artifacts dict from ``capture_artifacts``.
            metrics: The metrics dict from ``compute_metrics``.

        Returns:
            A ``JudgeResult`` with per-category scores and overall notes.

        Raises:
            JudgeError: If the model call fails or the response can't be
                parsed into valid category scores.
        """
        messages = build_pokemon_judge_messages(question, artifacts, metrics)

        try:
            response = await self._client.chat_completion(
                model=self._config.model,
                messages=messages,
                temperature=0.2,
            )
        except Exception as exc:
            raise JudgeError(f"Judge model call failed: {exc}") from exc

        result = _parse_judge_response(response.content)
        result.model = response.model or self._config.model
        return result
