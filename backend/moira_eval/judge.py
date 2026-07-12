"""Frontier-model judge for evaluation.

Stands up an ``InferenceClient`` directly (no DB, no registry) using env
vars for endpoint/model/key configuration. The judge calls the frontier
model with a rubric-specific prompt, parses the structured JSON response,
and returns a ``JudgeResult``.

Supports two rubrics:
- ``"pokemon"`` — 8 categories, 0-2 scale, named hard-fail categories +
  minimum total threshold (12/16)
- ``"general"`` — 5 criteria, 1-5 scale, category-score threshold (≤2
  triggers fail) + minimum total threshold (15/25)

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
from typing import Any, Callable

from moira.inference.client import InferenceClient
from moira.workflow.nodes._helpers import _parse_json_object
from moira_eval.prompts import build_judge_messages
from moira_eval.rubric_general import (
    HARD_FAIL_CATEGORIES as _GENERAL_HARD_FAIL,
)
from moira_eval.rubric_general import (
    SCALE_MAX as _GENERAL_SCALE_MAX,
)
from moira_eval.rubric_general import create_empty_scorecard as _general_scorecard
from moira_eval.rubric_pokemon import (
    HARD_FAIL_CATEGORIES as _POKEMON_HARD_FAIL,
)
from moira_eval.rubric_pokemon import (
    SCALE_MAX as _POKEMON_SCALE_MAX,
)
from moira_eval.rubric_pokemon import create_empty_scorecard as _pokemon_scorecard

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rubric specs — maps rubric type to its scale, hard-fail set, and scorecard
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RubricSpec:
    """Rubric-specific parsing parameters.

    Three independent pass/fail mechanisms (all must pass):

    - ``hard_fail_categories`` — named categories that must score at
      ``scale_max``. Used by the pokemon rubric (e.g., type correctness
      must be perfect).
    - ``hard_fail_category_max`` — if set, ANY category scoring at or
      below this value triggers a fail. Used by the general rubric
      (any single weak dimension is disqualifying).
    - ``hard_fail_min_total`` — if set, a total below this triggers a
      fail. Catches uniformly mediocre runs.
    """

    scale_max: int
    hard_fail_categories: frozenset[str]
    create_scorecard: Callable
    hard_fail_category_max: int | None = None
    hard_fail_min_total: int | None = None


_RUBRIC_SPECS: dict[str, _RubricSpec] = {
    "pokemon": _RubricSpec(
        scale_max=_POKEMON_SCALE_MAX,
        hard_fail_categories=frozenset(_POKEMON_HARD_FAIL),
        create_scorecard=_pokemon_scorecard,
        hard_fail_min_total=12,
    ),
    "general": _RubricSpec(
        scale_max=_GENERAL_SCALE_MAX,
        hard_fail_categories=frozenset(_GENERAL_HARD_FAIL),
        create_scorecard=_general_scorecard,
        hard_fail_category_max=2,
        hard_fail_min_total=15,
    ),
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class JudgeCategoryScore:
    """Score for a single rubric category from the judge."""

    name: str
    score: int
    rationale: str = ""


@dataclass
class JudgeResult:
    """Parsed judge output for a single run.

    ``scale_max``, ``hard_fail_categories``, ``hard_fail_category_max``,
    and ``hard_fail_min_total`` are set from the rubric spec so
    ``max_total``, ``passed``, etc. work correctly regardless of rubric.
    """

    categories: list[JudgeCategoryScore] = field(default_factory=list)
    overall_notes: str = ""
    model: str = ""
    raw_response: str = ""
    scale_max: int = 2
    hard_fail_categories: frozenset[str] = field(default_factory=frozenset)
    rubric: str = ""
    hard_fail_category_max: int | None = None
    hard_fail_min_total: int | None = None

    @property
    def total(self) -> int:
        return sum(c.score for c in self.categories)

    @property
    def max_total(self) -> int:
        return len(self.categories) * self.scale_max

    @property
    def hard_fail_categories_failed(self) -> list[str]:
        """Named hard-fail categories that scored below scale_max."""
        return [
            c.name
            for c in self.categories
            if c.name in self.hard_fail_categories and c.score < self.scale_max
        ]

    @property
    def categories_below_threshold(self) -> list[str]:
        """Categories at or below ``hard_fail_category_max`` (if set).

        Returns an empty list when no threshold is configured (e.g.,
        pokemon rubric) or when all categories exceed the threshold.
        """
        if self.hard_fail_category_max is None:
            return []
        return [c.name for c in self.categories if c.score <= self.hard_fail_category_max]

    @property
    def total_below_minimum(self) -> bool:
        """True when ``hard_fail_min_total`` is set and total is below it."""
        return self.hard_fail_min_total is not None and self.total < self.hard_fail_min_total

    @property
    def passed(self) -> bool:
        """True if all three fail conditions are clear.

        - No named hard-fail category scored below scale_max.
        - No category scored at or below ``hard_fail_category_max``.
        - Total is at least ``hard_fail_min_total``.
        """
        if self.hard_fail_categories_failed:
            return False
        if self.categories_below_threshold:
            return False
        if self.total_below_minimum:
            return False
        return True


@dataclass
class JudgeConfig:
    """Connection parameters for the judge model."""

    endpoint: str
    model: str
    api_key: str = ""


class JudgeError(Exception):
    """Raised when the judge cannot produce a valid result."""


# ---------------------------------------------------------------------------
# Config from env
# ---------------------------------------------------------------------------

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


def _parse_judge_response(raw_text: str, rubric: str) -> JudgeResult:
    """Parse the judge's raw text response into a ``JudgeResult``.

    Uses the multi-strategy JSON pipeline from ``_helpers`` to tolerate
    markdown fences, trailing commas, and other common model-output issues.

    Args:
        raw_text: The raw model response text.
        rubric: Rubric type (``"pokemon"`` or ``"general"``).
    """
    spec = _RUBRIC_SPECS.get(rubric)
    if spec is None:
        raise JudgeError(f"Unknown rubric type: {rubric!r}")

    parsed = _parse_json_object(raw_text)
    if not parsed:
        raise JudgeError(f"Judge returned no parseable JSON. First 200 chars: {raw_text[:200]}")

    raw_categories = parsed.get("categories", [])
    if not isinstance(raw_categories, list):
        raise JudgeError("Judge response 'categories' is not a list")

    result = JudgeResult(
        overall_notes=str(parsed.get("overall_notes", "")),
        raw_response=raw_text,
        scale_max=spec.scale_max,
        hard_fail_categories=spec.hard_fail_categories,
        rubric=rubric,
        hard_fail_category_max=spec.hard_fail_category_max,
        hard_fail_min_total=spec.hard_fail_min_total,
    )

    # Index the judge's scores by name for lookup.
    judge_scores: dict[str, dict[str, Any]] = {}
    for entry in raw_categories:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if name:
            judge_scores[name] = entry

    # Build categories in canonical rubric order.
    for template in spec.create_scorecard():
        entry = judge_scores.get(template.name)
        if entry is not None:
            score = entry.get("score", 0)
            try:
                score = int(score)
            except (ValueError, TypeError):
                score = 0
            score = max(0, min(spec.scale_max, score))
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
        rubric: str = "pokemon",
    ) -> JudgeResult:
        """Score a single run against the specified rubric.

        Args:
            question: The benchmark question text.
            artifacts: The artifacts dict from ``capture_artifacts``.
            metrics: The metrics dict from ``compute_metrics``.
            rubric: Rubric type — ``"pokemon"`` or ``"general"``.

        Returns:
            A ``JudgeResult`` with per-category scores and overall notes.

        Raises:
            JudgeError: If the model call fails or the response can't be
                parsed into valid category scores.
        """
        messages = build_judge_messages(question, artifacts, metrics, rubric)

        try:
            response = await self._client.chat_completion(
                model=self._config.model,
                messages=messages,
                temperature=0.2,
            )
        except Exception as exc:
            raise JudgeError(f"Judge model call failed: {exc}") from exc

        result = _parse_judge_response(response.content, rubric)
        result.model = response.model or self._config.model
        return result
