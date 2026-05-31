import httpx
import pytest

from moira.tools.builtin.url_content import UrlContentTool

SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head><title>Test Page</title>
<script>var x = 1;</script>
<style>body { color: red; }</style>
</head>
<body>
  <main>
    <h1>Research Article</h1>
    <p class="intro">This is the introduction paragraph.</p>
    <p class="body">The main content goes here.</p>
    <div id="sidebar">Navigation links</div>
  </main>
  <noscript>Enable JavaScript</noscript>
</body>
</html>"""


def _make_handler(html: str, status_code: int = 200):
    """Create an httpx.MockTransport handler that returns the given HTML."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=html)

    return handler


@pytest.fixture
def tool():
    defn = UrlContentTool.make_definition()
    return UrlContentTool(defn)


class TestUrlContentMakeDefinition:
    def test_make_definition_fields(self):
        defn = UrlContentTool.make_definition()
        assert defn.name == "url_content"
        assert "Retrieve the content" in defn.description
        assert defn.implementation == "moira.tools.builtin.url_content.UrlContentTool"
        assert defn.built_in is True
        assert "url" in defn.argument_schema["required"]


class TestUrlContentBasic:
    @pytest.mark.asyncio
    async def test_missing_url(self, tool):
        r = await tool.execute({})
        assert not r.success
        assert "Missing required parameter" in r.error

    @pytest.mark.asyncio
    async def test_empty_url(self, tool):
        r = await tool.execute({"url": "  "})
        assert not r.success
        assert "Missing required parameter" in r.error

    @pytest.mark.asyncio
    async def test_fetch_text_only(self, tool):
        transport = httpx.MockTransport(_make_handler(SAMPLE_HTML))
        async with httpx.AsyncClient(transport=transport) as client:
            # Patch the tool's _fetch to use our mock client

            async def mock_fetch(url):
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text

            tool._fetch = mock_fetch
            r = await tool.execute({"url": "https://example.com"})
            assert r.success
            assert "Research Article" in r.output
            assert "introduction paragraph" in r.output
            assert "main content" in r.output
            # Script, style, noscript should be stripped
            assert "var x" not in r.output
            assert "color: red" not in r.output
            assert "Enable JavaScript" not in r.output

    @pytest.mark.asyncio
    async def test_fetch_full_html(self, tool):
        transport = httpx.MockTransport(_make_handler(SAMPLE_HTML))
        async with httpx.AsyncClient(transport=transport) as client:

            async def mock_fetch(url):
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text

            tool._fetch = mock_fetch
            r = await tool.execute(
                {
                    "url": "https://example.com",
                    "text_only": False,
                }
            )
            assert r.success
            assert "<h1>Research Article</h1>" in r.output


class TestUrlContentXpath:
    @pytest.mark.asyncio
    async def test_xpath_extraction(self, tool):
        transport = httpx.MockTransport(_make_handler(SAMPLE_HTML))
        async with httpx.AsyncClient(transport=transport) as client:

            async def mock_fetch(url):
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text

            tool._fetch = mock_fetch
            r = await tool.execute(
                {
                    "url": "https://example.com",
                    "xpath": "//p[@class='intro']",
                }
            )
            assert r.success
            assert "introduction paragraph" in r.output

    @pytest.mark.asyncio
    async def test_xpath_no_match(self, tool):
        transport = httpx.MockTransport(_make_handler(SAMPLE_HTML))
        async with httpx.AsyncClient(transport=transport) as client:

            async def mock_fetch(url):
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text

            tool._fetch = mock_fetch
            r = await tool.execute(
                {
                    "url": "https://example.com",
                    "xpath": "//p[@class='nonexistent']",
                }
            )
            assert r.success
            assert r.output == ""

    @pytest.mark.asyncio
    async def test_xpath_with_html_output(self, tool):
        transport = httpx.MockTransport(_make_handler(SAMPLE_HTML))
        async with httpx.AsyncClient(transport=transport) as client:

            async def mock_fetch(url):
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text

            tool._fetch = mock_fetch
            r = await tool.execute(
                {
                    "url": "https://example.com",
                    "xpath": "//h1",
                    "text_only": False,
                }
            )
            assert r.success
            assert "<h1>Research Article</h1>" in r.output


class TestUrlContentErrors:
    @pytest.mark.asyncio
    async def test_http_error(self, tool):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:

            async def mock_fetch(url):
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text

            tool._fetch = mock_fetch
            r = await tool.execute({"url": "https://example.com/missing"})
            assert not r.success
            assert "404" in r.error or "Not Found" in r.error


class TestUrlContentExtractUnit:
    """Unit tests for the _extract method without HTTP."""

    def test_text_only_strips_scripts_and_styles(self, tool):
        result = tool._extract(SAMPLE_HTML, text_only=True)
        assert "Research Article" in result
        assert "var x" not in result
        assert "color: red" not in result

    def test_text_only_false_returns_html(self, tool):
        result = tool._extract(SAMPLE_HTML, text_only=False)
        assert "<h1>" in result

    def test_xpath_selects_subset(self, tool):
        result = tool._extract(SAMPLE_HTML, text_only=True, xpath="//p[@class='body']")
        assert "main content" in result
        assert "introduction" not in result

    def test_xpath_returns_empty_for_no_match(self, tool):
        result = tool._extract(SAMPLE_HTML, xpath="//div[@id='nonexistent']")
        assert result == ""


# ---------------------------------------------------------------------------
# Integration tests: fetch real URLs from the internet.
# These make actual HTTP requests and depend on external sites being up.
# ---------------------------------------------------------------------------


@pytest.fixture
def live_tool():
    """Tool instance with the real _fetch method (no mocking)."""
    defn = UrlContentTool.make_definition()
    return UrlContentTool(defn)


class TestUrlContentIntegration:
    """Integration tests against real websites. These make network calls
    and will fail if the target sites are down or have changed."""

    @pytest.mark.asyncio
    async def test_captive_apple(self, live_tool):
        """captive.apple.com returns a tiny HTML page with 'Success'."""
        r = await live_tool.execute({"url": "https://captive.apple.com"})
        assert r.success
        assert "Success" in r.output

    @pytest.mark.asyncio
    async def test_captive_apple_full_html(self, live_tool):
        """When text_only=False, the HTML tags should be present."""
        r = await live_tool.execute(
            {
                "url": "https://captive.apple.com",
                "text_only": False,
            }
        )
        assert r.success
        assert "<TITLE>Success</TITLE>" in r.output

    @pytest.mark.asyncio
    async def test_wttr_in_weather(self, live_tool):
        """wttr.in with format=3 returns a one-line weather report.
        The response contains a temperature reading."""
        r = await live_tool.execute({"url": "https://wttr.in/?format=3"})
        assert r.success
        assert r.output.strip() != ""

    @pytest.mark.asyncio
    async def test_hacker_news_text_only(self, live_tool):
        """Hacker News is a well-structured page with link submissions.
        Text extraction should produce readable content."""
        r = await live_tool.execute({"url": "https://news.ycombinator.com"})
        assert r.success
        assert "Hacker News" in r.output

    @pytest.mark.asyncio
    async def test_hacker_news_xpath(self, live_tool):
        """Use XPath to extract only the story titles from Hacker News.
        The title elements have class 'titleline'."""
        r = await live_tool.execute(
            {
                "url": "https://news.ycombinator.com",
                "xpath": "//span[@class='titleline']",
            }
        )
        assert r.success
        assert r.output.strip() != ""

    @pytest.mark.asyncio
    async def test_lite_cnn(self, live_tool):
        """CNN lite is a text-oriented news page. Should return
        readable news content."""
        r = await live_tool.execute({"url": "https://lite.cnn.com"})
        assert r.success
        # CNN lite always has news content with substantial text
        assert len(r.output) > 100
