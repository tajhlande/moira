-- Bump the system-wide default budget from 100 to 150.
--
-- The previous default of 100 was set before per-call tool costs were
-- enforced. With web_search at 5.0/call and url_content at 3.0/call, a
-- serious investigation easily spends 40-60 in tools alone, leaving the
-- agent cut off mid-investigation and unable to take the review retries
-- it is configured for (the routers gate retries on budget thresholds).
-- 150 fits a full research cycle (~28 step cost) plus 1-2 review retries
-- with realistic tool spend.
--
-- This migration is conditional: only rows still at exactly the old
-- default (100) are updated, so user customizations (e.g. 120, 130) are
-- preserved. seed_defaults() is insert-only and would not touch existing
-- rows on its own, so without this migration existing deployments would
-- keep running at 100 indefinitely.
UPDATE settings
SET value = '150'
WHERE key = 'budget.default_limit'
  AND value = '100';
