"""Web search tool using a self-hosted SearXNG instance.

Queries the SearXNG JSON API and returns formatted search results with
titles, URLs, and content snippets. The SearXNG query URL is stored in
the tool's config dict (``searxng_base_url`` key) and configured via
the YAML config file or the tools API.

SearXNG supports standard search engine query syntax including ``site:``
filters — the LLM can construct those directly in the query string.

Search results are cached in a local SQLite database. Cache settings are
configured via the system settings framework:

- ``web_search.cache_enabled`` (boolean, default true)
- ``web_search.cache_ttl_seconds`` (integer, default 604800 = 7 days)

The cache DB path defaults to ``./data/search_cache.db`` and can be
overridden via the ``cache_db_path`` key in the tool config dict.
"""

import hashlib
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from moira.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RESULTS = 5
_DEFAULT_CACHE_TTL_SECONDS = 604800
_DEFAULT_CACHE_DB_PATH = "./data/search_cache.db"

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


class SearchCache:
    """SQLite-backed cache for raw SearXNG JSON responses.

    Caches responses keyed on normalized (query, categories, language,
    time_range).  max_results is excluded from the key so different
    result limits share the same cache entry — the stored response is
    the full untrimmed SearXNG payload, and trimming is applied on read.
    """

    def __init__(self, db_path: str, ttl_seconds: int = _DEFAULT_CACHE_TTL_SECONDS):
        self._db_path = db_path
        self._ttl = timedelta(seconds=ttl_seconds)
        self._init_db()

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS search_cache (
                    cache_key    TEXT PRIMARY KEY,
                    query_text   TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at   TEXT NOT NULL,
                    expires_at   TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _make_key(
        query: str,
        categories: str | None,
        language: str | None,
        time_range: str | None,
    ) -> str:
        normalized = "|".join(
            [
                (query or "").strip().lower(),
                (categories or "").strip().lower(),
                (language or "").strip().lower(),
                (time_range or "").strip().lower(),
            ]
        )
        return hashlib.sha256(normalized.encode()).hexdigest()

    def get(
        self,
        query: str,
        categories: str | None,
        language: str | None,
        time_range: str | None,
    ) -> dict | None:
        """Return cached SearXNG response, or None on miss/expiry."""
        key = self._make_key(query, categories, language, time_range)
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT response_json, expires_at FROM search_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            response_json, expires_at = row
            expires = datetime.fromisoformat(expires_at)
            if datetime.now(timezone.utc) > expires:
                conn.execute("DELETE FROM search_cache WHERE cache_key = ?", (key,))
                conn.commit()
                return None
            return json.loads(response_json)
        finally:
            conn.close()

    def put(
        self,
        query: str,
        categories: str | None,
        language: str | None,
        time_range: str | None,
        response: dict,
    ) -> None:
        """Store a SearXNG response in the cache."""
        key = self._make_key(query, categories, language, time_range)
        now = datetime.now(timezone.utc)
        expires = now + self._ttl
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO search_cache
                    (cache_key, query_text, response_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    key,
                    query,
                    json.dumps(response),
                    now.isoformat(),
                    expires.isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def cleanup(self) -> int:
        """Delete expired entries and vacuum the database.

        Safe to call at startup or periodically. Returns the number of
        expired entries removed. Does nothing if the DB file does not
        exist yet.
        """
        if not Path(self._db_path).exists():
            logger.debug("Search cache: no DB at %s, skipping cleanup", self._db_path)
            return 0
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute("DELETE FROM search_cache WHERE expires_at < ?", (now_iso,))
            deleted = cursor.rowcount
            conn.commit()
            conn.execute("VACUUM")
            logger.info(
                "Search cache: pruned %d expired entries, VACUUM complete",
                deleted,
            )
            return deleted
        finally:
            conn.close()


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
        self._cache: SearchCache | None = None
        self._cache_ttl_seconds: int | None = None

    def _get_base_url(self) -> str:
        return self.definition.config.get("searxng_base_url", "").rstrip("/")

    async def _read_cache_settings(self) -> tuple[bool, int]:
        """Read cache config from system settings.

        Falls back to the tool config dict (then hardcoded defaults)
        if the settings service is unavailable — e.g. in unit tests
        without the full service stack.
        """
        try:
            from moira.service_setup import service_provider

            svc = service_provider("settings_service")
            enabled = await svc.get_typed("web_search.cache_enabled")
            ttl = await svc.get_typed("web_search.cache_ttl_seconds")
            return (
                enabled if enabled is not None else True,
                ttl if ttl is not None else _DEFAULT_CACHE_TTL_SECONDS,
            )
        except Exception:
            return (
                self.definition.config.get("cache_enabled", True),
                self.definition.config.get("cache_ttl_seconds", _DEFAULT_CACHE_TTL_SECONDS),
            )

    async def _get_cache(self) -> SearchCache | None:
        """Return the search cache, or None if disabled.

        Reads cache settings from system settings on each call so UI
        changes take effect immediately. The SearchCache instance is
        reused across calls unless the TTL setting changes.
        """
        enabled, ttl_seconds = await self._read_cache_settings()
        if not enabled or ttl_seconds <= 0:
            return None
        if self._cache is None or self._cache_ttl_seconds != ttl_seconds:
            db_path = self.definition.config.get("cache_db_path", _DEFAULT_CACHE_DB_PATH)
            self._cache = SearchCache(db_path, ttl_seconds)
            self._cache_ttl_seconds = ttl_seconds
        return self._cache

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

        # Check cache before hitting SearXNG
        cache = await self._get_cache()
        cache_hit = False
        data: dict | None = None

        if cache is not None:
            data = cache.get(
                query,
                args.get("categories"),
                args.get("language"),
                args.get("time_range"),
            )
            if data is not None:
                cache_hit = True
                logger.info("web_search cache hit for '%s'", query[:80])

        if data is None:
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

            # Cache any successful SearXNG response. Previously this
            # skipped caching when any engine was unresponsive, but since
            # SearXNG frequently has 1-2 down engines, that guard prevented
            # the cache from ever populating (zero entries, zero hits across
            # hundreds of calls). Degraded results are still valid for
            # caching — the same query would likely see similar degradation
            # next time, and a cache hit beats no caching at all. Empty
            # result lists are cached too, to avoid re-querying SearXNG for
            # terms that genuinely have no hits. HTTP errors are excluded
            # because they return early via _fail() before reaching here.
            if cache is not None:
                cache.put(
                    query,
                    args.get("categories"),
                    args.get("language"),
                    args.get("time_range"),
                    data,
                )

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
                    "cache_hit": cache_hit,
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
                "cache_hit": cache_hit,
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
