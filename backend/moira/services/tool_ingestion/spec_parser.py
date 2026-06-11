"""Parse OpenAPI and Swagger 2.0 specs into ToolCandidate lists.

Uses prance to resolve $ref references and produce a fully-dereferenced
spec dictionary. Then iterates over paths and operations to extract
ToolCandidate objects with their parameter and request body schemas."""

import logging
import re
from typing import Any

from prance import ResolvingParser
from prance.util.resolver import RESOLVE_FILES, RESOLVE_HTTP

from moira.persistence.interfaces import (
    ParameterSpec,
    ParsedSpec,
    RequestBodySpec,
    ToolCandidate,
)
from moira.services.tool_ingestion.auth_analyzer import extract_security_schemes

logger = logging.getLogger(__name__)


def _make_parser(
    spec_source: str | None = None,
    spec_string: str | None = None,
) -> ResolvingParser:
    """Create a ResolvingParser with validation disabled.

    Real-world specs often contain minor schema violations (e.g. ``type: ''``)
    that strict OpenAPI validators reject. We only need $ref resolution and
    path/operation extraction, so we skip validation entirely."""
    original = ResolvingParser._validate

    def _noop_validate(self: ResolvingParser) -> None:
        self.valid = True

    ResolvingParser._validate = _noop_validate  # type: ignore[assignment]
    try:
        parser = ResolvingParser(
            spec_source if spec_source else None,
            spec_string=spec_string,
            resolve_types=RESOLVE_HTTP | RESOLVE_FILES,
            strict=False,
        )
    finally:
        ResolvingParser._validate = original  # type: ignore[assignment]
    return parser


def parse_spec(
    spec_source: str,
    spec_url: str | None = None,
) -> ParsedSpec:
    """Parse an OpenAPI or Swagger spec from a file path or URL.

    spec_source can be a local file path or a URL. prance resolves all
    $ref references including cross-file and HTTP references."""
    parser = _make_parser(spec_source=spec_source)
    spec: dict[str, Any] = parser.specification

    version = _detect_version(spec)
    title = spec.get("info", {}).get("title", "Unknown API")
    description = spec.get("info", {}).get("description", "")
    server_urls = _extract_server_urls(spec, version)
    security_schemes = extract_security_schemes(spec)
    operations = _extract_operations(spec)

    return ParsedSpec(
        title=title,
        description=description,
        version=version,
        server_urls=server_urls,
        security_schemes=security_schemes,
        operations=operations,
        spec_url=spec_url,
    )


def parse_spec_content(content: str) -> ParsedSpec:
    """Parse a spec from raw string content (uploaded file).

    Writes content to a temporary file for prance, then parses it."""
    import tempfile
    from pathlib import Path

    suffix = ".yaml"
    if content.strip().startswith("{"):
        suffix = ".json"

    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as f:
        f.write(content)
        tmp_path = f.name

    try:
        return parse_spec(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _transliterate(text: str) -> str:
    """Replace Latin diacritics with their base ASCII counterparts.

    e.g. PokéAPI -> PokeAPI, Führer -> Fuhrer. Uses unicodedata
    decomposition: combining marks are stripped after NFD decomposition."""
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def derive_group_slug(group_name: str) -> str:
    """Derive a group_slug from a group_name.

    Transliterates diacritics, lowercases, replaces spaces and hyphens with
    underscores, strips non-alphanumeric characters, truncates to 48 chars."""
    slug = _transliterate(group_name).lower()
    slug = re.sub(r"[\s\-]+", "_", slug)
    slug = re.sub(r"[^a-z0-9_]", "", slug)
    slug = re.sub(r"_+", "_", slug)
    slug = slug.strip("_")
    return slug[:48] or "api"


def generate_tool_name(
    group_slug: str,
    method: str,
    path: str,
    operation_id: str | None = None,
    existing_names: set[str] | None = None,
) -> str:
    """Generate a globally unique tool name.

    Format: group_slug__method_id where method_id is derived from
    operationId (preferred) or method + non-parameter path segments."""
    method_lower = method.lower()

    if operation_id:
        method_id = _sanitize_operation_id(operation_id)
    else:
        method_id = _derive_method_id(method_lower, path)

    base_name = f"{group_slug}__{method_id}"

    if existing_names is None:
        return base_name

    # Deduplicate
    name = base_name
    counter = 2
    while name in existing_names:
        name = f"{base_name}_{counter}"
        counter += 1
    return name


def _detect_version(spec: dict[str, Any]) -> str:
    """Detect whether this is OpenAPI 3.x or Swagger 2.0."""
    openapi = spec.get("openapi", "")
    if openapi:
        if openapi.startswith("3.1"):
            return "openapi_3_1"
        return "openapi_3_0"
    return "swagger_2"


def _extract_server_urls(spec: dict[str, Any], version: str) -> list[str]:
    """Extract server URLs from the spec."""
    if version == "swagger_2":
        host = spec.get("host", "")
        base_path = spec.get("basePath", "")
        schemes = spec.get("schemes", ["https"])
        if not host:
            return []
        return [f"{s}://{host}{base_path}" for s in schemes]

    servers = spec.get("servers", [])
    urls: list[str] = []
    for server in servers:
        if isinstance(server, dict):
            url = server.get("url", "")
            if url:
                urls.append(url)
    return urls


def _extract_operations(spec: dict[str, Any]) -> list[ToolCandidate]:
    """Extract all operations from the spec's paths."""
    paths = spec.get("paths", {})
    operations: list[ToolCandidate] = []
    seen_names: set[str] = set()

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue

            parameters = _extract_parameters(path_item, operation)
            request_body = _extract_request_body(operation)
            responses = _extract_responses(operation)
            tags = operation.get("tags", [])
            operation_id = operation.get("operationId")
            deprecated = operation.get("deprecated", False)
            description = _build_description(operation)
            security_reqs = _extract_security_requirements(operation, spec)

            name = generate_tool_name(
                group_slug="api",
                method=method,
                path=path,
                operation_id=operation_id,
                existing_names=seen_names,
            )
            seen_names.add(name)

            operations.append(
                ToolCandidate(
                    name=name,
                    description=description,
                    method=method.upper(),
                    path=path,
                    parameters=parameters,
                    request_body=request_body,
                    responses=responses,
                    security_requirements=security_reqs,
                    tags=tags if isinstance(tags, list) else [],
                    operation_id=operation_id,
                    deprecated=deprecated,
                )
            )

    return operations


def _extract_parameters(
    path_item: dict[str, Any],
    operation: dict[str, Any],
) -> list[ParameterSpec]:
    """Extract parameters from both path-level and operation-level."""
    # Merge path-level and operation-level parameters
    raw_params: list[dict[str, Any]] = list(path_item.get("parameters", []))
    op_params = operation.get("parameters", [])

    # Operation params override path params with same name+location
    op_param_keys = {(p.get("name"), p.get("in")) for p in op_params if isinstance(p, dict)}
    for p in raw_params:
        if isinstance(p, dict) and (p.get("name"), p.get("in")) not in op_param_keys:
            op_params.append(p)

    params: list[ParameterSpec] = []
    for p in op_params:
        if not isinstance(p, dict):
            continue
        schema = p.get("schema", {"type": "string"})
        params.append(
            ParameterSpec(
                name=p.get("name", ""),
                location=p.get("in", "query"),
                required=p.get("required", False),
                schema_def=schema if isinstance(schema, dict) else {"type": "string"},
                description=p.get("description", ""),
            )
        )
    return params


def _extract_request_body(operation: dict[str, Any]) -> RequestBodySpec | None:
    """Extract request body from an OpenAPI 3.x operation."""
    rb = operation.get("requestBody")
    if not isinstance(rb, dict):
        return None

    content = rb.get("content", {})
    for content_type, media in content.items():
        if not isinstance(media, dict):
            continue
        schema = media.get("schema", {})
        return RequestBodySpec(
            content_type=content_type,
            schema_def=schema if isinstance(schema, dict) else {},
            required=rb.get("required", False),
            description=rb.get("description", ""),
        )
    return None


def _extract_responses(operation: dict[str, Any]) -> dict[str, str]:
    """Extract response status codes and their descriptions."""
    responses = operation.get("responses", {})
    result: dict[str, str] = {}
    for code, resp in responses.items():
        if isinstance(resp, dict):
            result[str(code)] = resp.get("description", "")
    return result


def _extract_security_requirements(
    operation: dict[str, Any],
    spec: dict[str, Any],
) -> list[str]:
    """Extract security requirement names for an operation.

    Falls back to global security if operation doesn't declare its own."""
    security = operation.get("security")
    if security is None:
        security = spec.get("security", [])

    names: list[str] = []
    for req in security:
        if isinstance(req, dict):
            names.extend(req.keys())
    return names


def _build_description(operation: dict[str, Any]) -> str:
    """Build a description from summary and description fields."""
    parts: list[str] = []
    summary = operation.get("summary", "")
    desc = operation.get("description", "")
    if summary:
        parts.append(summary)
    if desc and desc != summary:
        parts.append(desc)
    return " — ".join(parts) if parts else ""


def _sanitize_operation_id(operation_id: str) -> str:
    """Sanitize an operationId into a valid snake_case method identifier."""
    s = _transliterate(operation_id)
    # camelCase / PascalCase → snake_case before lowercasing
    s = re.sub(r"([a-z])([A-Z])", r"\1_\2", s)
    # Also split consecutive uppercase (e.g., "parseHTML" → "parse_HTML")
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = s.lower()
    # Non-alphanumeric → underscore, collapse multiples
    s = re.sub(r"[^a-z0-9]", "_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")
    return s or "operation"


def _derive_method_id(method: str, path: str) -> str:
    """Derive a method_id from HTTP method and non-parameter path segments.

    Joins all non-parameter path segments (those not wrapped in braces)
    with underscores. E.g., GET /books/{book_id}/reviews → get_books_reviews."""
    segments: list[str] = []
    for segment in path.split("/"):
        if not segment:
            continue
        # Skip parameter placeholders like {id} or {book_id}
        if segment.startswith("{") and segment.endswith("}"):
            continue
        segments.append(segment)

    path_part = "_".join(segments) if segments else "root"
    return f"{method}_{path_part}"
