-- Migration 013: API sources table for tracking ingested external APIs.

CREATE TABLE IF NOT EXISTS api_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    spec_url TEXT,
    spec_format TEXT NOT NULL,
    auth_type TEXT,
    group_name TEXT NOT NULL,
    tool_count INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
