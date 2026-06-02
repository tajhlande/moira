# Kagi Web Search Tool — Implementation Plan

## Summary

Add a `kagi_web_search` built-in tool that uses the [Kagi Search API](https://kagi.com/api/docs/openapi) for web search. This is the first tool that requires an API key stored in the credential store, making it the proving ground for the tool-to-credential binding pattern described in `tool-secrets-and-spec.md`.

The Kagi API is a paid service ($12/1k search requests, $4/1k extract pages). Authentication uses a Bearer token.

## Design Decisions

### 1. Use `httpx` directly, not the Kagi OpenAPI client

The Kagi team publishes an [OpenAPI-generated Python client](https://github.com/kagisearch/kagi-openapi-python), but we will use `httpx` directly against `https://kagi.com/api/v1/search` with Bearer auth instead.

Rationale:
1. The Kagi Search API is a single POST endpoint with JSON in/out — the generated client adds complexity without meaningful benefit.
2. The PyPI package is named `openapi-client`, which is extremely generic and risks namespace collisions.
3. `httpx` is already a project dependency (used by `WebSearchTool` and `InferenceClient`).
4. The generated client uses synchronous I/O internally (`urllib3`-based), which would require wrapping in `asyncio.to_thread` — adding latency and complexity. Using `httpx.AsyncClient` natively is simpler and faster.
5. The request/response shapes are trivially constructible as dicts.

If the Kagi API grows significantly in scope (many endpoints, complex auth flows), we can migrate to the client library later.

### 2. One tool, two workflows: `search` and `summarize`

The Kagi Search API supports a `workflow` parameter:
- `"search"` (default) — returns ranked search results
- `"summarize"` — returns a Kagi-generated summary (Kagi's "Universal Summarizer")

We expose `workflow` as an optional argument to the tool. The LLM can request a summary when it wants a synthesized answer rather than raw results. This is more useful to the research agent than a separate tool.

**Decision**: Single tool `kagi_web_search` with a `workflow` parameter, not two separate tools. The LLM decides which workflow to use based on the query.

### 3. Two-phase credential binding (minimal version)

This tool is the first consumer of the credential store's `tool.*` namespace. The binding works as follows:

1. `KagiWebSearchTool` declares `tool_secret_schema` with a single required field `api_key`
2. At `execute()` time, the tool reads the credential `tool.kagi_web_search.api_key` from the `CredentialService`
3. The tool does **not** receive the credential service via constructor injection from the executor. Instead, it imports `service_provider` from `service_setup` and retrieves the credential service at execution time.

**Why service_provider and not constructor injection?** The existing `ToolExecutor.register_tools()` instantiates tools with `tool_cls(defn)` — a single argument. Changing the executor's constructor signature to accept a credential service would affect all tool registrations. The service locator pattern is already used throughout the codebase and keeps the change scoped. If constructor injection is desired later, the executor can be extended to pass services through.

### 4. Inline extraction via `SearchRequest.extract`

The Kagi Search API supports an `extract` parameter that fetches and returns markdown content from the top N search results in a single API call (billed at Extract API rates). This is more efficient than a separate `UrlContentTool` call for each result.

We expose this as an optional `extract_count` argument (1-10). When set, the search response's `snippet` fields contain full extracted markdown instead of short summaries.

### 5. Config flag to prune the summarizer from the argument schema

The `summarize` workflow incurs additional API cost and may not be desired in all deployments. Rather than rejecting calls at execution time, we prune the option from the schema so the LLM never sees it.

A `tool_config_schema` flag `enable_summarizer` (default: `false`) controls this. The tool class has a `build_definition(config=...)` classmethod that wraps `make_definition()` and strips `summarize` from the `workflow` enum when the flag is off:

```python
@classmethod
def build_definition(cls, config: dict[str, Any] | None = None) -> ToolDefinition:
    defn = cls.make_definition()
    if config:
        defn.config = config
    if not defn.config.get("enable_summarizer", False):
        schema = copy.deepcopy(defn.argument_schema)
        schema["properties"]["workflow"]["enum"] = ["search"]
        defn.argument_schema = schema
    return defn
```

`service_setup.py` calls `build_definition(config=existing.config)` during tool seeding (where it already preserves user config for built-in tools). `STANDARD_TOOLS` also uses `build_definition()` with no config argument, which defaults to the empty config (summarizer off). Same method, same behavior, every time.

### 6. Tool naming: `kagi_web_search`

Follows the existing convention (`web_search`, `url_content`, `calculator`). The `kagi_` prefix distinguishes it from the SearXNG-based `web_search` tool. Both can coexist — the agent or user decides which to use.

### 7. Result formatting

Format results identically to `WebSearchTool._format_results()` for consistency. Each result includes:
- Title, URL, snippet, and optionally the source type (`search`, `news`, `video`, etc.)

The response `data` object has multiple result buckets (`search`, `image`, `video`, `podcast`, `news`, etc.). We concatenate results from `search` (primary) and optionally `news` and `interesting_finds` buckets, up to the user's `max_results` limit.

## API Reference

### Kagi Search API — `POST /search`

**Authentication**: Bearer token in `Authorization` header.

**Request body** (`SearchRequest`):
| Field         | Type                   | Description                                       |
|---------------|------------------------|---------------------------------------------------|
| `query`       | `str`                  | Search query (required)                           |
| `workflow`    | `str`                  | `"search"` or `"summarize"` (default: `"search"`) |
| `limit`       | `int`                  | Max results to return, 1-1024                     |
| `timeout`     | `float`                | Seconds to allow for collection                   |
| `page`        | `int`                  | Page number, 1-10                                 |
| `safe_search` | `bool`                 | Omit NSFW content (default: true)                 |
| `lens_id`     | `str`                  | Apply a Kagi Lens                                 |
| `filters`     | `SearchRequestFilters` | Country, language, etc.                           |
| `extract`     | `SearchRequestExtract` | Inline extraction config                          |
| `format`      | `str`                  | `"json"` or `"markdown"` (experimental)           |

**Response** (`Search200Response`):
```json
{
  "meta": {"id": "...", "ms": 123, "api_balance": 45.67},
  "data": {
    "search": [SearchResult, ...],
    "news": [SearchResult, ...],
    "video": [SearchResult, ...],
    "image": [SearchResult, ...],
    ...
  }
}
```

**`SearchResult`**:
| Field     | Type                | Description                                       |
|-----------|---------------------|---------------------------------------------------|
| `title`   | `str`               | Page title                                        |
| `url`     | `str`               | Result URL                                        |
| `snippet` | `str`               | Summary or extracted markdown (when extract used) |
| `time`    | `str`               | Date (optional)                                   |
| `image`   | `SearchResultImage` | Thumbnail (optional)                              |
| `props`   | `dict`              | Arbitrary metadata (optional)                     |

**HTTP errors**: 400 (bad request), 401 (bad token), 403 (IP not authorized), 429 (rate limited), 500 (server error).

## Tool Specification

### Class: `KagiWebSearchTool`

```python
tool_name = "kagi_web_search"
tool_group = "standard"
tool_description = (
    "Search the web using the Kagi Search API. Returns high-quality, "
    "re-ranked results with titles, URLs, and content snippets. "
    "Optionally extracts full page content from top results. "
    "Supports 'search' workflow (ranked results) and 'summarize' "
    "workflow (generated summary). Requires a Kagi API key."
)
tool_argument_schema = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query.",
        },
        "workflow": {
            "type": "string",
            "enum": ["search", "summarize"],
            "description": "Result type: 'search' for ranked results, 'summarize' for a generated summary.",
            "default": "search",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum number of search results to return (1-50).",
            "default": 10,
        },
        "extract_count": {
            "type": "integer",
            "description": (
                "Number of top results to extract full page content from (1-10). "
                "When set, snippets contain full extracted markdown. "
                "Incurs additional Extract API charges."
            ),
        },
        "safe_search": {
            "type": "boolean",
            "description": "Whether to filter potentially NSFW content.",
            "default": True,
        },
    },
    "required": ["query"],
}
tool_config_schema = {
    "type": "object",
    "properties": {
        "enable_summarizer": {
            "type": "boolean",
            "title": "Enable Summarizer",
            "description": (
                "Allow the 'summarize' workflow. When disabled, only the "
                "'search' workflow (ranked results) is available."
            ),
            "default": False,
        },
    },
}
tool_secret_schema = {
    "type": "object",
    "properties": {
        "api_key": {
            "type": "string",
            "description": "Kagi API Bearer token from https://kagi.com/api/keys",
        },
    },
    "required": ["api_key"],
}
```

## Credential Wiring

### Credential name

`tool.kagi_web_search.api_key`

### Binding flow

1. Tool is registered in `STANDARD_TOOLS` via `KagiWebSearchTool.make_definition()`
2. User stores their Kagi API key via the credentials API: `POST /api/credentials` with `name="tool.kagi_web_search.api_key"`, `value={"key": "xxx"}`
3. At execution time, `KagiWebSearchTool.execute()`:
   ```python
   from moira.service_setup import service_provider
   from moira.services.credentials import CredentialService

   cred_service = service_provider("credential_service")
   if cred_service is None:
       return self._fail(start, "Credential service not available")
   cred_value = await cred_service.get_credential("tool.kagi_web_search.api_key")
   if cred_value is None:
       return self._fail(start, "Kagi API key not configured. Store it as credential 'tool.kagi_web_search.api_key'.")
   api_key = cred_value.get("key", "")
   ```

### `credential_refs` (not needed)

The default `tool.<tool_name>.<secret_key>` convention is sufficient. No cross-namespace references needed for this tool.

## Files to Create

| File | Description |
|------|-------------|
| `moira/tools/builtin/kagi_web_search.py` | `KagiWebSearchTool` implementation |
| `tests/test_kagi_web_search.py` | Tests |

## Files to Modify

| File | Change |
|------|--------|
| `pyproject.toml` | Add `kagi-openapi-python` dependency (or verify PyPI name) |
| `moira/tools/standard.py` | Add `KagiWebSearchTool.make_definition()` to `STANDARD_TOOLS` |

## Dependency Decision: Client Library vs Raw httpx

**Recommendation: Use httpx directly.**

Rationale:
1. The Kagi API is one POST endpoint with JSON in/out — the generated client adds complexity without meaningful benefit for a single-endpoint integration.
2. The PyPI package is named `openapi-client`, which is extremely generic and risks namespace collisions.
3. `httpx` is already a project dependency (used by `WebSearchTool` and `InferenceClient`).
4. The generated client uses synchronous I/O internally (`urllib3`-based), which would require wrapping in `asyncio.to_thread` — adding latency and complexity. Using `httpx.AsyncClient` natively is simpler and faster.
5. The request/response shapes are trivially constructible as dicts.

If the Kagi API grows significantly in scope (many endpoints, complex auth flows), we can migrate to the client library later.

## Implementation Details

### Constructor

```python
def __init__(self, definition, timeout: float = 30.0, client: httpx.AsyncClient | None = None):
    super().__init__(definition)
    self._timeout = timeout
    self._test_client = client
```

Same pattern as `WebSearchTool` — test-injectable `httpx.AsyncClient`, configurable timeout.

### execute() method

1. Resolve API key from credential store (fail fast if missing)
2. Build request body from args
3. POST to `https://kagi.com/api/v1/search` with `Authorization: Bearer {api_key}`
4. Parse response, extract results from `data.search` (primary) + `data.news` (supplementary)
5. Format results using same layout as `WebSearchTool._format_results()`
6. Return `ToolResult`

### Error handling

| Condition | Response |
|-----------|----------|
| No API key configured | `_fail("Kagi API key not configured...")` |
| HTTP 401 | `_fail("Kagi API key is invalid...")` |
| HTTP 429 | `_fail("Kagi API rate limited...")` |
| HTTP 5xx | `_fail("Kagi API server error...")` |
| Timeout | `_fail("Kagi API request timed out...")` |
| Connection error | `_fail("Cannot reach Kagi API...")` |

### Result formatting

Same format as `WebSearchTool`:
```
[1] Title of First Result
    URL: https://example.com/page1
    Snippet: A short summary...

[2] Title of Second Result
    URL: https://example.com/page2
    Snippet: Another summary...
```

When `extract_count` is used, the snippet field contains full markdown. The output truncates at 10,000 characters to avoid overwhelming the context window.

## Test Plan

### Unit tests (mocked HTTP)

1. **Successful search** — mock 200 response with `data.search` results, verify formatted output
2. **Missing API key** — credential service returns `None`, verify error message
3. **Invalid API key** — mock 401 response, verify error message
4. **Rate limited** — mock 429 response, verify error message
5. **Empty results** — mock 200 with empty `data`, verify "No results" message
6. **Multiple result types** — mock response with `search` + `news` buckets, verify combined output
7. **Extract inline** — request with `extract_count=3`, verify extract field is sent in request
8. **Max results limiting** — request with `max_results=3` when API returns 10, verify output truncated
9. **Workflow parameter** — request with `workflow="summarize"`, verify field is sent
10. **Summarizer pruned from schema** — `build_definition(config={"enable_summarizer": False})` produces argument_schema where `workflow` enum is `["search"]` only
11. **Summarizer in schema when enabled** — `build_definition(config={"enable_summarizer": True})` produces argument_schema where `workflow` enum includes `"summarize"`
12. **Tool definition** — verify `make_definition()` produces correct `ToolDefinition`
13. **Secret schema** — verify `get_spec()` returns expected secret_schema
14. **Missing query** — empty query string, verify error

### Integration approach

No integration tests against the live Kagi API (would require a real API key and would incur charges). All tests use mocked HTTP responses and a mocked credential service.

## Implementation Order

1. Add dependency to `pyproject.toml` (if using client library — skip if using httpx)
2. Create `moira/tools/builtin/kagi_web_search.py`
3. Add `KagiWebSearchTool.make_definition()` to `STANDARD_TOOLS` in `moira/tools/standard.py`
4. Write tests in `tests/test_kagi_web_search.py`
5. Run full test suite, lint, typecheck
6. Update `agent-docs/index.md` with new plan doc reference

## Open Questions

1. **PyPI package name**: The `kagi-openapi-python` README says `pip install git+https://github.com/...` but also mentions `openapi-client` as the import name. Is there a published PyPI package? **Resolution: Using httpx directly avoids this question entirely.**

2. **Should this tool replace `web_search` (SearXNG) or coexist?** They serve different purposes — SearXNG is free and self-hosted, Kagi is paid but higher quality. Both should coexist. The user or agent chooses.

3. **Extract as a separate tool?** The Kagi Extract API (`POST /extract`) extracts markdown from arbitrary URLs, independent of search. This could be a separate `kagi_extract` tool that complements `url_content`. **Deferred** — the inline `extract_count` parameter covers the main use case. A standalone extract tool can be added later if needed.

4. **Output truncation threshold**: 10,000 characters default for extracted content? This should probably be configurable, but a sensible default is fine for now.

## Future Work

- Standalone `kagi_extract` tool for extracting markdown from arbitrary URLs
- Lens support (expose `lens_id` argument for scoped searches)
- Filter support (country, language, domain filters)
- Streaming/large result handling for deep research queries
- Caching layer for repeated queries within a research session
