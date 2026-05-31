-- Migration 004: Tool catalog table.
-- Stores all tools (built-in and user-added). Built-in tools are seeded at
-- startup and are immutable (cannot be created/deleted by users).

CREATE TABLE IF NOT EXISTS tools (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    tool_type TEXT NOT NULL DEFAULT 'rest',
    argument_schema TEXT NOT NULL DEFAULT '{}',
    endpoint TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT 'GET',
    tags TEXT NOT NULL DEFAULT '[]',
    reliability TEXT NOT NULL DEFAULT 'unknown',
    is_builtin INTEGER NOT NULL DEFAULT 0,
    tool_group TEXT NOT NULL DEFAULT ''
);
