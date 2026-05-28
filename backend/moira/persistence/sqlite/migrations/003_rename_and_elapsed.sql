-- Rename columns to match conceptual data model and add total_elapsed_ms.
-- This migration handles databases that were created with the old column names.
-- For fresh DBs (001_initial.sql or 002_workflow_runs.sql with new names),
-- these ALTERs are no-ops since columns already have the correct names.
-- We use a temporary column approach because SQLite doesn't support IF EXISTS on ALTER COLUMN.

-- Only rename if the old column name exists (check done in Python before executing).
-- See schema.py run_migrations() for the conditional logic.
