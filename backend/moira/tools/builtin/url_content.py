"""URL content retrieval tool: fetches a web page and returns its content.

Uses httpx for async HTTP and BeautifulSoup for HTML parsing. Supports
text-only extraction (strips HTML tags), XPath-based subsetting via lxml,
and optional summarization via a sub-agent call."""

import logging
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup
from lxml import etree

from moira.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# Maximum response size to accept (bytes). Prevents loading enormous pages
# into memory. 5MB is generous enough for most research-quality pages.
_MAX_RESPONSE_SIZE = 5 * 1024 * 1024

# Maximum text length to return in ToolResult. Truncation preserves the
# beginning of the content, which is typically the most useful part.
_MAX_OUTPUT_LENGTH = 100_000


class UrlContentTool(BaseTool):
    """Fetches a URL and returns its content. Supports text-only extraction,
    XPath subsetting, and optional summarization."""

    tool_name = "url_content"
    tool_description = (
        "Retrieve the content of a web page given its URL. "
        "Returns text-only content by default (HTML stripped). "
        "Supports XPath selectors to extract a subset of content "
        "and optional summarization via a sub-agent."
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
                "description": "Return only text content, stripping HTML (default: true)",
            },
            "xpath": {
                "type": "string",
                "description": "XPath expression to extract a subset of the page",
            },
            "summarize": {
                "type": "boolean",
                "description": "Summarize the content via a sub-agent (default: false)",
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

        text_only = args.get("text_only", True)
        xpath_expr = args.get("xpath", "")
        summarize = args.get("summarize", False)

        try:
            html = await self._fetch(url)
        except Exception as e:
            return self._fail(start, f"Failed to fetch {url}: {e}")

        try:
            content = self._extract(html, text_only=text_only, xpath=xpath_expr)
        except Exception as e:
            return self._fail(start, f"Failed to parse content from {url}: {e}")

        if summarize:
            content = await self._summarize(content, url)

        content = content[:_MAX_OUTPUT_LENGTH]
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_name=self.name,
            output=content,
            success=True,
            duration_ms=elapsed_ms,
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

    def _extract(self, html: str, text_only: bool = True, xpath: str = "") -> str:
        """Extract content from HTML. If xpath is provided, uses lxml to
        select a subtree first. If text_only, strips remaining HTML tags."""
        if xpath:
            tree = etree.HTML(html)
            selected = tree.xpath(xpath)
            if not selected:
                return ""
            # xpath results can be elements or strings
            parts = []
            for node in selected:
                if isinstance(node, etree._Element):
                    parts.append(etree.tostring(node, encoding="unicode", method="html"))
                else:
                    parts.append(str(node))
            html = "".join(parts)

        if text_only:
            soup = BeautifulSoup(html, "lxml")
            # Remove script and style elements before extracting text
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)

        return html

    async def _summarize(self, content: str, url: str) -> str:
        """Summarize content via the inference service. This is a placeholder
        — the full sub-agent integration will be wired in later. For now,
        returns the content unchanged with a note."""
        # TODO: wire up sub-agent summarization through the inference client.
        # This requires access to the model registry, which isn't available
        # in the tool execution context yet. Will be implemented as part of
        # the sub-agent orchestration feature.
        return content

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
