from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TokenCounts:
    """Accumulates token counts and inference timing across multiple
    ``chat_completion()`` calls within a single graph node invocation.
    Used by tool-loop nodes (research_execution, research_review) to
    aggregate metrics before recording a single workflow step row."""

    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    prompt_time_ms: float = 0.0
    gen_time_ms: float = 0.0

    def accumulate(self, response: object) -> None:
        """Add token and timing data from a ChatResponse.

        ``None`` fields are treated as zero — the provider did not
        report that metric. This is safe because we only ever sum
        positive values."""
        for attr, target in (
            ("input_tokens", "input_tokens"),
            ("output_tokens", "output_tokens"),
            ("thinking_tokens", "thinking_tokens"),
            ("prompt_time_ms", "prompt_time_ms"),
            ("gen_time_ms", "gen_time_ms"),
        ):
            val = getattr(response, attr, None)
            if val is not None:
                setattr(self, target, getattr(self, target) + val)


@dataclass
class InferenceMetricsRow:
    model: str
    purpose: str
    period_hour: str
    call_count: int
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    prompt_time_ms: float
    gen_time_ms: float


class InferenceMetricsRepository(ABC):
    @abstractmethod
    async def record_step(
        self,
        model: str,
        purpose: str,
        period_hour: str,
        call_count: int,
        input_tokens: int,
        output_tokens: int,
        thinking_tokens: int,
        prompt_time_ms: float,
        gen_time_ms: float,
    ) -> None: ...

    @abstractmethod
    async def get_metrics(
        self,
        model: str | None = None,
        purpose: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> list[InferenceMetricsRow]: ...
