import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

CURRENT_VERSION = 1


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


def run_migrations(db_path: str) -> None:
    logger.info("Running migrations for %s", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        current = get_schema_version(conn)
        logger.info("Current schema version: %d, target: %d", current, CURRENT_VERSION)
        for version in range(current + 1, CURRENT_VERSION + 1):
            # Expects one SQL file per version (e.g., 001_initial.sql).
            # If multiple files match, sorted() + [0] silently picks the
            # lexicographically first one.
            matches = sorted(Path(MIGRATIONS_DIR).glob(f"{version:03d}_*.sql"))
            if not matches:
                raise RuntimeError(f"Migration file not found for version {version}")
            sql = matches[0].read_text()
            conn.executescript(sql)
            set_schema_version(conn, version)
            logger.info("Applied migration version %d", version)
    finally:
        conn.close()
