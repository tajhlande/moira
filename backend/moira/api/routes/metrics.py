"""Analytics and inference metrics endpoints.

Owns the API surface for the Analytics sub-tab in the Settings tab:
tool-call metrics and inference metrics aggregated by model and purpose.
start/end are ISO date strings (YYYY-MM-DD) converted to period_hour
range."""

import logging
from typing import cast

from fastapi import APIRouter

from moira.service_setup import service_provider

logger = logging.getLogger(__name__)

router = APIRouter()


def _metrics_repo():
    from moira.tools.metrics import ToolMetricsRepository

    return cast(ToolMetricsRepository, service_provider("tool_metrics_repository"))


@router.get("/metrics")
async def get_metrics(
    start: str | None = None,
    end: str | None = None,
):
    """Return tool call metrics aggregated by tool name. Returns the top 20
    tools by aggregate call count over the requested period. start/end are
    ISO date strings (YYYY-MM-DD) converted to period_hour range."""
    from collections import defaultdict

    from moira.tools.metrics import ToolMetricsRow

    repo = _metrics_repo()

    period_start = f"{start}T00:00" if start else None
    period_end = f"{end}T23:59" if end else None

    rows: list[ToolMetricsRow] = await repo.get_metrics(
        period_start=period_start,
        period_end=period_end,
    )

    tool_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        tool_counts[r.tool_name] += r.call_count

    top_20 = sorted(tool_counts.keys(), key=lambda k: tool_counts[k], reverse=True)[:20]

    filtered = [r for r in rows if r.tool_name in top_20]

    return {
        "metrics": [
            {
                "tool_name": r.tool_name,
                "call_type": r.call_type,
                "period_hour": r.period_hour,
                "call_count": r.call_count,
                "success_count": r.success_count,
                "error_count": r.error_count,
                "aggregate_duration_ms": r.aggregate_duration_ms,
                "low_duration_ms": r.low_duration_ms,
                "high_duration_ms": r.high_duration_ms,
                "cache_hits": r.cache_hits,
                "cache_misses": r.cache_misses,
            }
            for r in filtered
        ]
    }


@router.get("/metrics/inference")
async def get_inference_metrics(
    start: str | None = None,
    end: str | None = None,
):
    """Return inference metrics aggregated by model and purpose. start/end
    are ISO date strings (YYYY-MM-DD) converted to period_hour range."""
    from moira.inference.metrics import InferenceMetricsRepository

    repo = cast(
        InferenceMetricsRepository,
        service_provider("inference_metrics_repository"),
    )

    period_start = f"{start}T00:00" if start else None
    period_end = f"{end}T23:59" if end else None

    rows = await repo.get_metrics(
        period_start=period_start,
        period_end=period_end,
    )

    return {
        "metrics": [
            {
                "model": r.model,
                "purpose": r.purpose,
                "period_hour": r.period_hour,
                "call_count": r.call_count,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "thinking_tokens": r.thinking_tokens,
                "prompt_time_ms": r.prompt_time_ms,
                "gen_time_ms": r.gen_time_ms,
            }
            for r in rows
        ]
    }
