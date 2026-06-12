-- Migration 016: Add report column for persisting research reports
ALTER TABLE workflow_runs ADD COLUMN report TEXT;
