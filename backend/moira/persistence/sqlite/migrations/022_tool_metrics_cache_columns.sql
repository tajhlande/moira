-- Add cache hit/miss counters to tool_metrics so web_search cache
-- effectiveness can be tracked over time. Existing rows default to 0.
-- Only the web_search tool populates these columns today; other tools
-- record 0 in both fields.
ALTER TABLE tool_metrics ADD COLUMN cache_hits INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tool_metrics ADD COLUMN cache_misses INTEGER NOT NULL DEFAULT 0;
