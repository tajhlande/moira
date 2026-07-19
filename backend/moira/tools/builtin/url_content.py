"""URL content retrieval tool: fetches a web page and returns its content.

Uses httpx for async HTTP fetching and trafilatura for robust main-content
extraction. By default, returns Markdown that preserves headings, links,
tables, formatting, and embedded LaTeX/math notation. Supports XPath
subsetting via lxml and optional plain-text output."""

import logging
import time
from typing import Any

import httpx
import trafilatura
from lxml import etree

from moira.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# Maximum response size to accept (bytes). Prevents loading enormous pages
# into memory. 5 MB is generous enough for most research-quality pages.
_MAX_RESPONSE_SIZE = 5 * 1024 * 1024

# Maximum text length to return in ToolResult. Truncation preserves the
# beginning of the content, which is typically the most useful part.
_MAX_OUTPUT_LENGTH = 100_000

# Snippet and content lengths carried in metadata["results"] for citation
# creation downstream. These mirror _SNIPPET_MAX_LENGTH (500) and
# _CITATION_CONTENT_LIMIT (5000) in research.py — the tool provides the
# data, the pipeline enforces its own limits.
_METADATA_SNIPPET_LENGTH = 500
_METADATA_CONTENT_LENGTH = 5_000


class UrlContentTool(BaseTool):
    """Fetches a URL and returns its content via trafilatura extraction.

    By default the output is Markdown with links, tables, formatting, and
    LaTeX/math preserved. Set ``text_only=True`` for plain text without
    Markdown markup. An optional ``xpath`` expression can narrow the
    extraction to a subtree of the page.
    """

    tool_name = "url_content"
    tool_description = (
        "Retrieve the content of a web page given its URL. "
        "Returns Markdown by default, preserving structure, links, tables, "
        "and embedded LaTeX/math notation. Set text_only=true for plain text "
        "without Markdown formatting. Supports XPath selectors to extract "
        "a subset of content."
    )
    tool_group = "standard"
    tool_argument_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to retrieve content from",
            },
            "text_only": {
                "type": "boolean",
                "description": "Return plain text without Markdown markup (default: false)",
            },
            "xpath": {
                "type": "string",
                "description": "XPath expression to extract a subset of the page",
            },
        },
        "required": ["url"],
    }

    def __init__(self, definition, timeout: float = 30.0):
        super().__init__(definition)
        self._timeout = timeout

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        start = time.monotonic()
        url = args.get("url", "").strip()
        if not url:
            return self._fail(start, "Missing required parameter: url")

        text_only = args.get("text_only", False)
        xpath_expr = args.get("xpath", "")

        try:
            html = await self._fetch(url)
        except Exception as e:
            return self._fail(start, f"Failed to fetch {url}: {e}")

        try:
            content = self._extract(html, url=url, text_only=text_only, xpath=xpath_expr)
        except Exception as e:
            return self._fail(start, f"Failed to parse content from {url}: {e}")

        content = content[:_MAX_OUTPUT_LENGTH]
        title = self._extract_title(html)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Include the URL and title in the output text so the model can
        # reference them when emitting sources entries. Without this, the
        # model — which sees the output text in its tool feedback —
        # frequently omits the URL from its sources array, creating phantom
        # URL-less citations that appear in the report's "Additional
        # Sources" without a link. web_search already includes URLs in its
        # output; this matches that pattern. The header is only prepended
        # when content was successfully extracted — empty output stays empty
        # so downstream "no results" handling is unaffected.
        if content:
            header_parts = [f"URL: {url}"]
            if title:
                header_parts.append(f"Title: {title}")
            output = "\n".join(header_parts) + "\n\n" + content
        else:
            output = content

        return ToolResult(
            tool_name=self.name,
            output=output,
            success=True,
            duration_ms=elapsed_ms,
            metadata={
                "results": [
                    {
                        "url": url,
                        "title": title,
                        "snippet": content[:_METADATA_SNIPPET_LENGTH],
                        "content": content[:_METADATA_CONTENT_LENGTH],
                    }
                ]
            },
        )

    async def _fetch(self, url: str) -> str:
        """Fetch the page HTML with a reasonable user-agent and size limit."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            max_redirects=10,
        ) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()

            if len(resp.content) > _MAX_RESPONSE_SIZE:
                raise ValueError(
                    f"Response too large ({len(resp.content)} bytes, max {_MAX_RESPONSE_SIZE})"
                )
            return resp.text

    def _extract(
        self,
        html: str,
        url: str = "",
        text_only: bool = False,
        xpath: str = "",
    ) -> str:
        """Extract main content from HTML using trafilatura.

        If ``xpath`` is provided, the HTML is first narrowed to the matching
        subtree via lxml so trafilatura processes only that section.

        ``text_only`` selects between Markdown (default) and plain-text
        output. Markdown preserves headings, links, tables, formatting, and
        LaTeX/math delimiters (``$...$``, ``\\(...\\)``, ``\\[...\\]``) that
        appear in the page text.

        ``url`` is passed to trafilatura so it can resolve relative links in
        the Markdown output.
        """
        if xpath:
            tree = etree.HTML(html)
            selected = tree.xpath(xpath)
            if not selected:
                return ""
            parts = []
            for node in selected:
                if isinstance(node, etree._Element):
                    parts.append(etree.tostring(node, encoding="unicode", method="html"))
                else:
                    parts.append(str(node))
            # Wrap the fragment in a minimal HTML document so trafilatura's
            # extraction pipeline recognises it as parseable content. Without
            # this, isolated fragments (e.g. a single <p>) yield None.
            html = f"<html><body>{''.join(parts)}</body></html>"

        output_format = "txt" if text_only else "markdown"

        # Common extraction options for both formats.
        common_kwargs: dict[str, Any] = dict(
            output_format=output_format,
            include_tables=True,
            include_comments=False,
            url=url,
        )

        # Markdown-specific options: preserve links, images (alt text),
        # and inline formatting (bold/italic) so the result reads like a
        # structured document. include_formatting also helps retain
        # math/LaTeX delimiters that rely on formatting markup.
        if not text_only:
            common_kwargs.update(
                include_links=True,
                include_images=True,
                include_formatting=True,
            )

        # Try precision-first for cleaner main-content extraction. Some
        # pages (e.g. table-based layouts like HN) are rejected entirely
        # by the precision filter, so fall back to default extraction to
        # avoid returning empty content.
        result = trafilatura.extract(html, favor_precision=True, **common_kwargs)
        if not result:
            result = trafilatura.extract(html, **common_kwargs)
        return result or ""

    def _fail(self, start: float, error: str) -> ToolResult:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error("url_content tool failed: %s", error)
        return ToolResult(
            tool_name=self.name,
            output="",
            success=False,
            duration_ms=elapsed_ms,
            error=error,
        )

    def _extract_title(self, html: str) -> str:
        """Extract the ``<title>`` element text from HTML.

        Used to populate citation metadata so the knowledge store records
        which page was fetched, not just its body text. Returns an empty
        string when no ``<title>`` is present (some pages lack one).
        """
        try:
            tree = etree.HTML(html)
            if tree is not None:
                titles = tree.xpath("//title/text()")
                if titles:
                    return titles[0].strip()
        except Exception:
            logger.debug("Title extraction failed", exc_info=True)
        return ""
