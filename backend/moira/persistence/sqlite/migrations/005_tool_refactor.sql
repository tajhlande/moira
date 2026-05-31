-- Migration 005: Refactor tool catalog.
-- Drops the v004 tools table and creates tool_groups + tools with the new
-- schema. The v004 tools table had no user data worth preserving since it
-- was just seeded at startup.

DROP TABLE IF EXISTS tools;

CREATE TABLE IF NOT EXISTS tool_groups (
    name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tools (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    argument_schema TEXT NOT NULL DEFAULT '{}',
    config TEXT NOT NULL DEFAULT '{}',
    tags TEXT NOT NULL DEFAULT '[]',
    reliability TEXT NOT NULL DEFAULT 'unknown',
    is_default INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    implementation TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (group_name) REFERENCES tool_groups(name)
);
