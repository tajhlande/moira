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
) -> WebSearchTool:
    """Create a WebSearchTool with the given base URL and optional mock handler."""
    defn = WebSearchTool.make_definition(
        config={"searxng_base_url": base_url},
    )
    client = None
    if handler:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WebSearchTool(defn, client=client)


@pytest.fixture
def tool():
    defn = WebSearchTool.make_definition(
        config={"searxng_base_url": "http://localhost:8888"},
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
