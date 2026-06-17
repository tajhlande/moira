-- Migration 015: Overhauled research loop schema
-- Drops and recreates workflow_runs and workflow_steps with new columns.
-- Adds invocation_cost and call_limit_per_run to tools.

-- Drop old tables (data from old loop is not migrated)
DROP TABLE IF EXISTS workflow_steps;
DROP TABLE IF EXISTS workflow_runs;

CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    user_message_id INTEGER NOT NULL REFERENCES messages(id),
    status TEXT NOT NULL DEFAULT 'running',
    budget_limit REAL NOT NULL,
    total_cost REAL NOT NULL DEFAULT 0,
    generation_reason TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    total_elapsed_ms INTEGER,
    updated_at TEXT,
    knowledge_snapshot TEXT,
    state_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE workflow_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id),
    node_name TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    cost REAL NOT NULL DEFAULT 0,
    tool_call_cost REAL NOT NULL DEFAULT 0,
    budget_remaining REAL NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    purpose TEXT,
    model TEXT,
    call_count INTEGER,
    input_tokens INTEGER,
    thinking_tokens INTEGER,
    output_tokens INTEGER,
    prompt_time_ms REAL,
    gen_time_ms REAL,
    error TEXT NOT NULL DEFAULT '',
    detail TEXT,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    step_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_workflow_steps_run_id ON workflow_steps(workflow_run_id);
CREATE INDEX idx_workflow_steps_node_name ON workflow_steps(node_name);
CREATE INDEX idx_workflow_steps_purpose ON workflow_steps(purpose);
CREATE INDEX idx_workflow_steps_started_at ON workflow_steps(started_at);

-- Add tool invocation cost and call limit columns
ALTER TABLE tools ADD COLUMN invocation_cost REAL NOT NULL DEFAULT 1.0;
ALTER TABLE tools ADD COLUMN call_limit_per_run INTEGER;

-- Update specific tool costs
UPDATE tools SET invocation_cost = 5.0, call_limit_per_run = 10 WHERE name = 'web_search';
UPDATE tools SET invocation_cost = 3.0, call_limit_per_run = 15 WHERE name = 'url_content';
UPDATE tools SET invocation_cost = 0.1 WHERE name = 'calculator';
UPDATE tools SET invocation_cost = 0.1 WHERE name = 'datetime';
