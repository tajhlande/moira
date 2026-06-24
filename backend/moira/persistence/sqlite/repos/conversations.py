import json
import logging
import sqlite3
from datetime import datetime, timezone

from moira.persistence.interfaces import (
    Conversation,
    ConversationRepository,
    Message,
    WorkflowRun,
)
from moira.persistence.sqlite.repos._connection import connect

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
        return connect(self._db_path)

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
                "(id, conversation_id, user_message_id, status, "
                "budget_limit, total_cost, generation_reason, started_at, "
                "completed_at, total_elapsed_ms, updated_at, "
                "knowledge_snapshot, state_version, report, thread_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "status = excluded.status, "
                "budget_limit = excluded.budget_limit, "
                "total_cost = excluded.total_cost, "
                "generation_reason = excluded.generation_reason, "
                "completed_at = excluded.completed_at, "
                "total_elapsed_ms = excluded.total_elapsed_ms, "
                "updated_at = excluded.updated_at, "
                "knowledge_snapshot = excluded.knowledge_snapshot, "
                "state_version = excluded.state_version, "
                "report = excluded.report, "
                "thread_id = excluded.thread_id",
                (
                    run.id,
                    run.conversation_id,
                    run.user_message_id,
                    run.status,
                    run.budget_limit,
                    run.total_cost,
                    run.generation_reason,
                    run.started_at,
                    run.completed_at or None,
                    run.total_elapsed_ms or None,
                    run.updated_at or None,
                    run.knowledge_snapshot or None,
                    run.state_version,
                    json.dumps(run.report) if run.report else None,
                    run.thread_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def get_workflow_runs(self, conversation_id: str) -> list[WorkflowRun]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, conversation_id, user_message_id, status, "
                "budget_limit, total_cost, generation_reason, started_at, "
                "completed_at, total_elapsed_ms, updated_at, "
                "knowledge_snapshot, state_version, report, thread_id "
                "FROM workflow_runs WHERE conversation_id = ? ORDER BY started_at ASC",
                (conversation_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["state_version"] = d.get("state_version") or 1
                d["updated_at"] = d.get("updated_at") or d.get("completed_at") or d["started_at"]
                d["total_elapsed_ms"] = d["total_elapsed_ms"] or 0
                report_raw = d.pop("report", None)
                d["report"] = json.loads(report_raw) if report_raw else None
                result.append(WorkflowRun(**d))
            return result
        finally:
            conn.close()

    async def get_workflow_run(self, run_id: str) -> WorkflowRun | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, conversation_id, user_message_id, status, "
                "budget_limit, total_cost, generation_reason, started_at, "
                "completed_at, total_elapsed_ms, updated_at, "
                "knowledge_snapshot, state_version, report, thread_id "
                "FROM workflow_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["state_version"] = d.get("state_version") or 1
            d["updated_at"] = d.get("updated_at") or d.get("completed_at") or d["started_at"]
            d["total_elapsed_ms"] = d["total_elapsed_ms"] or 0
            report_raw = d.pop("report", None)
            d["report"] = json.loads(report_raw) if report_raw else None
            return WorkflowRun(**d)
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

    async def truncate_from_message(self, conversation_id: str, user_message_id: int) -> bool:
        """Delete workflow run for user_message_id and all messages/runs with
        id >= user_message_id. The user message itself is kept so it can be
        re-submitted as a rerun."""
        logger.debug(
            "Truncating conversation %s from message %d",
            conversation_id,
            user_message_id,
        )
        conn = self._connect()
        try:
            # Find all runs associated with messages >= user_message_id
            run_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM workflow_runs "
                    "WHERE conversation_id = ? AND user_message_id >= ?",
                    (conversation_id, user_message_id),
                ).fetchall()
            ]
            for run_id in run_ids:
                conn.execute(
                    "DELETE FROM workflow_steps WHERE workflow_run_id = ?",
                    (run_id,),
                )
            conn.execute(
                "DELETE FROM workflow_runs WHERE conversation_id = ? AND user_message_id >= ?",
                (conversation_id, user_message_id),
            )
            # Delete messages AFTER the user message (keep the user message itself)
            cursor = conn.execute(
                "DELETE FROM messages WHERE conversation_id = ? AND id > ?",
                (conversation_id, user_message_id),
            )
            deleted = cursor.rowcount > 0 or len(run_ids) > 0
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
