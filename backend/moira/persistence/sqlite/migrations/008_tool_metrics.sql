CREATE TABLE tool_metrics (
    tool_name TEXT NOT NULL,
    call_type TEXT NOT NULL DEFAULT 'default',
    period_hour TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    aggregate_duration_ms INTEGER NOT NULL DEFAULT 0,
    low_duration_ms INTEGER NOT NULL DEFAULT 0,
    high_duration_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tool_name, call_type, period_hour)
);
