-- Migration 017: Add thread_id column for LangGraph checkpoint resumption.
--
-- The thread_id is generated per-run (UUID) and passed to the graph config
-- so the LangGraph checkpointer (SqliteSaver) can store and retrieve
-- checkpoint state.  Without persisting it, resume_run has no way to
-- locate the correct checkpoint and falls back to thread_id="" which
-- loads the wrong (or empty) state.
ALTER TABLE workflow_runs ADD COLUMN thread_id TEXT NOT NULL DEFAULT '';
