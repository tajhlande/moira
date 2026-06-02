-- Migration 007: Add credentials table for encrypted credential storage.

CREATE TABLE IF NOT EXISTS credentials (
    owner TEXT NOT NULL DEFAULT 'system',
    name TEXT NOT NULL,
    encrypted_data TEXT NOT NULL,
    salt TEXT NOT NULL,
    encryption_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (owner, name)
);
