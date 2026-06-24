import logging
import sqlite3

from moira.inference.metrics import (
    InferenceMetricsRepository,
    InferenceMetricsRow,
)
from moira.persistence.sqlite.repos._connection import connect

logger = logging.getLogger(__name__)


class SqliteInferenceMetricsRepository(InferenceMetricsRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

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
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO inference_metrics "
                "(model, purpose, period_hour, call_count, "
                "input_tokens, output_tokens, thinking_tokens, "
                "prompt_time_ms, gen_time_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(model, purpose, period_hour) DO UPDATE SET "
                "call_count = call_count + excluded.call_count, "
                "input_tokens = input_tokens + excluded.input_tokens, "
                "output_tokens = output_tokens + excluded.output_tokens, "
                "thinking_tokens = thinking_tokens + excluded.thinking_tokens, "
                "prompt_time_ms = prompt_time_ms + excluded.prompt_time_ms, "
                "gen_time_ms = gen_time_ms + excluded.gen_time_ms",
                (
                    model,
                    purpose,
                    period_hour,
                    call_count,
                    input_tokens,
                    output_tokens,
                    thinking_tokens,
                    prompt_time_ms,
                    gen_time_ms,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def get_metrics(
        self,
        model: str | None = None,
        purpose: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> list[InferenceMetricsRow]:
        conn = self._connect()
        try:
            clauses: list[str] = []
            params: list[str] = []
            if model is not None:
                clauses.append("model = ?")
                params.append(model)
            if purpose is not None:
                clauses.append("purpose = ?")
                params.append(purpose)
            if period_start is not None:
                clauses.append("period_hour >= ?")
                params.append(period_start)
            if period_end is not None:
                clauses.append("period_hour <= ?")
                params.append(period_end)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(
                "SELECT model, purpose, period_hour, call_count, "
                "input_tokens, output_tokens, thinking_tokens, "
                "prompt_time_ms, gen_time_ms "
                f"FROM inference_metrics{where} "
                "ORDER BY period_hour DESC, model, purpose",
                params,
            ).fetchall()
            return [InferenceMetricsRow(**dict(r)) for r in rows]
        finally:
            conn.close()
