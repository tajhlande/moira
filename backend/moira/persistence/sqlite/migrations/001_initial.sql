CREATE TABLE users (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE model_preferences (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    intelligence_endpoint TEXT NOT NULL DEFAULT '',
    intelligence_model TEXT NOT NULL DEFAULT '',
    task_endpoint TEXT NOT NULL DEFAULT '',
    task_model TEXT NOT NULL DEFAULT ''
);

CREATE TABLE workflow_runs (
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

INSERT INTO users (id) VALUES ('default');
