import json
import logging
import sqlite3
from datetime import datetime, timezone

from moira.inference.metrics import (
    InferenceMetricsRepository,
    InferenceMetricsRow,
)
from moira.persistence.interfaces import (
    ApiSource,
    ApiSourceRepository,
    Conversation,
    ConversationRepository,
    CredentialRepository,
    CredentialRow,
    Message,
    ModelPreferences,
    ModelPreferencesRepository,
    SettingEntry,
    SystemSettingsRepository,
    ToolRepository,
    WorkflowRun,
    WorkflowStep,
    WorkflowStepRepository,
)
from moira.tools.base import ToolDefinition
from moira.tools.metrics import ToolMetricsRepository, ToolMetricsRow

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
                "tool_executions, report, budget_limit, budget_consumed, "
                "error, status, state_version, started_at, completed_at, "
                "updated_at, total_elapsed_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "tool_executions = excluded.tool_executions, "
                "report = excluded.report, "
                "budget_limit = excluded.budget_limit, "
                "budget_consumed = excluded.budget_consumed, "
                "error = excluded.error, "
                "status = excluded.status, "
                "state_version = excluded.state_version, "
                "completed_at = excluded.completed_at, "
                "updated_at = excluded.updated_at, "
                "total_elapsed_ms = excluded.total_elapsed_ms",
                (
                    run.id,
                    run.conversation_id,
                    run.user_message_id,
                    run.thread_id,
                    json.dumps(run.tool_executions),
                    json.dumps(run.report) if run.report else None,
                    run.budget_limit,
                    run.budget_consumed,
                    run.error,
                    run.status,
                    run.state_version,
                    run.started_at,
                    run.completed_at or None,
                    run.updated_at or None,
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
                "tool_executions, report, budget_limit, budget_consumed, "
                "error, status, state_version, started_at, completed_at, "
                "updated_at, total_elapsed_ms "
                "FROM workflow_runs WHERE conversation_id = ? ORDER BY started_at ASC",
                (conversation_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                te = d["tool_executions"]
                d["tool_executions"] = (
                    json.loads(te) if te else []
                )
                d["report"] = json.loads(d["report"]) if d["report"] else None
                d["state_version"] = d.get("state_version") or 1
                d["updated_at"] = d.get("updated_at") or d.get("completed_at") or d["started_at"]
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
            # workflow_steps references workflow_runs, so steps must go first.
            run_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM workflow_runs WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchall()
            ]
            for run_id in run_ids:
                conn.execute(
                    "DELETE FROM workflow_steps WHERE workflow_run_id = ?",
                    (run_id,),
                )
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

    async def cleanup_stale_runs(self) -> int:
        """Mark any runs and steps with status='running' as 'stopped'.

        Called at startup to clean up after an unclean shutdown (server crash,
        container restart, etc.). Running state is ephemeral — it only exists
        while the process is alive. Returns the number of runs cleaned up."""
        conn = self._connect()
        try:
            now = datetime.now(timezone.utc).isoformat()
            step_cursor = conn.execute(
                "UPDATE workflow_steps SET status = 'stopped', "
                "step_version = step_version + 1 "
                "WHERE status = 'running'"
            )
            step_count = step_cursor.rowcount
            run_cursor = conn.execute(
                "UPDATE workflow_runs SET status = 'stopped', "
                "error = 'Server restarted while run was in progress', "
                "state_version = state_version + 1, "
                "completed_at = ?, updated_at = ? "
                "WHERE status = 'running'",
                (now, now),
            )
            run_count = run_cursor.rowcount
            conn.commit()
            if run_count > 0 or step_count > 0:
                logger.info(
                    "Stale run cleanup: marked %d runs and %d steps as stopped",
                    run_count,
                    step_count,
                )
            return run_count
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

    async def delete_group(self, name: str) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM tool_groups WHERE name = ?", (name,)
            )
            conn.commit()
            return cursor.rowcount > 0
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


class SqliteToolMetricsRepository(ToolMetricsRepository):
    """Records tool call metrics in hourly buckets using atomic upsert.

    Each call increments counters in the row identified by
    (tool_name, call_type, period_hour). Latency is tracked via
    aggregate/min/max for outlier visibility without per-call rows."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

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
                    tool_name, call_type, period_hour,
                    success_inc, error_inc, duration_ms, duration_ms, duration_ms,
                    success_inc, error_inc, duration_ms, duration_ms, duration_ms,
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


class SqliteWorkflowStepRepository(WorkflowStepRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    async def save_step(self, step: WorkflowStep) -> int:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO workflow_steps "
                "(workflow_run_id, node_name, label, status, cost, "
                "budget_remaining, started_at, elapsed_ms, "
                "step_version, tool_call_count, "
                "purpose, model, call_count, "
                "input_tokens, thinking_tokens, output_tokens, "
                "prompt_time_ms, gen_time_ms, "
                "error, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    step.workflow_run_id,
                    step.node_name,
                    step.label,
                    step.status,
                    step.cost,
                    step.budget_remaining,
                    step.started_at,
                    step.elapsed_ms,
                    step.step_version,
                    step.tool_call_count,
                    step.purpose,
                    step.model,
                    step.call_count,
                    step.input_tokens,
                    step.thinking_tokens,
                    step.output_tokens,
                    step.prompt_time_ms,
                    step.gen_time_ms,
                    step.error,
                    json.dumps(step.detail) if step.detail else None,
                ),
            )
            conn.commit()
            assert (cursor.lastrowid is not None)
            return cursor.lastrowid
        finally:
            conn.close()

    async def get_steps_for_run(self, workflow_run_id: str) -> list[WorkflowStep]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, workflow_run_id, node_name, label, status, cost, "
                "budget_remaining, started_at, elapsed_ms, "
                "step_version, tool_call_count, "
                "purpose, model, call_count, "
                "input_tokens, thinking_tokens, output_tokens, "
                "prompt_time_ms, gen_time_ms, "
                "error, detail "
                "FROM workflow_steps WHERE workflow_run_id = ? ORDER BY id",
                (workflow_run_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["detail"] = json.loads(d["detail"]) if d["detail"] else None
                result.append(WorkflowStep(**d))
            return result
        finally:
            conn.close()


class SqliteInferenceMetricsRepository(InferenceMetricsRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

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


class SqliteSystemSettingsRepository(SystemSettingsRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

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


class SqliteApiSourceRepository(ApiSourceRepository):
    """SQLite-backed persistence for ingested API sources."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

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
            row = conn.execute(
                "SELECT * FROM api_sources WHERE id = ?", (source_id,)
            ).fetchone()
            return self._row_to_source(row) if row else None
        finally:
            conn.close()

    async def get_all(self) -> list[ApiSource]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM api_sources ORDER BY created_at DESC"
            ).fetchall()
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
            cursor = conn.execute(
                "DELETE FROM api_sources WHERE id = ?", (source_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
