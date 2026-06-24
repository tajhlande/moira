"""Shared SQLite connection helper for all repository classes.

Each repository delegates ``_connect`` here to avoid duplicating the
WAL-mode and foreign-keys PRAGMA boilerplate across nine classes.
"""

import sqlite3


def connect(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and foreign keys enabled.

    A new connection is opened per operation. This avoids connection-management
    complexity and works with async code (sqlite3 is synchronous, so each call
    blocks the event loop briefly). If latency becomes an issue, consider
    aiosqlite.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
