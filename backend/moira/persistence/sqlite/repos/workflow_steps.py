import json
import logging
import sqlite3

from moira.persistence.interfaces import (
    WorkflowStep,
    WorkflowStepRepository,
)
from moira.persistence.sqlite.repos._connection import connect

logger = logging.getLogger(__name__)


class SqliteWorkflowStepRepository(WorkflowStepRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

    async def save_step(self, step: WorkflowStep) -> int:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO workflow_steps "
                "(workflow_run_id, node_name, label, status, cost, "
                "tool_call_cost, budget_remaining, started_at, elapsed_ms, "
                "step_version, tool_call_count, "
                "purpose, model, call_count, "
                "input_tokens, thinking_tokens, output_tokens, "
                "prompt_time_ms, gen_time_ms, "
                "error, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    step.workflow_run_id,
                    step.node_name,
                    step.label,
                    step.status,
                    step.cost,
                    step.tool_call_cost,
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
            assert cursor.lastrowid is not None
            return cursor.lastrowid
        finally:
            conn.close()

    async def get_steps_for_run(self, workflow_run_id: str) -> list[WorkflowStep]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, workflow_run_id, node_name, label, status, cost, "
                "tool_call_cost, budget_remaining, started_at, elapsed_ms, "
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
