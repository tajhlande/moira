-- Migration 020: Replace provider name PK with slug + display_name.
--
-- The original schema used `name` as both the human-readable display name
-- and the primary key. This migration splits it into:
--   slug         — kebab-case internal identifier (immutable PK)
--   display_name — human-readable name (editable)
--
-- Existing provider data is wiped. Users re-enter providers via the UI.
-- Model assignments (model_preferences) are reset since they referenced
-- the old name-based identifiers.

DROP TABLE IF EXISTS inference_models;
DROP TABLE IF EXISTS inference_providers;

CREATE TABLE inference_providers (
    slug            TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    base_url        TEXT NOT NULL,
    provider_type   TEXT NOT NULL DEFAULT 'completions',
    credential_name TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE inference_models (
    provider_slug       TEXT NOT NULL REFERENCES inference_providers(slug) ON DELETE CASCADE,
    model_id            TEXT NOT NULL,
    native_tool_calling INTEGER NOT NULL DEFAULT 0,
    discovered_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (provider_slug, model_id)
);

-- Reset stale endpoint references that used old display-name identifiers
UPDATE model_preferences SET intelligence_endpoint = '', intelligence_model = '';
UPDATE model_preferences SET task_endpoint = '', task_model = '';
