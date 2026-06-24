import logging
import sqlite3

from moira.persistence.interfaces import (
    SettingEntry,
    SystemSettingsRepository,
)
from moira.persistence.sqlite.repos._connection import connect

logger = logging.getLogger(__name__)


class SqliteSystemSettingsRepository(SystemSettingsRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

    async def get(self, key: str, scope: str, scope_id: str) -> SettingEntry | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT key, value, scope, scope_id FROM settings "
                "WHERE scope = ? AND scope_id = ? AND key = ?",
                (scope, scope_id, key),
            ).fetchone()
            if row is None:
                return None
            return SettingEntry(**dict(row))
        finally:
            conn.close()

    async def get_prefix(self, prefix: str, scope: str, scope_id: str) -> list[SettingEntry]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT key, value, scope, scope_id FROM settings "
                "WHERE scope = ? AND scope_id = ? AND key LIKE ? "
                "ORDER BY key",
                (scope, scope_id, prefix + "%"),
            ).fetchall()
            return [SettingEntry(**dict(r)) for r in rows]
        finally:
            conn.close()

    async def set(self, entry: SettingEntry) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO settings (scope, scope_id, key, value) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(scope, scope_id, key) DO UPDATE SET "
                "value = excluded.value",
                (entry.scope, entry.scope_id, entry.key, entry.value),
            )
            conn.commit()
        finally:
            conn.close()

    async def set_batch(self, entries: list[SettingEntry]) -> None:
        if not entries:
            return
        conn = self._connect()
        try:
            conn.executemany(
                "INSERT INTO settings (scope, scope_id, key, value) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(scope, scope_id, key) DO UPDATE SET "
                "value = excluded.value",
                [(e.scope, e.scope_id, e.key, e.value) for e in entries],
            )
            conn.commit()
        finally:
            conn.close()

    async def delete(self, key: str, scope: str, scope_id: str) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM settings WHERE scope = ? AND scope_id = ? AND key = ?",
                (scope, scope_id, key),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
