# Source fetch quality — parked

Two related deferred problems around `url_content` fetching, both
observed in live runs on 2026-10-03 (1881ee32, c7cacfd1). Neither is
active work; this note records the mechanism and the reasoning so they
can be picked up without re-derivation.

## 1. Cross-run source-content reuse

**Status:** parked (user decision, 2026-10-03).

Today each run refetches URLs from scratch: `fetched_urls` memory is
run-scoped, and the source store (`source_contents`) is forensics-only
(retention: 30-day age / 500M-char cap, Step 2 of the
retrieval-quality-amended plan). Run c7cacfd1 refetched a URL that run
1881ee32 had stored ~20 minutes earlier.

**Why parked (user reasoning):** reuse requires managing the *age* of
retrieved content, which is a problem in its own right:

- decide when to refresh stored content vs. serve it as-is
- flush content that is too old to trust
- fall back to old content when a refresh fails (stale-but-present vs.
  nothing)
- some questions are time-sensitive — material that is fine for a
  historical question is wrong for a current-events question, so a
  freshness policy has to be per-question/per-material, not global

Until there is a freshness model, "already fetched" carries no
reliable meaning across runs, so cross-run dedup stays off.

**Pickup notes:** the store side already has the primitives (per-URL
bodies keyed by url + run_id, `fetched_at` timestamps, Step 2 retention
sweeps). The missing piece is policy: freshness classification of
content, refresh/flush/fallback rules, and a way for a question to
declare time sensitivity (the decomposition node's fact_needed text is
a possible signal).

## 2. Soft fetch failures (200 OK, no extractable content)

**Status:** parked (user decision, 2026-10-03 — "acceptable for now").

Hard fetch failures classify cleanly (blocked/timeout/not_found/...,
Step 3 of the retrieval-quality-amended plan), but a bot-walled page
can return HTTP 200 HTML that extracts to nothing. Observed:
`link.springer.com` fetched "successfully" and extracted 209 chars —
title + URL header, no body. The model sees a success with near-empty
content and no signal that the fetch was effectively a failure.

**Why hard to address (user reasoning):** consistently *diagnosing*
this is the challenging part — a low extraction length can mean a
cookie/JS wall, a legitimately short page, or a page whose main content
is media. Any threshold will have false positives, and the response
differs per cause (give up on the host vs. retry vs. accept the page).

**Pickup notes:** signal would live in `url_content`'s success path —
extracted length vs. response size, presence of blocklist markers
(cookie-consent boilerplate, `<noscript>` walls). If picked up, extend
the failure-class list with a dedicated class rather than overloading
`parse:`, and consider feeding it into the blocked-host memory as a
soft block. Keep the `char_count` column of `source_contents` in mind
as calibration data for a length threshold.
