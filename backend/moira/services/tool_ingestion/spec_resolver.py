"""Fetch and resolve OpenAPI/Swagger specs from URLs, probes, or uploads.

Handles the three garden paths:
  A. Auto-discover: probe well-known paths at a base URL
  B. Explicit URL: fetch from a user-provided spec URL
  C. File upload: parse from raw file content

Also handles Swagger 2.0 specs transparently — the parser converts them."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SPEC_PROBE_PATHS = [
    "/openapi.json",
    "/openapi.yaml",
    "/swagger.json",
    "/swagger.yaml",
    "/v3/api-docs",
    "/v2/api-docs",
    "/api-docs",
    "/api/docs",
    "/docs/spec",
    "/.well-known/openapi.json",
    "/.well-known/schema-discovery",
]

PROBE_TIMEOUT = 10.0


class SpecResolutionError(Exception):
    """Raised when a spec cannot be found or fetched."""


async def resolve_spec(
    base_url: str | None = None,
    spec_url: str | None = None,
    spec_content: str | None = None,
) -> tuple[dict[str, Any], str, str | None]:
    """Resolve a spec from one of three sources.

    Returns (spec_dict, spec_format, resolved_spec_url).

    Priority:
      1. spec_content — raw uploaded file content
      2. spec_url — explicit URL to fetch
      3. base_url — auto-probe well-known paths

    Raises SpecResolutionError if no spec can be found."""
    from moira.services.tool_ingestion.spec_parser import parse_spec_content

    if spec_content:
        _validate_spec_text(spec_content)
        parsed = parse_spec_content(spec_content)
        return _parsed_to_tuple(parsed, None)

    if spec_url:
        spec_text = await _fetch_text(spec_url)
        parsed = parse_spec_content(spec_text)
        return _parsed_to_tuple(parsed, spec_url)

    if base_url:
        spec_text, resolved_url = await _probe_base_url(base_url)
        parsed = parse_spec_content(spec_text)
        return _parsed_to_tuple(parsed, resolved_url)

    raise SpecResolutionError(
        "No spec source provided (base_url, spec_url, or spec_content required)"
    )


async def _fetch_text(url: str) -> str:
    """Fetch text content from a URL."""
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            resp = await client.get(
                url,
                headers={"Accept": "application/json, application/yaml, text/yaml"},
            )
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPError as e:
        raise SpecResolutionError(f"Failed to fetch spec from {url}: {e}") from e


async def _probe_base_url(base_url: str) -> tuple[str, str]:
    """Probe well-known paths at a base URL for an API spec.

    Returns (spec_text, resolved_url). Tries each probe path in order
    and returns the first one that parses successfully."""
    base = base_url.rstrip("/")
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        for probe_path in SPEC_PROBE_PATHS:
            url = f"{base}{probe_path}"
            try:
                resp = await client.get(
                    url,
                    headers={"Accept": "application/json, application/yaml, text/yaml"},
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    continue

                text = resp.text
                if not text.strip():
                    continue

                # Quick validation: try to parse it
                _validate_spec_text(text)
                return text, url

            except Exception as e:
                errors.append(f"{url}: {e}")
                continue

    tried = ", ".join(SPEC_PROBE_PATHS[:5]) + ", ..."
    raise SpecResolutionError(
        f"No API description found at {base_url}. "
        f"Probed: {tried}. "
        f"Try providing the spec URL directly."
    )


def _validate_spec_text(text: str) -> None:
    """Quick validation that text looks like an OpenAPI or Swagger spec.

    Raises ValueError if it doesn't look like a valid spec."""
    import json

    stripped = text.strip()

    # Try JSON parse
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e

        if "openapi" not in data and "swagger" not in data:
            raise ValueError("JSON does not contain 'openapi' or 'swagger' key")
        return

    # Try YAML — look for openapi: or swagger: key
    if "openapi:" in stripped[:200] or "swagger:" in stripped[:200]:
        return

    raise ValueError("Content does not appear to be an OpenAPI or Swagger spec")


def _parsed_to_tuple(
    parsed: Any,
    spec_url: str | None,
) -> tuple[dict[str, Any], str, str | None]:
    """Convert a ParsedSpec back to a raw dict for the resolver return.

    Note: this is a convenience for the resolver layer. The parser already
    returns a ParsedSpec, but callers of resolve_spec may want the raw spec
    dict for storage or further processing. We re-parse here to get the raw
    dict — this is inefficient but correct. A future refactor could have
    parse_spec return both the ParsedSpec and the raw dict."""
    # Re-parse to get the raw spec dict
    # This is a temporary approach — the full integration will restructure
    # this to avoid double parsing.
    from moira.services.tool_ingestion.spec_parser import _make_parser

    if parsed.spec_url:
        source = parsed.spec_url
    elif spec_url:
        source = spec_url
    else:
        return {}, parsed.version, spec_url

    parser = _make_parser(spec_source=source)
    return parser.specification, parsed.version, spec_url
