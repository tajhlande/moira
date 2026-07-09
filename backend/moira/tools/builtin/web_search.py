"""Web search tool using a self-hosted SearXNG instance.

Queries the SearXNG JSON API and returns formatted search results with
titles, URLs, and content snippets. The SearXNG query URL is stored in
the tool's config dict (``searxng_base_url`` key) and configured via
the YAML config file or the tools API.

SearXNG supports standard search engine query syntax including ``site:``
filters — the LLM can construct those directly in the query string."""

import logging
import re
import time
from typing import Any

import httpx

from moira.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RESULTS = 5

# SearXNG / underlying engines (primarily Google) append "Missing: … |
# Show results with:…" to snippet content when query terms aren't all
# found.  This is a search-engine artifact, not real page content, so
# we strip it at the source.
_MISSING_RE = re.compile(
    r"\s*Missing:.*?(?:Show results with:.*)?$",
    re.IGNORECASE | re.DOTALL,
)


def _clean_snippet(content: str) -> str:
    """Remove SearXNG 'Missing:' / 'Show results with:' artifacts."""
    return _MISSING_RE.sub("", content).strip()


class WebSearchTool(BaseTool):
    """Search the web using a SearXNG instance. Returns results with titles,
    URLs, and content snippets formatted for LLM consumption."""

    tool_name = "web_search"
    tool_description = (
        "Search the web using SearXNG. Returns results with titles, URLs, "
        "and content snippets. Supports standard search engine query syntax "
        "including site: filters (e.g. 'site:github.com moira project'). "
        "Optional parameters: categories (e.g. 'general,news'), language "
        "(e.g. 'en'), time_range ('day', 'month', 'year')."
    )
    tool_group = "standard"
    tool_argument_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "The search query. Supports site: filters and other search engine syntax."
                ),
            },
            "categories": {
                "type": "string",
                "description": (
                    "Comma-separated search categories (e.g. 'general', 'news', 'science')"
                ),
            },
            "language": {
                "type": "string",
                "description": "Language code for search results (e.g. 'en', 'de', 'fr')",
            },
            "time_range": {
                "type": "string",
                "description": "Time range for results: 'day', 'month', or 'year'",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of search results to return",
                "default": 5,
            },
        },
        "required": ["query"],
    }
    tool_config_schema = {
        "type": "object",
        "properties": {
            "searxng_base_url": {
                "type": "string",
                "title": "SearXNG Base URL",
                "description": "URL of the SearXNG instance (e.g. http://localhost:8888)",
            },
        },
        "required": ["searxng_base_url"],
    }

    def __init__(
        self,
        definition,
        timeout: float = _DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ):
        super().__init__(definition)
        self._timeout = timeout
        self._test_client = client

    def _get_base_url(self) -> str:
        return self.definition.config.get("searxng_base_url", "").rstrip("/")

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        start = time.monotonic()

        query = args.get("query", "").strip()
        if not query:
            return self._fail(start, "Missing required parameter: query")

        base_url = self._get_base_url()
        if not base_url:
            return self._fail(
                start,
                "SearXNG base URL not configured. "
                "Set searxng_base_url in the web_search tool config.",
            )

        max_results = args.get("max_results", _DEFAULT_MAX_RESULTS)
        params: dict[str, Any] = {
            "q": query,
            "format": "json",
        }
        if args.get("categories"):
            params["categories"] = args["categories"]
        if args.get("language"):
            params["language"] = args["language"]
        if args.get("time_range"):
            params["time_range"] = args["time_range"]

        try:
            if self._test_client:
                resp = await self._test_client.get(
                    f"{base_url}/search",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(f"{base_url}/search", params=params)
                    resp.raise_for_status()
                    data = resp.json()
        except httpx.TimeoutException:
            return self._fail(start, f"SearXNG request timed out after {self._timeout}s")
        except httpx.HTTPStatusError as e:
            return self._fail(
                start,
                f"SearXNG returned HTTP {e.response.status_code}: {e.response.text[:200]}",
            )
        except Exception as e:
            return self._fail(start, f"SearXNG request failed: {e}")

        results = data.get("results", [])
        unresponsive_engines = data.get("unresponsive_engines", [])

        if not results:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if unresponsive_engines:
                logger.warning(
                    "web_search: no results for '%s'; unresponsive engines: %s",
                    query[:80],
                    unresponsive_engines,
                )
            return ToolResult(
                tool_name=self.name,
                output="No search results found.",
                success=True,
                duration_ms=elapsed_ms,
                metadata={
                    "results": [],
                    "unresponsive_engines": unresponsive_engines,
                },
            )

        trimmed = results[:max_results]
        output = self._format_results(trimmed)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "web_search returned %d results for query '%s'",
            len(trimmed),
            query[:80],
        )
        if unresponsive_engines:
            logger.info(
                "web_search: unresponsive engines for '%s': %s",
                query[:80],
                unresponsive_engines,
            )

        # Carry structured per-result data so downstream nodes can create
        # citations with proper url/title without re-parsing the text output.
        structured_results = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": _clean_snippet(r.get("content", "")),
            }
            for r in trimmed
        ]

        return ToolResult(
            tool_name=self.name,
            output=output,
            success=True,
            duration_ms=elapsed_ms,
            metadata={
                "results": structured_results,
                "unresponsive_engines": unresponsive_engines,
            },
        )

    @staticmethod
    def _format_results(results: list[dict]) -> str:
        parts: list[str] = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            snippet = _clean_snippet(r.get("content", ""))
            engines = r.get("engines", [])
            engine_str = ", ".join(engines) if isinstance(engines, list) else str(engines)
            parts.append(f"[{i}] {title}")
            if url:
                parts.append(f"    URL: {url}")
            if engine_str:
                parts.append(f"    Source: {engine_str}")
            if snippet:
                parts.append(f"    Snippet: {snippet}")
            parts.append("")
        return "\n".join(parts)

    def _fail(self, start: float, error: str) -> ToolResult:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error("web_search tool failed: %s", error)
        return ToolResult(
            tool_name=self.name,
            output="",
            success=False,
            duration_ms=elapsed_ms,
            error=error,
        )
