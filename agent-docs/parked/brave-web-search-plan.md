# Brave Web Search Tool — Implementation Plan

## Summary

Add a `brave_web_search` built-in tool using the [Brave Search API](https://api.search.brave.com/). Follows the parked Kagi blueprint (`agent-docs/parked/kagi-web-search-plan.md`) — coexists with SearXNG-based `web_search`, uses the existing credential framework (`tool.*` namespace).

## Brave API Reference

**Endpoint**: `GET https://api.search.brave.com/res/v1/web/search`

**Auth**: `X-Subscription-Token: <API_KEY>` header

**Key query parameters**:
| Param            | Type   | Description                                                                 |
|------------------|--------|-----------------------------------------------------------------------------|
| `q`              | string | Search query (required)                                                     |
| `count`          | int    | Max results per page (max 20, default 20)                                   |
| `offset`         | int    | Pagination offset (0-based, max 9)                                          |
| `safesearch`     | string | `off`, `moderate` (default), `strict`                                       |
| `freshness`      | string | `pd` (24h), `pw` (7d), `pm` (31d), `py` (year), or `YYYY-MM-DDtoYYYY-MM-DD` |
| `country`        | string | 2-char country code (e.g., `US`)                                            |
| `search_lang`    | string | Content language filter                                                     |
| `extra_snippets` | bool   | Up to 5 additional excerpts per result                                      |

**Response** (`200`):
```json
{
  "web": {
    "results": [
      {
        "title": "Result Title",
        "url": "https://example.com",
        "description": "Main snippet text.",
        "extra_snippets": ["Additional excerpt 1", "excerpt 2"],
        "age": "3 days"
      }
    ]
  },
  "query": {
    "original": "search query",
    "more_results_available": true
  }
}
```

**Pricing**: Free tier 2,000 queries/month. Paid: $5/month includes 2K queries, then $0.003/query.

## Differences from Kagi Plan

| Aspect                | Kagi                            | Brave                                           |
|-----------------------|---------------------------------|-------------------------------------------------|
| HTTP method           | POST                            | GET                                             |
| Auth header           | `Authorization: Bearer <token>` | `X-Subscription-Token: <key>`                   |
| Response results path | `data.search[]`                 | `web.results[]`                                 |
| Summarizer workflow   | Yes (`workflow=summarize`)      | No                                              |
| Inline extraction     | Yes (`extract` param)           | No (use existing `url_content` tool)            |
| Result fields         | `title`, `url`, `snippet`       | `title`, `url`, `description`, `extra_snippets` |

## Tool Specification

```python
tool_name = "brave_web_search"
tool_group = "standard"
tool_description = (
    "Search the web using the Brave Search API. Returns high-quality "
    "results from an independent index with titles, URLs, and content "
    "snippets. Requires a Brave API key."
)
tool_argument_schema = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "The search query."},
        "max_results": {
            "type": "integer",
            "description": "Maximum results to return (1-20).",
            "default": 10,
        },
        "freshness": {
            "type": "string",
            "enum": ["pd", "pw", "pm", "py"],
            "description": "Filter by recency: past day (pd), week (pw), month (pm), or year (py).",
        },
    },
    "required": ["query"],
}
tool_secret_schema = {
    "type": "object",
    "properties": {
        "api_key": {
            "type": "string",
            "description": "Brave Search API subscription token from https://api-dashboard.search.brave.com/",
        },
    },
    "required": ["api_key"],
}
```

## Credential Wiring

Credential name: `tool.brave_web_search.api_key`

Binding flow (same as Kagi plan):
1. Tool registered in `STANDARD_TOOLS` via `BraveWebSearchTool.make_definition()`
2. User stores API key: `POST /api/credentials` with `name="tool.brave_web_search.api_key"`, `value={"key": "xxx"}`
3. At `execute()` time, reads credential via `service_provider("credential_service")`

## Caching

Reuse the existing `SearchCache` (SQLite, `data/search_cache.db`) with a namespace prefix to avoid collisions with SearXNG cached results. The cache key should include `"brave"` in the engine field so the same query on SearXNG vs Brave doesn't collide.

Alternatively, the Brave tool can share the cache since queries are query-keyed and results differ anyway. But this would mean a SearXNG result for "telescope mount cost" would be returned for a Brave query too, which is wrong. **Decision: separate cache namespace.**

## Files to Create

| File | Description |
|------|-------------|
| `backend/moira/tools/builtin/brave_web_search.py` | `BraveWebSearchTool` implementation |
| `backend/tests/test_brave_web_search.py` | Tests (mocked HTTP + mocked credentials) |

## Files to Modify

| File | Change |
|------|--------|
| `backend/moira/tools/standard.py` | Add `BraveWebSearchTool.make_definition()` to `STANDARD_TOOLS` |

## Implementation Details

### execute() method

1. Resolve API key from credential store (fail fast with helpful message if missing)
2. Build query params: `q`, `count`, `offset`, `safesearch=moderate`
3. GET `https://api.search.brave.com/res/v1/web/search` with `X-Subscription-Token` header
4. Parse `web.results[]`, extract `title`, `url`, `description` (+ `extra_snippets` if present)
5. Format results identically to `WebSearchTool._format_results()`
6. Return `ToolResult`

### Result formatting

Same format as `web_search`:
```
[1] Title of First Result
    URL: https://example.com/page1
    Snippet: A short summary...

[2] Title of Second Result
    URL: https://example.com/page2
    Snippet: Another summary...
```

### Error handling

| Condition | Response |
|-----------|----------|
| No API key | `_fail("Brave API key not configured. Store it as credential 'tool.brave_web_search.api_key'.")` |
| HTTP 401 | `_fail("Brave API key is invalid.")` |
| HTTP 429 | `_fail("Brave API rate limited. Try again later.")` |
| HTTP 5xx | `_fail("Brave API server error.")` |
| Timeout | `_fail("Brave API request timed out.")` |
| Connection error | `_fail("Cannot reach Brave API.")` |

### Tool registration

```python
# In standard.py
BraveWebSearchTool.make_definition(
    invocation_cost=5.0, call_limit_per_run=10, call_limit_per_step=5
),
```

Same limits as `web_search` — 10 calls per run, 5 per step.

## Test Plan

1. **Successful search** — mock 200 with `web.results`, verify formatted output
2. **Missing API key** — credential service returns `None`, verify error message
3. **Invalid API key** — mock 401, verify error message
4. **Rate limited** — mock 429, verify error message
5. **Empty results** — mock 200 with empty `web.results`, verify "No results" message
6. **Max results limiting** — request with `max_results=5` when API returns 20, verify truncation
7. **Extra snippets** — mock response with `extra_snippets`, verify they're included in output
8. **Freshness parameter** — request with `freshness="pw"`, verify param is sent
9. **Tool definition** — verify `make_definition()` produces correct `ToolDefinition`
10. **Secret schema** — verify `get_spec()` returns expected secret_schema

## Implementation Order

1. Create `backend/moira/tools/builtin/brave_web_search.py`
2. Add to `STANDARD_TOOLS` in `backend/moira/tools/standard.py`
3. Write tests in `backend/tests/test_brave_web_search.py`
4. Run full test suite + ruff
5. Store API key via credentials API
6. Manual test against live Brave API

## Open Questions

1. **Cache namespace**: Should Brave results share the `SearchCache` or use a separate one? (Leaning: same SQLite DB, different namespace prefix in cache key)
2. **Should both web_search and brave_web_search be enabled simultaneously for eval?** For eval consistency, might want to disable SearXNG-based `web_search` so all runs use Brave. But for production use, both should coexist.
3. **Tool description**: Should brave_web_search's description hint that it's preferred over web_search? Or let the agent decide freely?
