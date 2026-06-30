-- Migration 021: Per-conversation intelligence model override table.
--
-- Stores an optional per-conversation override for the intelligence model.
-- When a row exists, its endpoint + model are used instead of the global
-- default from model_preferences. Task model is always global — only
-- intelligence is overridable per conversation.

CREATE TABLE IF NOT EXISTS conversation_models (
    conversation_id       TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    intelligence_endpoint TEXT NOT NULL,
    intelligence_model    TEXT NOT NULL,
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
