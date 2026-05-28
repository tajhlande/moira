-- Adds the workflow_runs table for persisting LangGraph research workflow artifacts.
-- This table was added to 001_initial.sql after some databases had already
-- been migrated to version 1, so existing DBs need this incremental migration.
-- Column names match the current conceptual model (renamed in migration 003).

CREATE TABLE IF NOT EXISTS workflow_runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    user_message_id INTEGER NOT NULL REFERENCES messages(id),
    thread_id TEXT NOT NULL,
    execution_steps TEXT NOT NULL DEFAULT '[]',
    tool_executions TEXT NOT NULL DEFAULT '[]',
    verification_attempts TEXT NOT NULL DEFAULT '[]',
    thinking_traces TEXT NOT NULL DEFAULT '{}',
    report TEXT,
    budget_limit REAL NOT NULL DEFAULT 0,
    budget_consumed REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    total_elapsed_ms INTEGER
);
