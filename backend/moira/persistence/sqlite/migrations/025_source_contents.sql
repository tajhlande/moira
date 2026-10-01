-- Source-content store: full fetched bodies with material-class flags.
--
-- Backs the retrieval-quality plan's "source-content foundation" (agent-docs/
-- retrieval-quality-plan.md Phase 2b). Citation.content remains a 5K-char
-- serving window into the model context; this table persists the much larger
-- pre-cap body (url_content returns up to 100K chars) so later phases
-- (summarize_source, passage retrieval) can deep-read what was actually
-- fetched, and run forensics can answer "what did the agent really have".
--
-- Composite PRIMARY KEY (run_id, url_hash): the store is per-run scoped, so
-- two runs fetching the same URL keep independent rows — a re-fetch by one
-- run must never clobber another run's stored body (get/evict scoping and
-- storage scoping have to agree). Cross-run body sharing is a separate policy
-- decision (cross-invocation memory) and stays out until taken deliberately.
-- No extra index on run_id: the composite PK's leftmost column already
-- covers run-scoped queries (get by run, evict_run).
CREATE TABLE source_contents (
    url_hash TEXT NOT NULL,
    url TEXT NOT NULL,
    run_id TEXT NOT NULL,
    citation_id TEXT,
    material_class TEXT NOT NULL,
    content TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'text/markdown',
    byte_size INTEGER NOT NULL,
    truncated INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (run_id, url_hash)
);
