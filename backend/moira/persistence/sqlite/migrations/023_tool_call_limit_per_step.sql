-- Add call_limit_per_step column to the tools table.
-- This caps how many times a tool can be invoked within a single
-- research node execution, independent of the per-run limit.
-- 0 means unlimited (no per-step cap).  This directly targets the
-- multi-round query-expansion problem where the model issues many
-- web_search calls within one research step.
ALTER TABLE tools ADD COLUMN call_limit_per_step INTEGER NOT NULL DEFAULT 0;
