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

# A richer page with LaTeX/math content
LATEX_HTML = """<!DOCTYPE html>
<html>
<head><title>Math Article</title></head>
<body>
  <article>
    <h1>On the Euler Identity</h1>
    <p>The famous Euler identity states that $e^{i\\pi} + 1 = 0$.</p>
    <p>A block equation follows:</p>
    <p>\\[\\int_0^\\infty e^{-x^2}\\,dx = \\frac{\\sqrt{\\pi}}{2}\\]</p>
  </article>
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
        assert "Markdown" in defn.description
        assert defn.implementation == "moira.tools.builtin.url_content.UrlContentTool"
        assert defn.built_in is True
        assert "url" in defn.argument_schema["required"]

    def test_schema_has_no_summarize(self):
        """The summarize parameter was removed; only url, text_only, xpath remain."""
        defn = UrlContentTool.make_definition()
        props = defn.argument_schema["properties"]
        assert "summarize" not in props
        assert set(props.keys()) == {"url", "text_only", "xpath"}


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
    async def test_fetch_markdown_default(self, tool):
        """Default output (no text_only) should be Markdown with headings."""
        transport = httpx.MockTransport(_make_handler(SAMPLE_HTML))
        async with httpx.AsyncClient(transport=transport) as client:

            async def mock_fetch(url):
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text

            tool._fetch = mock_fetch
            r = await tool.execute({"url": "https://example.com"})
            assert r.success
            # Markdown heading, not an HTML <h1> tag
            assert "Research Article" in r.output
            assert "introduction paragraph" in r.output
            assert "main content" in r.output
            # Script, style, noscript should be stripped
            assert "var x" not in r.output
            assert "color: red" not in r.output
            assert "Enable JavaScript" not in r.output

    @pytest.mark.asyncio
    async def test_fetch_text_only(self, tool):
        """text_only=True returns plain text without Markdown markup."""
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
                    "text_only": True,
                }
            )
            assert r.success
            assert "Research Article" in r.output
            # No Markdown heading marker
            assert "# Research Article" not in r.output

    @pytest.mark.asyncio
    async def test_fetch_latex_preserved(self, tool):
        """LaTeX delimiters ($...$, \\[...\\]) should survive extraction."""
        transport = httpx.MockTransport(_make_handler(LATEX_HTML))
        async with httpx.AsyncClient(transport=transport) as client:

            async def mock_fetch(url):
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text

            tool._fetch = mock_fetch
            r = await tool.execute({"url": "https://example.com"})
            assert r.success
            assert "e^{i" in r.output
            assert "\\pi" in r.output
            assert "\\int_0" in r.output
            assert "\\frac" in r.output


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


class TestUrlContentMetadata:
    """Tests for citation metadata (url, title, snippet, content) in
    the ToolResult returned by url_content."""

    @pytest.mark.asyncio
    async def test_metadata_has_url_and_title(self, tool):
        transport = httpx.MockTransport(_make_handler(SAMPLE_HTML))
        async with httpx.AsyncClient(transport=transport) as client:

            async def mock_fetch(url):
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text

            tool._fetch = mock_fetch
            r = await tool.execute({"url": "https://example.com/page"})
            assert r.success
            assert r.metadata is not None
            results = r.metadata["results"]
            assert len(results) == 1
            assert results[0]["url"] == "https://example.com/page"
            assert results[0]["title"] == "Test Page"

    @pytest.mark.asyncio
    async def test_metadata_snippet_and_content_present(self, tool):
        transport = httpx.MockTransport(_make_handler(SAMPLE_HTML))
        async with httpx.AsyncClient(transport=transport) as client:

            async def mock_fetch(url):
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text

            tool._fetch = mock_fetch
            r = await tool.execute({"url": "https://example.com"})
            assert r.success
            entry = r.metadata["results"][0]
            assert "snippet" in entry
            assert "content" in entry
            assert len(entry["content"]) >= len(entry["snippet"])

    @pytest.mark.asyncio
    async def test_metadata_title_empty_when_missing(self, tool):
        html_without_title = "<html><body><p>No title here</p></body></html>"
        transport = httpx.MockTransport(_make_handler(html_without_title))
        async with httpx.AsyncClient(transport=transport) as client:

            async def mock_fetch(url):
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text

            tool._fetch = mock_fetch
            r = await tool.execute({"url": "https://example.com"})
            assert r.success
            assert r.metadata["results"][0]["title"] == ""

    @pytest.mark.asyncio
    async def test_metadata_url_reflects_requested_url(self, tool):
        transport = httpx.MockTransport(_make_handler(SAMPLE_HTML))
        async with httpx.AsyncClient(transport=transport) as client:

            async def mock_fetch(url):
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text

            tool._fetch = mock_fetch
            r = await tool.execute({"url": "https://wiki.example.com/Article"})
            assert r.success
            assert r.metadata["results"][0]["url"] == "https://wiki.example.com/Article"

    def test_extract_title_from_html(self, tool):
        assert tool._extract_title(SAMPLE_HTML) == "Test Page"

    def test_extract_title_missing_returns_empty(self, tool):
        html = "<html><body><p>no title</p></body></html>"
        assert tool._extract_title(html) == ""


class TestUrlContentExtractUnit:
    """Unit tests for the _extract method without HTTP."""

    def test_markdown_includes_heading(self, tool):
        result = tool._extract(SAMPLE_HTML, text_only=False)
        assert "Research Article" in result
        assert "var x" not in result
        assert "color: red" not in result

    def test_text_only_returns_plain_text(self, tool):
        result = tool._extract(SAMPLE_HTML, text_only=True)
        assert "Research Article" in result
        # Plain text should not contain Markdown heading markers
        assert "# " not in result

    def test_latex_preserved_in_markdown(self, tool):
        """The key feature: LaTeX delimiters survive trafilatura extraction."""
        result = tool._extract(LATEX_HTML, text_only=False)
        assert "$e^{i" in result
        assert "\\pi" in result
        assert "\\int" in result
        assert "\\frac" in result

    def test_xpath_selects_subset(self, tool):
        result = tool._extract(SAMPLE_HTML, text_only=True, xpath="//p[@class='body']")
        assert "main content" in result
        assert "introduction" not in result

    def test_xpath_returns_empty_for_no_match(self, tool):
        result = tool._extract(SAMPLE_HTML, xpath="//div[@id='nonexistent']")
        assert result == ""

    def test_extract_returns_empty_on_trafilatura_none(self, tool):
        """If trafilatura cannot extract anything, return empty string, not None."""
        result = tool._extract("", text_only=False)
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
    async def test_captive_apple_text_only(self, live_tool):
        """text_only=True should still return the text content."""
        r = await live_tool.execute(
            {
                "url": "https://captive.apple.com",
                "text_only": True,
            }
        )
        assert r.success
        assert "Success" in r.output

    @pytest.mark.asyncio
    async def test_wttr_in_weather(self, live_tool):
        """wttr.in with format=3 returns a one-line weather report.
        The response contains a temperature reading."""
        r = await live_tool.execute({"url": "https://wttr.in/20500?format=3"})
        assert r.success
        assert "20500" in r.output

    @pytest.mark.asyncio
    async def test_hacker_news_markdown(self, live_tool):
        """Hacker News is a well-structured page with link submissions.
        Markdown extraction should produce readable content."""
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
