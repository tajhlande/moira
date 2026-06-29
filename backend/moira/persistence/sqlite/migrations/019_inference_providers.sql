-- Migration 019: Move inference provider configuration to database.
--
-- SUPERSEDED by migration 020, which drops and recreates these tables
-- with slug + display_name replacing the single `name` column. This file
-- is retained for migration history integrity — do not modify.
--
-- Providers are now DB-backed and runtime-manageable via the API.
-- API keys are stored in the encrypted credentials table, referenced
-- by credential_name. This table holds only connection metadata.

CREATE TABLE IF NOT EXISTS inference_providers (
    name            TEXT PRIMARY KEY,
    base_url        TEXT NOT NULL,
    provider_type   TEXT NOT NULL DEFAULT 'completions',
    credential_name TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inference_models (
    provider_name       TEXT NOT NULL REFERENCES inference_providers(name) ON DELETE CASCADE,
    model_id            TEXT NOT NULL,
    native_tool_calling INTEGER NOT NULL DEFAULT 0,
    discovered_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (provider_name, model_id)
);
