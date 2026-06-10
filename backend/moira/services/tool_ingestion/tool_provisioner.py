"""Orchestrate tool provisioning: parse spec → register tools → embed.

The provisioner is the central coordinator for the dynamic tool discovery
pipeline. It takes a parsed API spec and a user's operation selections,
builds ToolDefinition objects, persists them to the database, adds them
to the in-memory catalog and executor, and embeds them for semantic
discovery.

It also handles:
  - Deleting API sources and their tools
  - Refreshing an API source (re-fetch spec, diff, update)

Credential binding is deferred — auth-required APIs register tools as
enabled=false until credentials are wired up in a future phase."""

import logging
import uuid
from typing import Any, cast

from moira.persistence.interfaces import (
    ApiSource,
    ApiSourceRepository,
    ParsedSpec,
    ToolRepository,
)
from moira.service_setup import service_provider
from moira.services.tool_ingestion.spec_parser import (
    derive_group_slug,
    generate_tool_name,
)
from moira.tools.base import ToolDefinition
from moira.tools.catalog import ToolCatalog
from moira.tools.discovery import ToolDiscovery
from moira.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

REST_TOOL_IMPL = "moira.tools.rest_tool.RESTTool"
MAX_OPERATIONS_PER_INGESTION = 200

_HINT_GENERATION_PROMPT = """\
You are a tool description writer for a research agent. For each tool below, \
write a single sentence describing when the agent should use this tool. Be \
specific about the tool's domain and capabilities. Do not mention other tools \
or use phrases like "instead of". Write in third person. Each hint should \
start with a verb like "Retrieves", "Fetches", "Looks up", "Queries", etc.

Respond with ONLY a JSON object mapping tool names to hint strings. No markdown \
fences, no explanation.

Tools:
{tool_list}"""


def build_argument_schema(
    params: list[dict[str, Any]],
    body_schema: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a JSON Schema describing a tool's arguments.

    Each parameter gets an x-parameter-location annotation so RESTTool
    knows where to route it (path, query, header). Body properties are
    merged at the top level with no location tag — they default to the
    request body."""
    properties: dict[str, Any] = {}
    required: list[str] = []

    for p in params:
        name = p.get("name", "")
        schema_def = p.get("schema_def", {"type": "string"})
        properties[name] = {
            **(schema_def if isinstance(schema_def, dict) else {"type": "string"}),
            "description": p.get("description", ""),
            "x-parameter-location": p.get("location", "query"),
        }
        if p.get("required", False):
            required.append(name)

    if body_schema and isinstance(body_schema, dict):
        for prop_name, prop_def in body_schema.get("properties", {}).items():
            if prop_name not in properties:
                properties[prop_name] = prop_def
        for r in body_schema.get("required", []):
            if r not in required:
                required.append(r)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


class ProvisionResult:
    """Outcome of a provisioning batch. Tracks per-tool success/failure."""

    def __init__(self):
        self.succeeded: list[str] = []
        self.failed: list[dict[str, str]] = []
        self.disabled: list[str] = []

    @property
    def total(self) -> int:
        return len(self.succeeded) + len(self.failed)


def _parse_raw_spec(
    spec_dict: dict[str, Any],
    spec_url: str | None = None,
) -> "ParsedSpec":
    """Parse a raw spec dict into a ParsedSpec without re-fetching.

    Used when resolve_spec already has the raw dict in hand, avoiding
    a redundant prance parse of re-serialized content."""
    from moira.services.tool_ingestion.auth_analyzer import extract_security_schemes
    from moira.services.tool_ingestion.spec_parser import (
        _detect_version,
        _extract_operations,
        _extract_server_urls,
    )

    version = _detect_version(spec_dict)
    title = spec_dict.get("info", {}).get("title", "Unknown API")
    description = spec_dict.get("info", {}).get("description", "")
    server_urls = _extract_server_urls(spec_dict, version)
    security_schemes = extract_security_schemes(spec_dict)
    operations = _extract_operations(spec_dict)

    return ParsedSpec(
        title=title,
        description=description,
        version=version,
        server_urls=server_urls,
        security_schemes=security_schemes,
        operations=operations,
        spec_url=spec_url,
    )


class ToolProvisioner:
    """Orchestrates the full tool provisioning lifecycle.

    Depends on:
      - ApiSourceRepository: persist API source metadata
      - ToolRepository: persist ToolDefinition rows
      - ToolCatalog (via service_provider): in-memory tool registry
      - ToolDiscovery (via service_provider): embedding ingestion
      - ToolExecutor (via service_provider): runtime tool registration
    """

    def __init__(
        self,
        source_repo: ApiSourceRepository,
        tool_repo: ToolRepository,
    ):
        self._source_repo = source_repo
        self._tool_repo = tool_repo

    async def _generate_usage_hints(
        self,
        operations: list[dict[str, Any]],
    ) -> dict[str, str]:
        """Generate a usage hint for each tool via the task model.

        Returns a dict mapping tool name → hint string. On failure (model
        unavailable, parse error, etc.) returns an empty dict — hints are
        optional enrichment, never blocking."""
        import json

        from moira.inference.registry import ModelRegistry

        registry = cast(ModelRegistry | None, service_provider("model_registry"))
        if registry is None:
            logger.debug("No model registry available, skipping hint generation")
            return {}

        tool_list_parts: list[str] = []
        for op in operations:
            params_desc = ""
            params = op.get("parameters", [])
            if params:
                param_names = [p.get("name", "?") for p in params[:5]]
                params_desc = f" Parameters: {', '.join(param_names)}"
            desc = op.get("description", "")[:200]
            tool_list_parts.append(
                f"- {op['name']}: {desc}{params_desc}"
            )

        prompt = _HINT_GENERATION_PROMPT.format(
            tool_list="\n".join(tool_list_parts),
        )

        logger.debug(
            "Hint generation prompt for %d tools:\n%s",
            len(operations),
            prompt[:2000],
        )

        try:
            resolved = await registry.resolve("task")
        except (ValueError, KeyError) as e:
            logger.debug("Task model not available for hint generation: %s", e)
            return {}

        try:
            raw = await resolved.client.chat_completion(
                model=resolved.model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
        except Exception as e:
            logger.warning("Hint generation call failed: %s", e)
            return {}

        content = raw.content if hasattr(raw, "content") else str(raw)

        logger.debug(
            "Hint generation response (%d chars):\n%s",
            len(content),
            content[:2000],
        )

        try:
            hints = json.loads(content.strip())
            if isinstance(hints, dict):
                valid = {k: str(v) for k, v in hints.items() if isinstance(v, str)}
                logger.debug("Parsed %d usage hints: %s", len(valid), list(valid.keys()))
                return valid
        except (json.JSONDecodeError, ValueError):
            logger.warning("Failed to parse hint generation response as JSON")

        return {}

    async def preview(
        self,
        base_url: str | None = None,
        spec_url: str | None = None,
        spec_content: str | None = None,
        group_name_override: str | None = None,
    ) -> dict[str, Any]:
        """Phase 1: Parse a spec and return a preview for the wizard.

        No tools are created. Returns the parsed operations, security
        info, and a generated source_id for tracking this ingestion
        session. The source_id is not persisted until commit()."""
        from moira.services.tool_ingestion.spec_resolver import resolve_spec

        spec_dict, spec_format, resolved_url = await resolve_spec(
            base_url=base_url,
            spec_url=spec_url,
            spec_content=spec_content,
        )

        # resolve_spec returns a raw dict when a URL was fetched, but
        # for the spec_content path it returns an empty dict (no source
        # to re-parse). We need the raw spec dict for auth analysis, so
        # parse it from content when available.
        if spec_dict and isinstance(spec_dict, dict) and spec_dict.get("info"):
            parsed = _parse_raw_spec(spec_dict, resolved_url)
        elif spec_content:
            import json

            from moira.services.tool_ingestion.spec_parser import parse_spec_content

            parsed = parse_spec_content(spec_content)
            # Recover the raw spec dict from the content for auth analysis
            try:
                spec_dict = json.loads(spec_content)
            except (json.JSONDecodeError, ValueError):
                spec_dict = {}
        else:
            raise ValueError("No spec content available for parsing")

        group_name = group_name_override or parsed.title
        group_slug = derive_group_slug(group_name)

        # Re-generate tool names with the actual group_slug
        seen_names: set[str] = set()
        for op in parsed.operations:
            proper_name = generate_tool_name(
                group_slug=group_slug,
                method=op.method,
                path=op.path,
                operation_id=op.operation_id,
                existing_names=seen_names,
            )
            op.name = proper_name
            seen_names.add(proper_name)

        source_id = str(uuid.uuid4())
        server_url = parsed.server_urls[0] if parsed.server_urls else (base_url or "")

        auth_type = None
        if parsed.security_schemes:
            from moira.services.tool_ingestion.auth_analyzer import get_auth_type_for_spec
            auth_type = get_auth_type_for_spec(spec_dict)

        return {
            "source_id": source_id,
            "api_title": parsed.title,
            "api_description": parsed.description,
            "api_version": spec_format,
            "spec_format": parsed.version,
            "server_urls": parsed.server_urls,
            "base_url": base_url or server_url,
            "security_schemes": {
                k: {
                    "scheme_type": v.scheme_type,
                    "name": v.name,
                    "location": v.location,
                    "description": v.description,
                }
                for k, v in parsed.security_schemes.items()
            },
            "operations": [
                {
                    "name": op.name,
                    "description": op.description,
                    "method": op.method,
                    "path": op.path,
                    "parameters": [
                        {
                            "name": p.name,
                            "location": p.location,
                            "required": p.required,
                            "schema_def": p.schema_def,
                            "description": p.description,
                        }
                        for p in op.parameters
                    ],
                    "request_body": {
                        "content_type": op.request_body.content_type,
                        "schema_def": op.request_body.schema_def,
                        "required": op.request_body.required,
                        "description": op.request_body.description,
                    } if op.request_body else None,
                    "tags": op.tags,
                    "deprecated": op.deprecated,
                    "security_requirements": op.security_requirements,
                    "operation_id": op.operation_id,
                }
                for op in parsed.operations
            ],
            "total_operations": len(parsed.operations),
            "auth_required": auth_type is not None,
            "auth_type": auth_type,
            "group_name": group_name,
            "group_slug": group_slug,
            "spec_url": resolved_url,
        }

    async def commit(
        self,
        source_id: str,
        base_url: str,
        spec_url: str | None,
        spec_format: str,
        group_name: str,
        auth_type: str | None,
        selected_operations: list[str],
        operations_data: list[dict[str, Any]],
        server_url: str,
        is_default: bool = False,
    ) -> ProvisionResult:
        """Phase 2: Provision selected operations as tools.

        Creates ToolDefinition rows, ApiSource row, adds tools to the
        in-memory catalog, executor, and embedding store."""
        result = ProvisionResult()

        if len(selected_operations) > MAX_OPERATIONS_PER_INGESTION:
            raise ValueError(
                f"Too many operations selected ({len(selected_operations)}). "
                f"Maximum is {MAX_OPERATIONS_PER_INGESTION}."
            )

        from moira.services.tool_ingestion.spec_parser import derive_group_slug

        group_slug = derive_group_slug(group_name)

        op_map = {op["name"]: op for op in operations_data}
        tools_to_embed: list[ToolDefinition] = []

        source = ApiSource(
            id=source_id,
            name=group_name,
            base_url=base_url,
            spec_url=spec_url,
            spec_format=spec_format,
            auth_type=auth_type,
            group_name=group_name,
            tool_count=0,
            enabled=True,
            created_at="",
            updated_at="",
        )

        # Save the tool group first — tools table has FK to tool_groups.
        await self._tool_repo.save_group({
            "name": group_slug,
            "display_name": group_name,
        })

        # Generate usage hints via the task model (best-effort, non-blocking)
        selected_op_data = [
            op_map[n] for n in selected_operations if n in op_map
        ]
        hints = await self._generate_usage_hints(selected_op_data)
        if hints:
            logger.info("Generated usage hints for %d tools", len(hints))

        for tool_name in selected_operations:
            op_data = op_map.get(tool_name)
            if op_data is None:
                result.failed.append({
                    "name": tool_name,
                    "reason": "Operation not found in preview data",
                })
                continue

            try:
                hint = hints.get(tool_name, "")
                defn = self._build_tool_definition(
                    op_data=op_data,
                    group_slug=group_slug,
                    group_name=group_name,
                    server_url=server_url,
                    source_id=source_id,
                    auth_type=auth_type,
                    is_default=is_default,
                    usage_hint=hint,
                )
                await self._tool_repo.save_tool(defn)
                tools_to_embed.append(defn)
                result.succeeded.append(tool_name)

                if not defn.enabled:
                    result.disabled.append(tool_name)

            except Exception as e:
                logger.error("Failed to provision tool '%s': %s", tool_name, e)
                result.failed.append({"name": tool_name, "reason": str(e)})

        source.tool_count = len(result.succeeded)
        await self._source_repo.save(source)

        # Wire up in-memory: catalog, executor, embeddings
        await self._register_runtime(tools_to_embed)

        logger.info(
            "Provisioned %d tools from '%s' (%d disabled, %d failed)",
            len(result.succeeded),
            group_name,
            len(result.disabled),
            len(result.failed),
        )
        return result

    async def delete_source(self, source_id: str) -> list[str]:
        """Delete an API source and all its tools.

        Removes tools from catalog, executor, embeddings, credential store,
        and database. Returns list of deleted tool names."""
        source = await self._source_repo.get(source_id)
        if source is None:
            raise ValueError(f"API source '{source_id}' not found")

        # Find all tools belonging to this source
        all_tools = await self._tool_repo.get_all_tools()
        source_tools = [
            t for t in all_tools
            if t.config.get("source_id") == source_id
        ]

        deleted_names: list[str] = []
        for defn in source_tools:
            try:
                await self._tool_repo.delete_tool(defn.name)
                deleted_names.append(defn.name)
            except Exception as e:
                logger.error("Failed to delete tool '%s': %s", defn.name, e)

        await self._source_repo.delete(source_id)

        # Remove from runtime
        await self._unregister_runtime(deleted_names)

        logger.info(
            "Deleted API source '%s' and %d tools",
            source.name,
            len(deleted_names),
        )
        return deleted_names

    async def list_sources(self) -> list[ApiSource]:
        """Return all registered API sources."""
        return await self._source_repo.get_all()

    async def get_source(self, source_id: str) -> ApiSource | None:
        """Return a single API source by ID."""
        return await self._source_repo.get(source_id)

    def _build_tool_definition(
        self,
        op_data: dict[str, Any],
        group_slug: str,
        group_name: str,
        server_url: str,
        source_id: str,
        auth_type: str | None,
        is_default: bool,
        usage_hint: str = "",
    ) -> ToolDefinition:
        """Build a ToolDefinition from an operation's preview data."""
        method = op_data.get("method", "GET")
        path = op_data.get("path", "/")
        params = op_data.get("parameters", [])
        request_body = op_data.get("request_body")

        param_dicts = [
            {
                "name": p.get("name", ""),
                "location": p.get("location", "query"),
                "required": p.get("required", False),
                "schema_def": p.get("schema_def", {"type": "string"}),
                "description": p.get("description", ""),
            }
            for p in params
        ]

        body_schema = request_body.get("schema_def") if request_body else None
        argument_schema = build_argument_schema(param_dicts, body_schema)

        fingerprint = f"{method}:{path}"

        # Auth-required tools are disabled until credentials are configured
        enabled = auth_type is None

        # Build description with optional usage hint appended
        base_desc = op_data.get("description", "")
        description = f"{base_desc} {usage_hint}" if usage_hint else base_desc

        config: dict[str, Any] = {
            "base_url": server_url,
            "endpoint": path,
            "method": method,
            "parameters": param_dicts,
            "source_id": source_id,
            "operation_fingerprint": fingerprint,
        }

        if request_body:
            config["request_body"] = {
                "content_type": request_body.get("content_type", "application/json"),
                "required": request_body.get("required", False),
            }

        if auth_type:
            config["auth_type"] = auth_type
            config["credential_ref"] = f"service.{group_slug}.auth"

        return ToolDefinition(
            name=op_data.get("name", ""),
            description=description,
            argument_schema=argument_schema,
            config=config,
            tags=op_data.get("tags", []),
            reliability="unknown",
            is_default=is_default,
            enabled=enabled,
            built_in=False,
            implementation=REST_TOOL_IMPL,
            group_name=group_slug,
        )

    async def _register_runtime(self, tools: list[ToolDefinition]) -> None:
        """Add tools to the in-memory catalog, executor, and embedding store."""
        catalog = cast(ToolCatalog | None, service_provider("tool_catalog"))
        executor = cast(ToolExecutor | None, service_provider("tool_executor"))
        discovery = cast(ToolDiscovery | None, service_provider("tool_discovery"))

        for defn in tools:
            if catalog is not None:
                catalog.add(defn)

        if executor is not None:
            executor.register_tools(tools)

        if discovery is not None:
            await discovery.ingest_tools(tools)

    async def _unregister_runtime(self, tool_names: list[str]) -> None:
        """Remove tools from the in-memory catalog, executor, and embedding store."""
        catalog = cast(ToolCatalog | None, service_provider("tool_catalog"))
        executor = cast(ToolExecutor | None, service_provider("tool_executor"))

        for name in tool_names:
            if catalog is not None and hasattr(catalog, "remove"):
                catalog.remove(name)

        if executor is not None:
            for name in tool_names:
                executor._tool_instances.pop(name, None)

        # Remove from embedding store
        discovery = cast(ToolDiscovery | None, service_provider("tool_discovery"))
        if discovery is not None:
            embedding_repo = discovery._embedding_repo
            await embedding_repo.delete(tool_names)
