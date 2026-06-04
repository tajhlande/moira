CREATE TABLE workflow_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id),
    node_name TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    cost REAL NOT NULL DEFAULT 0,
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
    detail TEXT
);

CREATE INDEX idx_workflow_steps_run ON workflow_steps(workflow_run_id);
CREATE INDEX idx_workflow_steps_node ON workflow_steps(node_name);
CREATE INDEX idx_workflow_steps_purpose ON workflow_steps(purpose);
CREATE INDEX idx_workflow_steps_started ON workflow_steps(started_at);
