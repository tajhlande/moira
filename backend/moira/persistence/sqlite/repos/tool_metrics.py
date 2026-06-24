import logging
import sqlite3

from moira.persistence.sqlite.repos._connection import connect
from moira.tools.metrics import ToolMetricsRepository, ToolMetricsRow

logger = logging.getLogger(__name__)


class SqliteToolMetricsRepository(ToolMetricsRepository):
    """Records tool call metrics in hourly buckets using atomic upsert.

    Each call increments counters in the row identified by
    (tool_name, call_type, period_hour). Latency is tracked via
    aggregate/min/max for outlier visibility without per-call rows."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

    async def record_call(
        self,
        tool_name: str,
        call_type: str,
        period_hour: str,
        success: bool,
        duration_ms: int,
    ) -> None:
        success_inc = 1 if success else 0
        error_inc = 0 if success else 1
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO tool_metrics "
                "(tool_name, call_type, period_hour, call_count, "
                "success_count, error_count, aggregate_duration_ms, "
                "low_duration_ms, high_duration_ms) "
                "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?) "
                "ON CONFLICT(tool_name, call_type, period_hour) DO UPDATE SET "
                "call_count = call_count + 1, "
                "success_count = success_count + ?, "
                "error_count = error_count + ?, "
                "aggregate_duration_ms = aggregate_duration_ms + ?, "
                "low_duration_ms = MIN(low_duration_ms, ?), "
                "high_duration_ms = MAX(high_duration_ms, ?)",
                (
                    tool_name,
                    call_type,
                    period_hour,
                    success_inc,
                    error_inc,
                    duration_ms,
                    duration_ms,
                    duration_ms,
                    success_inc,
                    error_inc,
                    duration_ms,
                    duration_ms,
                    duration_ms,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def get_metrics(
        self,
        tool_name: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> list[ToolMetricsRow]:
        conn = self._connect()
        try:
            clauses: list[str] = []
            params: list[str] = []
            if tool_name is not None:
                clauses.append("tool_name = ?")
                params.append(tool_name)
            if period_start is not None:
                clauses.append("period_hour >= ?")
                params.append(period_start)
            if period_end is not None:
                clauses.append("period_hour <= ?")
                params.append(period_end)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(
                "SELECT tool_name, call_type, period_hour, call_count, "
                "success_count, error_count, aggregate_duration_ms, "
                "low_duration_ms, high_duration_ms "
                f"FROM tool_metrics{where} "
                "ORDER BY period_hour DESC, tool_name, call_type",
                params,
            ).fetchall()
            return [ToolMetricsRow(**dict(r)) for r in rows]
        finally:
            conn.close()
