import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

CURRENT_VERSION = 8


def get_schema_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        conn.commit()
        return 0
    return row[0]


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("UPDATE schema_version SET version = ?", (version,))
    conn.commit()


def _apply_migration_003(conn: sqlite3.Connection) -> None:
    """Rename old columns to conceptual model names and add total_elapsed_ms.

    This migration is conditional: if the DB was created fresh (via 001 or 002
    that already use new column names), the renames are skipped. Only databases
    that have the old column names (steps, tool_calls, verification_reports,
    created_at) will be altered.
    """
    # Check if old column names exist by inspecting the workflow_runs schema
    cursor = conn.execute("PRAGMA table_info(workflow_runs)")
    columns = {row[1] for row in cursor.fetchall()}

    renames = [
        ("steps", "execution_steps"),
        ("tool_calls", "tool_executions"),
        ("verification_reports", "verification_attempts"),
        ("created_at", "started_at"),
    ]
    for old_name, new_name in renames:
        if old_name in columns and new_name not in columns:
            conn.execute(f"ALTER TABLE workflow_runs RENAME COLUMN {old_name} TO {new_name}")
            logger.info("Renamed workflow_runs.%s → %s", old_name, new_name)

    if "total_elapsed_ms" not in columns:
        conn.execute("ALTER TABLE workflow_runs ADD COLUMN total_elapsed_ms INTEGER")
        logger.info("Added workflow_runs.total_elapsed_ms")


def run_migrations(db_path: str) -> None:
    logger.info("Running migrations for %s", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        current = get_schema_version(conn)
        logger.info("Current schema version: %d, target: %d", current, CURRENT_VERSION)
        for version in range(current + 1, CURRENT_VERSION + 1):
            if version == 3:
                _apply_migration_003(conn)
            else:
                matches = sorted(Path(MIGRATIONS_DIR).glob(f"{version:03d}_*.sql"))
                if not matches:
                    raise RuntimeError(f"Migration file not found for version {version}")
                sql = matches[0].read_text()
                conn.executescript(sql)
            set_schema_version(conn, version)
            logger.info("Applied migration version %d", version)
    finally:
        conn.close()
