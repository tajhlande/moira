import logging
import sqlite3
from datetime import datetime, timezone

from moira.persistence.interfaces import (
    ApiSource,
    ApiSourceRepository,
)
from moira.persistence.sqlite.repos._connection import connect

logger = logging.getLogger(__name__)


class SqliteApiSourceRepository(ApiSourceRepository):
    """SQLite-backed persistence for ingested API sources."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

    @staticmethod
    def _row_to_source(row: sqlite3.Row) -> ApiSource:
        return ApiSource(
            id=row["id"],
            name=row["name"],
            base_url=row["base_url"],
            spec_url=row["spec_url"],
            spec_format=row["spec_format"],
            auth_type=row["auth_type"],
            group_name=row["group_name"],
            tool_count=row["tool_count"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get(self, source_id: str) -> ApiSource | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM api_sources WHERE id = ?", (source_id,)).fetchone()
            return self._row_to_source(row) if row else None
        finally:
            conn.close()

    async def get_all(self) -> list[ApiSource]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM api_sources ORDER BY created_at DESC").fetchall()
            return [self._row_to_source(r) for r in rows]
        finally:
            conn.close()

    async def save(self, source: ApiSource) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO api_sources
                   (id, name, base_url, spec_url, spec_format, auth_type,
                    group_name, tool_count, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       name = excluded.name,
                       base_url = excluded.base_url,
                       spec_url = excluded.spec_url,
                       spec_format = excluded.spec_format,
                       auth_type = excluded.auth_type,
                       group_name = excluded.group_name,
                       tool_count = excluded.tool_count,
                       enabled = excluded.enabled,
                       updated_at = excluded.updated_at""",
                (
                    source.id,
                    source.name,
                    source.base_url,
                    source.spec_url,
                    source.spec_format,
                    source.auth_type,
                    source.group_name,
                    source.tool_count,
                    int(source.enabled),
                    source.created_at,
                    source.updated_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def update_tool_count(self, source_id: str, tool_count: int) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE api_sources SET tool_count = ?, updated_at = ? WHERE id = ?",
                (tool_count, datetime.now(timezone.utc).isoformat(), source_id),
            )
            conn.commit()
        finally:
            conn.close()

    async def delete(self, source_id: str) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute("DELETE FROM api_sources WHERE id = ?", (source_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
