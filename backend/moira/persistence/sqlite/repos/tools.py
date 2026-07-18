import json
import logging
import sqlite3

from moira.persistence.interfaces import (
    ToolRepository,
)
from moira.persistence.sqlite.repos._connection import connect
from moira.tools.base import ToolDefinition

logger = logging.getLogger(__name__)


class SqliteToolRepository(ToolRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_path)

    @staticmethod
    def _row_to_defn(row: sqlite3.Row) -> ToolDefinition:
        invocation_cost = row["invocation_cost"] if "invocation_cost" in row.keys() else 1.0
        call_limit = row["call_limit_per_run"] if "call_limit_per_run" in row.keys() else 0
        call_limit_step = row["call_limit_per_step"] if "call_limit_per_step" in row.keys() else 0
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
            original_description=(
                row["original_description"] if "original_description" in row.keys() else ""
            ),
            invocation_cost=float(invocation_cost),
            call_limit_per_run=int(call_limit) if call_limit is not None else 0,
            call_limit_per_step=int(call_limit_step) if call_limit_step is not None else 0,
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
                    implementation, group_name, original_description,
                    invocation_cost, call_limit_per_run, call_limit_per_step)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    tool.original_description,
                    tool.invocation_cost,
                    tool.call_limit_per_run,
                    tool.call_limit_per_step,
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
            cursor = conn.execute("DELETE FROM tool_groups WHERE name = ?", (name,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
