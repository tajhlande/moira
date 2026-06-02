from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ToolMetricsRow:
    tool_name: str
    call_type: str
    period_hour: str
    call_count: int
    success_count: int
    error_count: int
    aggregate_duration_ms: int
    low_duration_ms: int
    high_duration_ms: int


class ToolMetricsRepository(ABC):
    @abstractmethod
    async def record_call(
        self,
        tool_name: str,
        call_type: str,
        period_hour: str,
        success: bool,
        duration_ms: int,
    ) -> None: ...

    @abstractmethod
    async def get_metrics(
        self,
        tool_name: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> list[ToolMetricsRow]: ...
