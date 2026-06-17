import json
import logging
import re
import time
from typing import Any

import httpx

from moira.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_MAX_OUTPUT_CHARS = 10000


def _truncate_json_value(
    obj: Any,
    max_array_items: int = 10,
    max_string_len: int = 200,
) -> Any:
    """Recursively truncate long arrays and strings in a JSON-compatible object.

    Returns a new object where:
    - Arrays longer than *max_array_items* keep only the first N items,
      with a ``"... (M more items)"`` marker string appended.
    - String values longer than *max_string_len* are cut to N chars,
      with a ``"... (M more chars)"`` marker appended.
    - Dicts keep all keys but recurse into values.
    - Scalars (int, float, bool, None) are unchanged.

    The result is always valid JSON when serialized.
    """
    if isinstance(obj, str):
        if len(obj) > max_string_len:
            return obj[:max_string_len] + (f"... ({len(obj) - max_string_len} more chars)")
        return obj
    elif isinstance(obj, list):
        items = [
            _truncate_json_value(item, max_array_items, max_string_len)
            for item in obj[:max_array_items]
        ]
        if len(obj) > max_array_items:
            items.append(f"... ({len(obj) - max_array_items} more items)")
        return items
    elif isinstance(obj, dict):
        return {
            k: _truncate_json_value(v, max_array_items, max_string_len) for k, v in obj.items()
        }
    else:
        return obj


def _serialize_json_truncated(
    data: Any,
    max_chars: int = _MAX_OUTPUT_CHARS,
) -> str:
    """Serialize *data* as indented JSON, truncating if over *max_chars*.

    Applies progressively more aggressive array/string limits until the
    serialized output fits.  Falls back to character truncation only if
    all rounds fail (extremely rare).
    """
    full = json.dumps(data, indent=2)
    if len(full) <= max_chars:
        return full

    for max_items, max_str in [(10, 200), (5, 100), (3, 50)]:
        trimmed = _truncate_json_value(data, max_items, max_str)
        result = json.dumps(trimmed, indent=2)
        if len(result) <= max_chars:
            return result

    return full[:max_chars]


class RESTTool(BaseTool):
    """Execute a tool by calling a REST endpoint. Supports path parameter
    resolution, query/body/header separation, and configurable auth.

    For ingested API tools, the ToolDefinition.config contains:
      - base_url: The API server URL
      - endpoint: The path template (e.g., "/weather/{city}")
      - method: HTTP method
      - parameters: List of parameter specs with location info

    For legacy YAML-defined tools, config contains endpoint + method only.
    The execution logic handles both formats."""

    def __init__(self, definition, timeout: float = 30.0):
        super().__init__(definition)
        self._timeout = timeout

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        start = time.monotonic()
        try:
            # Copy so _resolve_path's pop() doesn't mutate the caller's
            # dict (research.py reuses it for the tool_result event).
            args = dict(args)
            config = self.definition.config
            method = (config.get("method") or "GET").upper()
            base_url = config.get("base_url", "")
            path_template = config.get("endpoint", "")

            # Ingested API tools use base_url + path template.
            # Legacy YAML tools store the full URL in endpoint.
            if base_url and not path_template.startswith("http"):
                url = self._resolve_path(f"{base_url}{path_template}", args, config)
            else:
                url = path_template

            query_params, body, extra_headers = self._categorize_args(args, config)

            headers = {**extra_headers}

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(
                    method,
                    url,
                    params=query_params or None,
                    json=body or None,
                    headers=headers or None,
                )
                resp.raise_for_status()
                elapsed_ms = int((time.monotonic() - start) * 1000)
                try:
                    output = _serialize_json_truncated(resp.json())
                except Exception:
                    output = resp.text[:_MAX_OUTPUT_CHARS]
                return ToolResult(
                    tool_name=self.name,
                    output=output,
                    success=True,
                    duration_ms=elapsed_ms,
                )
        except Exception as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.error("REST tool '%s' failed: %s", self.name, e)
            return ToolResult(
                tool_name=self.name,
                output="",
                success=False,
                duration_ms=elapsed_ms,
                error=str(e),
            )

    def _resolve_path(self, url: str, args: dict[str, Any], config: dict[str, Any]) -> str:
        """Substitute path parameters like {city} with actual values from args.

        Consumed path parameters are removed from args so they are not also
        sent as query/body parameters."""
        param_specs = config.get("parameters", [])
        path_param_names = {
            p["name"] for p in param_specs if isinstance(p, dict) and p.get("location") == "path"
        }

        consumed: set[str] = set()
        for name in path_param_names:
            if name in args:
                # URL-encode the value for safe substitution
                url = url.replace(f"{{{name}}}", str(args[name]))
                consumed.add(name)

        # Also handle any remaining {param} placeholders not in specs
        for match in re.finditer(r"\{(\w+)\}", url):
            param_name = match.group(1)
            if param_name in args:
                url = url.replace(f"{{{param_name}}}", str(args[param_name]))
                consumed.add(param_name)

        for name in consumed:
            args.pop(name, None)

        return url

    def _categorize_args(
        self, args: dict[str, Any], config: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
        """Split args into query params, body, and header params.

        Uses the x-parameter-location from the argument schema (set by
        spec_parser during ingestion) to determine where each argument
        goes. Args without a location tag default to query for GET
        requests and body for everything else."""
        param_specs = config.get("parameters", [])
        spec_locations: dict[str, str] = {}
        for p in param_specs:
            if isinstance(p, dict):
                spec_locations[p.get("name", "")] = p.get("location", "")

        # Also check argument_schema for x-parameter-location annotations
        arg_schema = self.definition.argument_schema
        schema_locations: dict[str, str] = {}
        if arg_schema and "properties" in arg_schema:
            for prop_name, prop_def in arg_schema["properties"].items():
                if isinstance(prop_def, dict):
                    loc = prop_def.get("x-parameter-location", "")
                    if loc:
                        schema_locations[prop_name] = loc

        query_params: dict[str, Any] = {}
        body: dict[str, Any] = {}
        headers: dict[str, str] = {}
        method = (config.get("method") or "GET").upper()

        for name, value in args.items():
            location = spec_locations.get(name) or schema_locations.get(name)

            if location == "header":
                headers[name] = str(value)
            elif location == "query":
                query_params[name] = value
            elif location == "path":
                # Already consumed by _resolve_path — skip
                pass
            elif location == "cookie":
                # Cookie params not supported yet — treat as header
                headers[name] = str(value)
            else:
                # No location info: default by HTTP method
                if method == "GET":
                    query_params[name] = value
                else:
                    body[name] = value

        return query_params, body, headers
