import httpx
import pytest

from moira.tools.builtin.web_search import WebSearchTool

SEARXNG_RESPONSE = {
    "query": "test query",
    "number_of_results": 42,
    "results": [
        {
            "url": "https://example.com/result1",
            "title": "First Result",
            "content": "This is the first search result snippet.",
            "engine": "google",
            "engines": ["google"],
            "score": 1.0,
            "category": "general",
        },
        {
            "url": "https://example.org/result2",
            "title": "Second Result",
            "content": "Another snippet here.",
            "engine": "bing",
            "engines": ["bing", "duckduckgo"],
            "score": 0.8,
            "category": "general",
        },
        {
            "url": "https://example.net/result3",
            "title": "Third Result",
            "content": "Third snippet content.",
            "engine": "wikipedia",
            "engines": ["wikipedia"],
            "score": 0.6,
            "category": "general",
        },
    ],
}


def _searxng_handler(body: dict, status_code: int = 200):
    """Create an httpx.MockTransport handler that returns a SearXNG response."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    return handler


def _make_tool(
    base_url: str = "http://localhost:8888",
    handler=None,
    config: dict | None = None,
) -> WebSearchTool:
    """Create a WebSearchTool with the given base URL and optional mock handler.

    Caching is disabled by default so tests can verify HTTP call behavior
    without cache side effects. Pass cache_enabled=True in config to test
    caching.
    """
    cfg = {"searxng_base_url": base_url, "cache_enabled": False}
    if config:
        cfg.update(config)
    defn = WebSearchTool.make_definition(config=cfg)
    client = None
    if handler:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WebSearchTool(defn, client=client)


@pytest.fixture
def tool():
    defn = WebSearchTool.make_definition(
        config={"searxng_base_url": "http://localhost:8888", "cache_enabled": False},
    )
    return WebSearchTool(defn)


class TestWebSearchMakeDefinition:
    def test_make_definition_fields(self):
        defn = WebSearchTool.make_definition()
        assert defn.name == "web_search"
        assert "SearXNG" in defn.description
        assert defn.implementation == "moira.tools.builtin.web_search.WebSearchTool"
        assert defn.built_in is True
        assert defn.is_default is True
        assert "query" in defn.argument_schema["required"]

    def test_make_definition_with_config(self):
        defn = WebSearchTool.make_definition(
            config={"searxng_base_url": "http://searx:8080"},
        )
        assert defn.config["searxng_base_url"] == "http://searx:8080"


class TestWebSearchUnit:
    @pytest.mark.asyncio
    async def test_missing_query(self, tool):
        r = await tool.execute({})
        assert not r.success
        assert "Missing required parameter: query" in r.error

    @pytest.mark.asyncio
    async def test_empty_query(self, tool):
        r = await tool.execute({"query": "  "})
        assert not r.success
        assert "Missing required parameter: query" in r.error

    @pytest.mark.asyncio
    async def test_missing_base_url(self):
        defn = WebSearchTool.make_definition(config={})
        tool = WebSearchTool(defn)
        r = await tool.execute({"query": "test"})
        assert not r.success
        assert "SearXNG base URL not configured" in r.error

    @pytest.mark.asyncio
    async def test_basic_search(self):
        tool = _make_tool(handler=_searxng_handler(SEARXNG_RESPONSE))
        r = await tool.execute({"query": "test query"})
        assert r.success
        assert "[1] First Result" in r.output
        assert "https://example.com/result1" in r.output
        assert "first search result snippet" in r.output
        assert "[2] Second Result" in r.output
        assert "[3] Third Result" in r.output

    @pytest.mark.asyncio
    async def test_metadata_has_structured_results(self):
        """ToolResult.metadata should carry per-result title/url/snippet
        so downstream nodes can create citations without re-parsing text."""
        tool = _make_tool(handler=_searxng_handler(SEARXNG_RESPONSE))
        r = await tool.execute({"query": "test query"})
        assert r.success
        assert "results" in r.metadata
        structured = r.metadata["results"]
        assert len(structured) == 3
        assert structured[0]["title"] == "First Result"
        assert structured[0]["url"] == "https://example.com/result1"
        assert structured[0]["snippet"] == "This is the first search result snippet."
        assert structured[2]["url"] == "https://example.net/result3"

    @pytest.mark.asyncio
    async def test_metadata_respects_max_results(self):
        tool = _make_tool(handler=_searxng_handler(SEARXNG_RESPONSE))
        r = await tool.execute({"query": "test", "max_results": 2})
        assert r.success
        assert len(r.metadata["results"]) == 2

    @pytest.mark.asyncio
    async def test_metadata_empty_results_on_no_results(self):
        """When search returns no results, metadata carries an empty results
        list (not None) so the pipeline can distinguish '0 hits' from
        'tool has no metadata'."""
        tool = _make_tool(
            handler=_searxng_handler({"query": "obscure", "results": []}),
        )
        r = await tool.execute({"query": "obscure"})
        assert r.success
        assert r.metadata is not None
        assert r.metadata["results"] == []
        assert r.metadata["unresponsive_engines"] == []

    @pytest.mark.asyncio
    async def test_metadata_carries_unresponsive_engines(self):
        """SearXNG reports engines that are suspended or rate-limited.
        These should be carried in metadata for diagnostics."""
        tool = _make_tool(
            handler=_searxng_handler(
                {
                    "query": "test",
                    "results": [],
                    "unresponsive_engines": [
                        ["brave", "too many requests"],
                        ["startpage", "Suspended: CAPTCHA"],
                    ],
                }
            ),
        )
        r = await tool.execute({"query": "test"})
        assert r.success
        assert r.metadata["results"] == []
        assert len(r.metadata["unresponsive_engines"]) == 2

    @pytest.mark.asyncio
    async def test_unresponsive_engines_on_successful_search(self):
        """Even when results are returned, unresponsive engines should
        be carried in metadata for diagnostics."""
        tool = _make_tool(
            handler=_searxng_handler(
                {
                    **SEARXNG_RESPONSE,
                    "unresponsive_engines": [["startpage", "Suspended: CAPTCHA"]],
                }
            ),
        )
        r = await tool.execute({"query": "test"})
        assert r.success
        assert len(r.metadata["results"]) == 3
        assert len(r.metadata["unresponsive_engines"]) == 1

    @pytest.mark.asyncio
    async def test_max_results_limits_output(self):
        tool = _make_tool(handler=_searxng_handler(SEARXNG_RESPONSE))
        r = await tool.execute({"query": "test", "max_results": 2})
        assert r.success
        assert "[1] First Result" in r.output
        assert "[2] Second Result" in r.output
        assert "[3]" not in r.output

    @pytest.mark.asyncio
    async def test_empty_results(self):
        tool = _make_tool(
            handler=_searxng_handler({"query": "obscure", "results": []}),
        )
        r = await tool.execute({"query": "obscure"})
        assert r.success
        assert r.output == "No search results found."

    @pytest.mark.asyncio
    async def test_http_error(self):
        tool = _make_tool(handler=_searxng_handler({}, status_code=500))
        r = await tool.execute({"query": "test"})
        assert not r.success
        assert "HTTP 500" in r.error

    @pytest.mark.asyncio
    async def test_timeout(self):
        def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Connection timed out")

        tool = _make_tool(handler=timeout_handler)
        r = await tool.execute({"query": "test"})
        assert not r.success
        assert "timed out" in r.error

    @pytest.mark.asyncio
    async def test_connection_error(self):
        def conn_error_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        tool = _make_tool(handler=conn_error_handler)
        r = await tool.execute({"query": "test"})
        assert not r.success
        assert "Connection refused" in r.error

    @pytest.mark.asyncio
    async def test_categories_passed_through(self):
        received_params = {}

        def capture_handler(request: httpx.Request) -> httpx.Response:
            import urllib.parse

            parsed = urllib.parse.urlparse(str(request.url))
            params = dict(urllib.parse.parse_qsl(parsed.query))
            received_params.update(params)
            return httpx.Response(200, json=SEARXNG_RESPONSE)

        tool = _make_tool(handler=capture_handler)
        r = await tool.execute({"query": "test", "categories": "general,news"})
        assert r.success
        assert received_params.get("categories") == "general,news"

    @pytest.mark.asyncio
    async def test_language_passed_through(self):
        received_params = {}

        def capture_handler(request: httpx.Request) -> httpx.Response:
            import urllib.parse

            parsed = urllib.parse.urlparse(str(request.url))
            params = dict(urllib.parse.parse_qsl(parsed.query))
            received_params.update(params)
            return httpx.Response(200, json=SEARXNG_RESPONSE)

        tool = _make_tool(handler=capture_handler)
        r = await tool.execute({"query": "test", "language": "de"})
        assert r.success
        assert received_params.get("language") == "de"

    @pytest.mark.asyncio
    async def test_time_range_passed_through(self):
        received_params = {}

        def capture_handler(request: httpx.Request) -> httpx.Response:
            import urllib.parse

            parsed = urllib.parse.urlparse(str(request.url))
            params = dict(urllib.parse.parse_qsl(parsed.query))
            received_params.update(params)
            return httpx.Response(200, json=SEARXNG_RESPONSE)

        tool = _make_tool(handler=capture_handler)
        r = await tool.execute({"query": "test", "time_range": "month"})
        assert r.success
        assert received_params.get("time_range") == "month"

    @pytest.mark.asyncio
    async def test_format_always_json(self):
        received_params = {}

        def capture_handler(request: httpx.Request) -> httpx.Response:
            import urllib.parse

            parsed = urllib.parse.urlparse(str(request.url))
            params = dict(urllib.parse.parse_qsl(parsed.query))
            received_params.update(params)
            return httpx.Response(200, json=SEARXNG_RESPONSE)

        tool = _make_tool(handler=capture_handler)
        await tool.execute({"query": "test"})
        assert received_params.get("format") == "json"

    @pytest.mark.asyncio
    async def test_result_without_optional_fields(self):
        minimal = {
            "results": [
                {"url": "https://example.com", "title": "Minimal"},
            ],
        }
        tool = _make_tool(handler=_searxng_handler(minimal))
        r = await tool.execute({"query": "test"})
        assert r.success
        assert "[1] Minimal" in r.output
        assert "https://example.com" in r.output

    @pytest.mark.asyncio
    async def test_base_url_trailing_slash_stripped(self):
        def capture_handler(request: httpx.Request) -> httpx.Response:
            assert "/search" in str(request.url)
            assert "//search" not in str(request.url)
            return httpx.Response(200, json=SEARXNG_RESPONSE)

        tool = _make_tool(
            base_url="http://localhost:8888/",
            handler=capture_handler,
        )
        r = await tool.execute({"query": "test"})
        assert r.success

    @pytest.mark.asyncio
    async def test_duration_ms_recorded(self):
        tool = _make_tool(handler=_searxng_handler(SEARXNG_RESPONSE))
        r = await tool.execute({"query": "test"})
        assert r.success
        assert r.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_missing_artifact_stripped_from_metadata(self):
        """SearXNG/Google 'Missing:' text should be stripped from snippets."""
        response = {
            "results": [
                {
                    "url": "https://example.com/recipe",
                    "title": "Recipe",
                    "content": (
                        "Need 2 cups cherries, 1 mesh strainer"
                        "Missing: variety | Show results with:variety"
                    ),
                    "engines": ["google"],
                },
            ],
        }
        tool = _make_tool(handler=_searxng_handler(response))
        r = await tool.execute({"query": "cherry recipe"})
        assert r.success
        structured = r.metadata["results"]
        assert "Missing:" not in structured[0]["snippet"]
        assert "Show results" not in structured[0]["snippet"]
        assert structured[0]["snippet"] == "Need 2 cups cherries, 1 mesh strainer"

    @pytest.mark.asyncio
    async def test_missing_artifact_stripped_from_output(self):
        """SearXNG/Google 'Missing:' text should also be stripped from
        the formatted text output given to the model."""
        response = {
            "results": [
                {
                    "url": "https://example.com/recipe",
                    "title": "Recipe",
                    "content": "Some content here Missing: sugar | Show results with:sugar",
                    "engines": ["google"],
                },
            ],
        }
        tool = _make_tool(handler=_searxng_handler(response))
        r = await tool.execute({"query": "recipe"})
        assert r.success
        assert "Missing:" not in r.output
        assert "Show results" not in r.output
        assert "Some content here" in r.output


class TestSearchCache:
    """Tests for the SQLite-backed search result cache."""

    def _make_cached_tool(
        self,
        tmp_path,
        handler=None,
        ttl_seconds: int = 604800,
    ) -> WebSearchTool:
        """Create a WebSearchTool with caching enabled and a temp DB.

        Cache settings are injected via the tool config dict, which
        _read_cache_settings falls back to when the settings service
        is unavailable (as in unit tests).
        """
        cache_db = str(tmp_path / "test_cache.db")
        cfg = {
            "searxng_base_url": "http://localhost:8888",
            "cache_enabled": True,
            "cache_ttl_seconds": ttl_seconds,
            "cache_db_path": cache_db,
        }
        return _make_tool(handler=handler, config=cfg)

    @pytest.mark.asyncio
    async def test_cache_miss_then_hit(self, tmp_path):
        """First call hits SearXNG and stores in cache; second call is a
        cache hit with no HTTP request."""
        call_count = 0

        def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=SEARXNG_RESPONSE)

        tool = self._make_cached_tool(tmp_path, handler=counting_handler)

        # First call — cache miss, HTTP happens
        r1 = await tool.execute({"query": "test query"})
        assert r1.success
        assert r1.metadata["cache_hit"] is False
        assert call_count == 1

        # Second call — cache hit, no HTTP
        r2 = await tool.execute({"query": "test query"})
        assert r2.success
        assert r2.metadata["cache_hit"] is True
        assert call_count == 1  # no additional HTTP call

        # Same results
        assert r1.output == r2.output

    @pytest.mark.asyncio
    async def test_different_max_results_shares_cache(self, tmp_path):
        """max_results is excluded from the cache key, so different values
        share the same cached response."""
        call_count = 0

        def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=SEARXNG_RESPONSE)

        tool = self._make_cached_tool(tmp_path, handler=counting_handler)

        r1 = await tool.execute({"query": "test", "max_results": 2})
        assert r1.success
        assert len(r1.metadata["results"]) == 2
        assert call_count == 1

        # Same query, different max_results — cache hit, trimmed differently
        r2 = await tool.execute({"query": "test", "max_results": 1})
        assert r2.success
        assert r2.metadata["cache_hit"] is True
        assert len(r2.metadata["results"]) == 1
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_different_query_is_cache_miss(self, tmp_path):
        call_count = 0

        def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=SEARXNG_RESPONSE)

        tool = self._make_cached_tool(tmp_path, handler=counting_handler)

        await tool.execute({"query": "alpha"})
        await tool.execute({"query": "beta"})
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_cache_expiry(self, tmp_path):
        """After TTL expires, the cache entry is treated as a miss."""
        import time as time_mod

        call_count = 0

        def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=SEARXNG_RESPONSE)

        # TTL = 0 seconds → immediately expired
        tool = self._make_cached_tool(tmp_path, handler=counting_handler, ttl_seconds=0)

        r1 = await tool.execute({"query": "test"})
        assert r1.metadata["cache_hit"] is False
        assert call_count == 1

        # Sleep 0.01s to ensure expiry
        time_mod.sleep(0.01)

        r2 = await tool.execute({"query": "test"})
        assert r2.metadata["cache_hit"] is False
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_cache_disabled_no_storage(self, tmp_path):
        """When cache_enabled is false, no caching occurs."""
        call_count = 0

        def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=SEARXNG_RESPONSE)

        tool = _make_tool(handler=counting_handler)  # cache_enabled=False by default

        await tool.execute({"query": "test"})
        await tool.execute({"query": "test"})
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_cache_hit_on_empty_results(self, tmp_path):
        """Empty results are cached too — saves wasted HTTP for queries
        that genuinely have no results."""
        call_count = 0

        def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json={"query": "obscure", "results": []})

        tool = self._make_cached_tool(tmp_path, handler=counting_handler)

        r1 = await tool.execute({"query": "obscure"})
        assert r1.success
        assert r1.metadata["cache_hit"] is False
        assert call_count == 1

        r2 = await tool.execute({"query": "obscure"})
        assert r2.success
        assert r2.metadata["cache_hit"] is True
        assert r2.output == "No search results found."
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_http_error_not_cached(self, tmp_path):
        """Failed HTTP responses must not be cached."""
        call_count = 0

        def error_then_success(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(500, json={})
            return httpx.Response(200, json=SEARXNG_RESPONSE)

        tool = self._make_cached_tool(tmp_path, handler=error_then_success)

        r1 = await tool.execute({"query": "test"})
        assert not r1.success

        r2 = await tool.execute({"query": "test"})
        assert r2.success
        assert r2.metadata["cache_hit"] is False
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_cache_normalizes_query_case(self, tmp_path):
        """Query case differences should not cause cache misses."""
        call_count = 0

        def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=SEARXNG_RESPONSE)

        tool = self._make_cached_tool(tmp_path, handler=counting_handler)

        await tool.execute({"query": "Test Query"})
        r2 = await tool.execute({"query": "test query"})
        assert r2.metadata["cache_hit"] is True
        assert call_count == 1

    def test_cleanup_removes_expired_entries(self, tmp_path):
        """cleanup() deletes expired rows and vacuums the DB."""
        import time as time_mod

        from moira.tools.builtin.web_search import SearchCache

        cache_db = str(tmp_path / "test_cache.db")

        # Insert 3 entries with TTL=0 so they expire immediately
        cache = SearchCache(cache_db, ttl_seconds=0)
        cache.put("alpha", None, None, None, {"results": []})
        cache.put("beta", None, None, None, {"results": []})
        cache.put("gamma", None, None, None, {"results": []})
        time_mod.sleep(0.01)

        deleted = cache.cleanup()
        assert deleted == 3

        # DB should still exist and be usable
        fresh = SearchCache(cache_db, ttl_seconds=60)
        assert fresh.get("alpha", None, None, None) is None

    def test_cleanup_no_db_is_noop(self, tmp_path):
        """cleanup() is a no-op when the DB file does not exist."""
        from moira.tools.builtin.web_search import SearchCache

        cache = SearchCache(str(tmp_path / "nonexistent.db"))
        assert cache.cleanup() == 0

    @pytest.mark.asyncio
    async def test_degraded_results_not_cached(self, tmp_path):
        """When SearXNG reports unresponsive engines, the response should
        not be cached — degraded results shouldn't lock out fresh queries
        for the full TTL."""
        call_count = 0

        def degraded_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                200,
                json={
                    **SEARXNG_RESPONSE,
                    "unresponsive_engines": [["mojeek", "HTTP error 403"]],
                },
            )

        tool = self._make_cached_tool(tmp_path, handler=degraded_handler)

        # First call — HTTP, but not cached due to unresponsive engines
        r1 = await tool.execute({"query": "test"})
        assert r1.success
        assert r1.metadata["cache_hit"] is False
        assert call_count == 1

        # Second call — should hit HTTP again, not cache
        r2 = await tool.execute({"query": "test"})
        assert r2.success
        assert r2.metadata["cache_hit"] is False
        assert call_count == 2
