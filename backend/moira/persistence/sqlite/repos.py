import json
import logging
import sqlite3
from datetime import datetime, timezone

from moira.persistence.interfaces import (
    Conversation,
    ConversationRepository,
    CredentialRepository,
    CredentialRow,
    Message,
    ModelPreferences,
    ModelPreferencesRepository,
    ToolRepository,
    WorkflowRun,
)
from moira.tools.base import ToolDefinition

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
                "report, budget_limit, budget_consumed, "
                "error, status, started_at, completed_at, total_elapsed_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "execution_steps = excluded.execution_steps, "
                "tool_executions = excluded.tool_executions, "
                "verification_attempts = excluded.verification_attempts, "
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
                "report, budget_limit, budget_consumed, "
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
                d["report"] = json.loads(d["report"]) if d["report"] else None
                d["total_elapsed_ms"] = d["total_elapsed_ms"] or 0
                result.append(WorkflowRun(**d))
            return result
        finally:
            conn.close()

    async def delete_conversation(self, conversation_id: str) -> bool:
        logger.debug("Deleting conversation %s and all dependent records", conversation_id)
        conn = self._connect()
        try:
            # Delete dependents first — foreign keys lack ON DELETE CASCADE.
            conn.execute(
                "DELETE FROM workflow_runs WHERE conversation_id = ?",
                (conversation_id,),
            )
            conn.execute(
                "DELETE FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted
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


class SqliteToolRepository(ToolRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _row_to_defn(row: sqlite3.Row) -> ToolDefinition:
        return ToolDefinition(
            name=row["name"],
            description=row["description"],
            argument_schema=json.loads(row["argument_schema"]),
            config=json.loads(row["config"]),
            tags=json.loads(row["tags"]),
            reliability=row["reliability"],
            is_default=bool(row["is_default"]),
            enabled=bool(row["enabled"]),
            built_in=bool(row["built_in"]),
            implementation=row["implementation"],
            group_name=row["group_name"],
        )

    async def get_all_tools(self) -> list[ToolDefinition]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM tools ORDER BY group_name, name").fetchall()
            return [self._row_to_defn(r) for r in rows]
        finally:
            conn.close()

    async def get_tool(self, name: str) -> ToolDefinition | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM tools WHERE name = ?", (name,)).fetchone()
            return self._row_to_defn(row) if row else None
        finally:
            conn.close()

    async def save_tool(self, tool: ToolDefinition) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO tools
                   (name, description, argument_schema, config, tags,
                    reliability, is_default, enabled, built_in,
                    implementation, group_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tool.name,
                    tool.description,
                    json.dumps(tool.argument_schema),
                    json.dumps(tool.config),
                    json.dumps(tool.tags),
                    tool.reliability,
                    int(tool.is_default),
                    int(tool.enabled),
                    int(tool.built_in),
                    tool.implementation,
                    tool.group_name,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def delete_tool(self, name: str) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute("DELETE FROM tools WHERE name = ? AND is_default = 0", (name,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    async def set_enabled(self, name: str, enabled: bool) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE tools SET enabled = ? WHERE name = ?",
                (int(enabled), name),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    async def get_all_groups(self) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM tool_groups ORDER BY name").fetchall()
            return [{"name": r["name"], "display_name": r["display_name"]} for r in rows]
        finally:
            conn.close()

    async def save_group(self, group: dict) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO tool_groups (name, display_name) VALUES (?, ?)",
                (group["name"], group["display_name"]),
            )
            conn.commit()
        finally:
            conn.close()


class SqliteCredentialRepository(CredentialRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _row_to_credential_row(row: sqlite3.Row) -> CredentialRow:
        return CredentialRow(
            owner=row["owner"],
            name=row["name"],
            encrypted_data=row["encrypted_data"],
            salt=row["salt"],
            encryption_version=row["encryption_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get_by_name(self, owner: str, name: str) -> CredentialRow | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT owner, name, encrypted_data, salt, "
                "encryption_version, created_at, updated_at "
                "FROM credentials WHERE owner = ? AND name = ?",
                (owner, name),
            ).fetchone()
            return self._row_to_credential_row(row) if row else None
        finally:
            conn.close()

    async def save(
        self,
        owner: str,
        name: str,
        encrypted_data: str,
        salt: str,
        encryption_version: int,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO credentials "
                "(owner, name, encrypted_data, salt, encryption_version) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(owner, name) DO UPDATE SET "
                "encrypted_data = excluded.encrypted_data, "
                "salt = excluded.salt, "
                "encryption_version = excluded.encryption_version, "
                "updated_at = datetime('now')",
                (owner, name, encrypted_data, salt, encryption_version),
            )
            conn.commit()
        finally:
            conn.close()

    async def delete(self, owner: str, name: str) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM credentials WHERE owner = ? AND name = ?",
                (owner, name),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    async def list_all(self, owner: str | None = None) -> list[CredentialRow]:
        conn = self._connect()
        try:
            if owner is not None:
                rows = conn.execute(
                    "SELECT owner, name, encrypted_data, salt, "
                    "encryption_version, created_at, updated_at "
                    "FROM credentials WHERE owner = ? ORDER BY name",
                    (owner,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT owner, name, encrypted_data, salt, "
                    "encryption_version, created_at, updated_at "
                    "FROM credentials ORDER BY owner, name"
                ).fetchall()
            return [self._row_to_credential_row(r) for r in rows]
        finally:
            conn.close()
