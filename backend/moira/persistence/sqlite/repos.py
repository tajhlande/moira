import logging
import sqlite3
from datetime import datetime, timezone

from moira.persistence.interfaces import (
    Message,
    ModelPreferences,
    ModelPreferencesRepository,
    Session,
    SessionRepository,
)

logger = logging.getLogger(__name__)


class SqliteSessionRepository(SessionRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path

    # Opens a new SQLite connection per operation. This avoids connection
    # management complexity and works with async code (sqlite3 is
    # synchronous, so each call blocks the event loop briefly). WAL mode
    # and foreign_keys are set per-connection because PRAGMAs are
    # connection-scoped. If latency becomes an issue, consider aiosqlite.
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def create_session(self, user_id: str, title: str) -> Session:
        logger.debug("Creating session for user %s", user_id)
        conn = self._connect()
        try:
            import uuid

            session_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
                (session_id, user_id, title, now),
            )
            conn.commit()
            return Session(id=session_id, user_id=user_id, title=title, created_at=now)
        finally:
            conn.close()

    async def get_session(self, session_id: str) -> Session | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, user_id, title, created_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            return Session(**dict(row))
        finally:
            conn.close()

    async def list_sessions(self, user_id: str) -> list[Session]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, user_id, title, created_at FROM sessions "
                "WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            return [Session(**dict(r)) for r in rows]
        finally:
            conn.close()

    async def insert_message(self, session_id: str, role: str, content: str) -> Message:
        logger.debug("Inserting %s message into session %s", role, session_id)
        conn = self._connect()
        try:
            now = datetime.now(timezone.utc).isoformat()
            cursor = conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, now),
            )
            conn.commit()
            return Message(
                id=cursor.lastrowid,
                session_id=session_id,
                role=role,
                content=content,
                created_at=now,
            )
        finally:
            conn.close()

    async def get_messages(self, session_id: str) -> list[Message]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, session_id, role, content, created_at FROM messages "
                "WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
            return [Message(**dict(r)) for r in rows]
        finally:
            conn.close()


class SqliteModelPreferencesRepository(ModelPreferencesRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def get_preferences(self, user_id: str) -> ModelPreferences:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT user_id, intelligence_endpoint, intelligence_model, "
                "task_endpoint, task_model FROM model_preferences "
                "WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return ModelPreferences(user_id=user_id)
            return ModelPreferences(**dict(row))
        finally:
            conn.close()

    async def set_preferences(self, preferences: ModelPreferences) -> None:
        logger.debug("Setting model preferences for user %s", preferences.user_id)
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO model_preferences "
                "(user_id, intelligence_endpoint, intelligence_model, "
                "task_endpoint, task_model) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "intelligence_endpoint = excluded.intelligence_endpoint, "
                "intelligence_model = excluded.intelligence_model, "
                "task_endpoint = excluded.task_endpoint, "
                "task_model = excluded.task_model",
                (
                    preferences.user_id,
                    preferences.intelligence_endpoint,
                    preferences.intelligence_model,
                    preferences.task_endpoint,
                    preferences.task_model,
                ),
            )
            conn.commit()
        finally:
            conn.close()
