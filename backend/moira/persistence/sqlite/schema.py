import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

CURRENT_VERSION = 16


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


def _apply_migration_009(conn: sqlite3.Connection) -> None:
    """Create workflow_steps table, migrate data from JSON blobs, drop old columns.

    Reads execution_steps JSON blobs from workflow_runs and inserts each step
    as a row in workflow_steps. Verification reports are already embedded in
    verification steps' detail.structured_output, so verification_attempts
    is redundant and not separately migrated.

    Drops execution_steps, verification_attempts, and the unused thinking_traces
    columns after migration. Steps are migrated with NULL for purpose, model,
    call_count, and token columns since that data was not captured previously.
    """
    cursor = conn.execute("PRAGMA table_info(workflow_runs)")
    columns = {row[1] for row in cursor.fetchall()}

    if "execution_steps" not in columns:
        logger.info(
            "Migration 009: execution_steps column already absent, skipping data migration"
        )
        return

    rows = conn.execute("SELECT id, execution_steps FROM workflow_runs").fetchall()

    step_count = 0
    for run_id, steps_json in rows:
        steps = json.loads(steps_json) if steps_json else []
        for step in steps:
            detail = step.get("detail")
            conn.execute(
                """INSERT INTO workflow_steps
                   (workflow_run_id, node_name, label, status, cost,
                    budget_remaining, started_at, elapsed_ms, error, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    step.get("node", ""),
                    step.get("label", ""),
                    step.get("status", "completed"),
                    step.get("cost", 0),
                    step.get("budget_remaining", 0),
                    step.get("started_at", ""),
                    step.get("elapsed_ms", 0),
                    step.get("error", ""),
                    json.dumps(detail) if detail else None,
                ),
            )
            step_count += 1

    logger.info(
        "Migration 009: migrated %d steps from %d workflow_runs",
        step_count,
        len(rows),
    )

    columns_to_drop = []
    for col in ("execution_steps", "verification_attempts", "thinking_traces"):
        if col in columns:
            columns_to_drop.append(col)

    for col in columns_to_drop:
        try:
            conn.execute(f"ALTER TABLE workflow_runs DROP COLUMN {col}")
            logger.info("Migration 009: dropped workflow_runs.%s", col)
        except sqlite3.OperationalError:
            logger.info(
                "Migration 009: could not drop %s (SQLite < 3.35 does not support DROP COLUMN)",
                col,
            )

    conn.commit()


def _apply_migration_011(conn: sqlite3.Connection) -> None:
    """Add run/step versioning and updated_at tracking columns.

    - workflow_runs: state_version, updated_at
    - workflow_steps: step_version, tool_call_count
    """
    run_columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_runs)").fetchall()}
    if "state_version" not in run_columns:
        conn.execute(
            "ALTER TABLE workflow_runs ADD COLUMN state_version INTEGER NOT NULL DEFAULT 1"
        )
        logger.info("Added workflow_runs.state_version")
    if "updated_at" not in run_columns:
        conn.execute("ALTER TABLE workflow_runs ADD COLUMN updated_at TEXT")
        logger.info("Added workflow_runs.updated_at")
    conn.execute(
        "UPDATE workflow_runs SET updated_at = COALESCE(updated_at, completed_at, started_at)"
    )

    step_columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_steps)").fetchall()}
    if "step_version" not in step_columns:
        conn.execute(
            "ALTER TABLE workflow_steps ADD COLUMN step_version INTEGER NOT NULL DEFAULT 1"
        )
        logger.info("Added workflow_steps.step_version")
    if "tool_call_count" not in step_columns:
        conn.execute(
            "ALTER TABLE workflow_steps ADD COLUMN tool_call_count INTEGER NOT NULL DEFAULT 0"
        )
        logger.info("Added workflow_steps.tool_call_count")

    rows = conn.execute("SELECT id, detail FROM workflow_steps").fetchall()
    for step_id, detail_json in rows:
        tool_count = 0
        if detail_json:
            try:
                detail = json.loads(detail_json)
                tool_results = detail.get("tool_results", [])
                if isinstance(tool_results, list):
                    tool_count = len(tool_results)
            except json.JSONDecodeError:
                pass
        conn.execute(
            "UPDATE workflow_steps SET tool_call_count = ? WHERE id = ?",
            (tool_count, step_id),
        )

    conn.commit()


def run_migrations(db_path: str) -> None:
    """Apply all pending schema migrations to the SQLite database at db_path."""
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
            elif version == 9:
                sql_file = sorted(Path(MIGRATIONS_DIR).glob("009_*.sql"))[0]
                conn.executescript(sql_file.read_text())
                _apply_migration_009(conn)
            elif version == 11:
                _apply_migration_011(conn)
            else:
                matches = sorted(Path(MIGRATIONS_DIR).glob(f"{version:03d}_*.sql"))
                if not matches:
                    raise RuntimeError(f"Migration file not found for version {version}")
                sql = matches[0].read_text()
                conn.executescript(sql)
            set_schema_version(conn, version)
            logger.info("Applied migration version %d", version)

        _repair_migration_009(conn)
        _backfill_inference_metrics(conn)
    finally:
        conn.close()


def _backfill_inference_metrics(conn: sqlite3.Connection) -> None:
    """Aggregate existing workflow_steps into inference_metrics hourly
    buckets. Safe to call multiple times — only processes runs that have
    steps not yet reflected in inference_metrics."""
    existing_tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "inference_metrics" not in existing_tables or "workflow_steps" not in existing_tables:
        return

    # Check if backfill already done by looking for any rows.
    # If inference_metrics already has data, assume backfill completed.
    count = conn.execute("SELECT COUNT(*) FROM inference_metrics").fetchone()[0]
    if count > 0:
        return

    conn.row_factory = sqlite3.Row
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT model, purpose, started_at, call_count, "
        "input_tokens, output_tokens, thinking_tokens, "
        "prompt_time_ms, gen_time_ms "
        "FROM workflow_steps "
        "WHERE model IS NOT NULL AND purpose IS NOT NULL"
    ).fetchall()

    if not rows:
        return

    buckets: dict[tuple[str, str, str], list[int | float]] = {}
    for r in rows:
        started = r["started_at"] or ""
        hour = started[:13] + ":00" if len(started) >= 13 else started
        key = (r["model"], r["purpose"], hour)
        acc = buckets.get(key, [0, 0, 0, 0, 0.0, 0.0])
        acc[0] += r["call_count"] or 0
        acc[1] += r["input_tokens"] or 0
        acc[2] += r["output_tokens"] or 0
        acc[3] += r["thinking_tokens"] or 0
        acc[4] += r["prompt_time_ms"] or 0.0
        acc[5] += r["gen_time_ms"] or 0.0
        buckets[key] = acc

    for (model, purpose, period_hour), vals in buckets.items():
        conn.execute(
            "INSERT INTO inference_metrics "
            "(model, purpose, period_hour, call_count, "
            "input_tokens, output_tokens, thinking_tokens, "
            "prompt_time_ms, gen_time_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (model, purpose, period_hour, *vals),
        )
    conn.commit()
    logger.info(
        "Backfill: aggregated %d workflow_steps into %d inference_metrics rows",
        len(rows),
        len(buckets),
    )


def _repair_migration_009(conn: sqlite3.Connection) -> None:
    """Ensure workflow_steps table has all required columns and re-migrate
    data from execution_steps JSON blobs if needed. Safe to call multiple
    times."""

    # Ensure the table exists (handles databases where CREATE TABLE
    # was rolled back due to a later failure in _apply_migration_009).
    existing_tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "workflow_steps" not in existing_tables:
        sql_file = sorted(Path(MIGRATIONS_DIR).glob("009_*.sql"))[0]
        conn.executescript(sql_file.read_text())

    # Add any missing columns (handles partial table creation on
    # databases where the SQL file was rolled back but the table was
    # created with fewer columns).
    step_cols = {r[1] for r in conn.execute("PRAGMA table_info(workflow_steps)").fetchall()}
    for col, definition in [
        ("purpose", "TEXT"),
        ("model", "TEXT"),
        ("call_count", "INTEGER"),
        ("input_tokens", "INTEGER"),
        ("thinking_tokens", "INTEGER"),
        ("output_tokens", "INTEGER"),
        ("prompt_time_ms", "REAL"),
        ("gen_time_ms", "REAL"),
        ("step_version", "INTEGER NOT NULL DEFAULT 1"),
        ("tool_call_count", "INTEGER NOT NULL DEFAULT 0"),
    ]:
        if col not in step_cols:
            conn.execute(f"ALTER TABLE workflow_steps ADD COLUMN {col} {definition}")
            logger.info("Repair 009: added workflow_steps.%s", col)

    # Re-migrate data from execution_steps blobs for runs that have
    # no rows in workflow_steps yet.
    cursor = conn.execute("PRAGMA table_info(workflow_runs)")
    columns = {row[1] for row in cursor.fetchall()}

    if "execution_steps" not in columns:
        return

    runs = conn.execute(
        "SELECT r.id, r.execution_steps FROM workflow_runs r "
        "LEFT JOIN (SELECT DISTINCT workflow_run_id FROM workflow_steps) ws "
        "ON r.id = ws.workflow_run_id "
        "WHERE ws.workflow_run_id IS NULL AND r.execution_steps IS NOT NULL"
    ).fetchall()

    if not runs:
        return

    logger.info("Repair 009: found %d runs with unmigrated steps", len(runs))
    step_count = 0
    for run_id, steps_json in runs:
        steps = json.loads(steps_json) if steps_json else []
        for step in steps:
            detail = step.get("detail")
            conn.execute(
                """INSERT INTO workflow_steps
                   (workflow_run_id, node_name, label, status, cost,
                    budget_remaining, started_at, elapsed_ms, error, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    step.get("node", ""),
                    step.get("label", ""),
                    step.get("status", "completed"),
                    step.get("cost", 0),
                    step.get("budget_remaining", 0),
                    step.get("started_at", ""),
                    step.get("elapsed_ms", 0),
                    step.get("error", ""),
                    json.dumps(detail) if detail else None,
                ),
            )
            step_count += 1
    conn.commit()
    logger.info("Repair 009: migrated %d steps from %d runs", step_count, len(runs))
