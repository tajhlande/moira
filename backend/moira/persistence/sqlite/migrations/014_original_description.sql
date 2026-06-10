ALTER TABLE tools ADD COLUMN original_description TEXT;

-- Backfill: set original_description = description for all existing rows.
-- For existing tools, there was no distinction, so the current description
-- is both the original and the user-facing text.
UPDATE tools SET original_description = description WHERE original_description IS NULL;
