import json
import logging
import sqlite3
from datetime import datetime, timezone

from moira.persistence.interfaces import (
    Conversation,
    ConversationRepository,
    Message,
    ModelPreferences,
    ModelPreferencesRepository,
    WorkflowRun,
)

logger = logging.getLogger(__name__)


class SqliteConversationRepository(ConversationRepository):
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

    async def create_conversation(self, user_id: str, title: str) -> Conversation:
        logger.debug("Creating conversation for user %s", user_id)
        conn = self._connect()
        try:
            import uuid

            conversation_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO conversations (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, user_id, title, now),
            )
            conn.commit()
            return Conversation(id=conversation_id, user_id=user_id, title=title, created_at=now)
        finally:
            conn.close()

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, user_id, title, created_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            return Conversation(**dict(row))
        finally:
            conn.close()

    async def list_conversations(self, user_id: str) -> list[Conversation]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, user_id, title, created_at FROM conversations "
                "WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            return [Conversation(**dict(r)) for r in rows]
        finally:
            conn.close()

    async def insert_message(self, conversation_id: str, role: str, content: str) -> Message:
        logger.debug("Inserting %s message into conversation %s", role, conversation_id)
        conn = self._connect()
        try:
            now = datetime.now(timezone.utc).isoformat()
            cursor = conn.execute(
                "INSERT INTO messages "
                "(conversation_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (conversation_id, role, content, now),
            )
            conn.commit()
            return Message(
                id=cursor.lastrowid,
                conversation_id=conversation_id,
                role=role,
                content=content,
                created_at=now,
            )
        finally:
            conn.close()

    async def get_messages(self, conversation_id: str) -> list[Message]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, conversation_id, role, content, created_at "
                "FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
                (conversation_id,),
            ).fetchall()
            return [Message(**dict(r)) for r in rows]
        finally:
            conn.close()

    async def update_conversation(self, conversation_id: str, title: str) -> Conversation | None:
        logger.debug("Updating conversation %s title to '%s'", conversation_id, title)
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ?",
                (title, conversation_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT id, user_id, title, created_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            return Conversation(**dict(row)) if row else None
        finally:
            conn.close()

    async def save_workflow_run(self, run: WorkflowRun) -> None:
        logger.debug("Saving workflow run %s for conversation %s", run.id, run.conversation_id)
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO workflow_runs "
                "(id, conversation_id, user_message_id, thread_id, "
                "execution_steps, tool_executions, verification_attempts, "
                "thinking_traces, report, budget_limit, budget_consumed, "
                "error, status, started_at, completed_at, total_elapsed_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "execution_steps = excluded.execution_steps, "
                "tool_executions = excluded.tool_executions, "
                "verification_attempts = excluded.verification_attempts, "
                "thinking_traces = excluded.thinking_traces, "
                "report = excluded.report, "
                "budget_consumed = excluded.budget_consumed, "
                "error = excluded.error, "
                "status = excluded.status, "
                "completed_at = excluded.completed_at, "
                "total_elapsed_ms = excluded.total_elapsed_ms",
                (
                    run.id,
                    run.conversation_id,
                    run.user_message_id,
                    run.thread_id,
                    json.dumps(run.execution_steps),
                    json.dumps(run.tool_executions),
                    json.dumps(run.verification_attempts),
                    json.dumps(run.thinking_traces),
                    json.dumps(run.report) if run.report else None,
                    run.budget_limit,
                    run.budget_consumed,
                    run.error,
                    run.status,
                    run.started_at,
                    run.completed_at or None,
                    run.total_elapsed_ms or None,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def get_workflow_runs(self, conversation_id: str) -> list[WorkflowRun]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, conversation_id, user_message_id, thread_id, "
                "execution_steps, tool_executions, verification_attempts, "
                "thinking_traces, report, budget_limit, budget_consumed, "
                "error, status, started_at, completed_at, total_elapsed_ms "
                "FROM workflow_runs WHERE conversation_id = ? ORDER BY started_at ASC",
                (conversation_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["execution_steps"] = json.loads(d["execution_steps"])
                d["tool_executions"] = json.loads(d["tool_executions"])
                d["verification_attempts"] = json.loads(d["verification_attempts"])
                d["thinking_traces"] = json.loads(d["thinking_traces"])
                d["report"] = json.loads(d["report"]) if d["report"] else None
                d["total_elapsed_ms"] = d["total_elapsed_ms"] or 0
                result.append(WorkflowRun(**d))
            return result
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
