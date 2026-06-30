import logging
import sqlite3

from moira.persistence.interfaces import (
    ConversationModelOverride,
    ConversationModelRepository,
)
from moira.persistence.sqlite.repos._connection import connect

logger = logging.getLogger(__name__)


class SqliteConversationModelRepository(ConversationModelRepository):
    """SQLite-backed repository for the conversation_models table.

    Stores optional per-conversation intelligence model overrides.
    FK ON DELETE CASCADE on conversations(id) ensures rows are cleaned
    up automatically when a conversation is deleted."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

    async def get_override(self, conversation_id: str) -> ConversationModelOverride | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT conversation_id, intelligence_endpoint, intelligence_model, updated_at "
                "FROM conversation_models WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            return ConversationModelOverride(**dict(row))
        finally:
            conn.close()

    async def upsert_override(self, override: ConversationModelOverride) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO conversation_models "
                "(conversation_id, intelligence_endpoint, intelligence_model, updated_at) "
                "VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(conversation_id) DO UPDATE SET "
                "intelligence_endpoint = excluded.intelligence_endpoint, "
                "intelligence_model = excluded.intelligence_model, "
                "updated_at = datetime('now')",
                (
                    override.conversation_id,
                    override.intelligence_endpoint,
                    override.intelligence_model,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def delete_override(self, conversation_id: str) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM conversation_models WHERE conversation_id = ?",
                (conversation_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
