# Application Readiness for Home Deployment

## What works well enough for family testing

1. Core research loop — full 7-node workflow works, streaming UI works, reports render
2. Multi-turn conversations — prior Q&A carried forward to planning (just improved this session)
3. 3 built-in tools — calculator, web search (via SearXNG), URL content fetching
4. Stop/resume — just built and validated
5. Conversation management — create, rename, delete, AI-generated titles
6. Error surfacing — server errors now show actual cause in UI
7. Sidebar — running indicators, stop button for running conversations

## Issues that would bite family testers

1. Context overflow kills runs — the most common failure mode right now. PDF-heavy research hits 98K tokens. No graceful handling — just an error. The context-management.md plan addresses this but isn't implemented.
2. Stale runs on server restart — if the container restarts mid-run, the DB has status="running" that's never cleaned up. The implementation plan mentions this (Phase 3, item 7) but it's not built. Family testers would see ghost "running" conversations that never complete.
3. /api/health exists but at wrong path — it's at /api/health, but the Docker HEALTHCHECK in the deployment plan references it. This actually works fine — the path is correct.
4. Banner path is CWD-sensitive — main.py:27 opens moira/resources/banner.txt as a relative path. In Docker with a different WORKDIR, this will fail silently (caught by the try/except, but ugly). Not a blocker, just noisy logs.
5. No frontend build in production mode — the app currently only runs in dev mode. The Docker deployment plan adds static file serving, but that code doesn't exist yet.
6. CORS configured for localhost:5173 — same-origin deployment won't need CORS, but if anyone accesses the container directly on port 8000 from a browser, API calls will fail. Should add the deployed origin or null the CORS config for same-origin.

## Nice-to-haves that would improve the experience

7. Empty state for new users — the app shows a bare chat interface with "New Chat" button. A brief "Ask me a research question" prompt or intro text would help family members understand what to do.
8. Report rendering quality — reports render as markdown with code highlighting, but LaTeX in reports won't render (the latex-rendering.md plan isn't implemented).
9. LaTeX rendering — deferred, but any math-heavy questions will show raw LaTeX in reports.
Recommendations for a minimum viable deployment

## Must fix before deploying:

- Stale run cleanup on startup
- Context overflow handling (at minimum: graceful error + retry guidance, even without adaptive truncation)
- Frontend static build + serving (the Docker deployment plan)
- Banner path fix (trivial, avoids log noise)

## Should fix:

- CORS config update for production
- A welcoming empty state in the UI

## Can defer:

- Adaptive context truncation (the full plan)
- LaTeX rendering
- Two-pass discovery, tool secrets, MCP integration
- Everything else in Phase 3+