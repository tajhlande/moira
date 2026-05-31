-- Migration 006: Add built_in flag separate from is_default.
-- built_in = tools whose implementation and spec ship with MOiRA.
-- These tools are immutable except for the enabled flag.

ALTER TABLE tools ADD COLUMN built_in INTEGER NOT NULL DEFAULT 0;
