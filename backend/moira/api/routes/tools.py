"""Tool catalog, group, and ingestion endpoints.

Owns the API surface for the Tools tab in the frontend: tool CRUD, group
management, OpenAPI spec ingestion, and embedding-debug search. Routes
that mutate tools also keep the in-memory catalog, executor, and LanceDB
embeddings in sync."""

import logging
from typing import Any, cast

from fastapi import APIRouter, HTTPException

from moira.embeddings.local import LocalEmbeddingProvider
from moira.persistence.lancedb.tool_embeddings import ToolEmbeddingRepository
from moira.service_setup import service_provider

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Repository / service accessors ---


def _tool_repo():
    from moira.persistence.interfaces import ToolRepository

    return cast(ToolRepository, service_provider("tool_repository"))


def _provisioner():
    from moira.services.tool_ingestion.tool_provisioner import ToolProvisioner

    return cast(ToolProvisioner, service_provider("tool_provisioner"))


# --- Index sync helpers ---


async def _sync_tool_to_index(tool) -> None:
    """Update the LanceDB vector index when a tool's enabled state changes.
    Enabled tools are (re-)embedded and upserted. Disabled tools are removed
    from the index entirely."""
    embedding_repo = cast(ToolEmbeddingRepository, service_provider("tool_embedding_repo"))
    embedding_provider = cast(LocalEmbeddingProvider, service_provider("embedding_provider"))

    if tool.enabled:
        embedding = await embedding_provider.embed(tool.description)
        await embedding_repo.upsert(
            names=[tool.name],
            embeddings=[embedding],
            descriptions=[tool.description],
            enabled_flags=[True],
        )
    else:
        await embedding_repo.delete(tool.name)


async def _reembed_group_tools(tools: list) -> None:
    """Re-embed a list of tools into LanceDB."""
    from moira.service_setup import service_provider as sp

    catalog = sp("tool_catalog")
    discovery = sp("tool_discovery")
    if not discovery or not catalog:
        return

    enabled = [t for t in tools if t.enabled]
    if not enabled:
        names = [t.name for t in tools]
        await discovery._embedding_repo.delete(names)
        return

    embedding_provider = discovery._embedding_provider
    descriptions = [t.description for t in enabled]
    embeddings = await embedding_provider.embed_batch(descriptions)
    await discovery._embedding_repo.upsert(
        names=[t.name for t in enabled],
        embeddings=embeddings,
        descriptions=descriptions,
        enabled_flags=[True] * len(enabled),
    )

    disabled = [t for t in tools if not t.enabled]
    if disabled:
        await discovery._embedding_repo.delete([t.name for t in disabled])


# --- Tool catalog endpoints ---


@router.get("/tools")
async def list_tools():
    """Return all registered tools and tool groups."""
    repo = _tool_repo()
    tools = await repo.get_all_tools()
    groups = await repo.get_all_groups()
    return {
        "tools": [t.to_dict() for t in tools],
        "groups": groups,
    }


@router.get("/tools/{name}")
async def get_tool(name: str):
    """Return a single tool by name."""
    repo = _tool_repo()
    tool = await repo.get_tool(name)
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    return tool.to_dict()


@router.get("/tools/{name}/spec")
async def get_tool_spec(name: str):
    """Return the tool's config schema from its implementation class.
    Used by the frontend to render a configuration form."""
    repo = _tool_repo()
    tool = await repo.get_tool(name)
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")

    if not tool.implementation:
        return {"config_schema": {}}

    from moira.tools.executor import ToolExecutor

    cls = ToolExecutor._resolve_implementation(tool.implementation)
    if cls is None:
        return {"config_schema": {}}

    return cls.get_spec()


@router.get("/tools/embeddings/search")
async def debug_embedding_search(q: str = "", top_k: int = 20):
    """Debug endpoint: run a semantic search against LanceDB and return
    raw results with distance scores. Used for inspecting tool discovery."""
    if not q:
        raise HTTPException(status_code=400, detail="q parameter is required")

    embedding_provider = cast(LocalEmbeddingProvider, service_provider("embedding_provider"))
    embedding_repo = cast(ToolEmbeddingRepository, service_provider("tool_embedding_repo"))

    if embedding_provider is None or embedding_repo is None:
        raise HTTPException(status_code=503, detail="Embedding services not available")

    query_embedding = await embedding_provider.embed(q)
    results = await embedding_repo.search_raw(query_embedding, top_k=top_k)
    return {"query": q, "results": results}


@router.patch("/tools/{name}")
async def update_tool(name: str, body: dict[str, Any]):
    """Update mutable fields on a tool. Built-in tools only allow `enabled`
    and `is_default` to be changed. Non-built-in tools allow all fields."""
    repo = _tool_repo()
    tool = await repo.get_tool(name)
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")

    allowed = (
        {
            "enabled",
            "is_default",
            "config",
            "invocation_cost",
            "call_limit_per_run",
            "call_limit_per_step",
        }
        if tool.built_in
        else {
            "description",
            "argument_schema",
            "config",
            "tags",
            "reliability",
            "is_default",
            "enabled",
            "group_name",
            "invocation_cost",
            "call_limit_per_run",
            "call_limit_per_step",
        }
    )
    updates = {k: v for k, v in body.items() if k in allowed}

    # Special handling: description=null resets to original_description
    if "description" in body and body["description"] is None:
        if tool.original_description:
            updates["description"] = tool.original_description

    if not updates:
        raise HTTPException(status_code=400, detail="No updatable fields provided")

    for k, v in updates.items():
        setattr(tool, k, v)
    await repo.save_tool(tool)

    catalog = service_provider("tool_catalog")
    if catalog:
        catalog.add(tool)

    if "enabled" in updates:
        await _sync_tool_to_index(tool)

    # Re-embed when description changes so LanceDB stays in sync
    if "description" in updates:
        await _sync_tool_to_index(tool)

    return tool.to_dict()


@router.patch("/tool-admin/bulk")
async def bulk_update_tools(body: dict[str, Any]):
    """Update mutable fields on multiple tools in a single request.

    Expects ``{"updates": [{"name": "...", "enabled": true}, ...]}``.
    Returns ``{"updated": [{"name": "...", ...}, ...]}``."""
    updates_list = body.get("updates")
    if not updates_list or not isinstance(updates_list, list):
        raise HTTPException(
            status_code=400,
            detail="'updates' must be a non-empty list of {name, ...fields} objects",
        )

    repo = _tool_repo()
    results: list[dict[str, Any]] = []

    for item in updates_list:
        name = item.get("name")
        if not name:
            continue

        tool = await repo.get_tool(name)
        if tool is None:
            continue

        allowed = (
            {
                "enabled",
                "is_default",
                "config",
                "invocation_cost",
                "call_limit_per_run",
                "call_limit_per_step",
            }
            if tool.built_in
            else {
                "description",
                "argument_schema",
                "config",
                "tags",
                "reliability",
                "is_default",
                "enabled",
                "group_name",
                "invocation_cost",
                "call_limit_per_run",
                "call_limit_per_step",
            }
        )
        fields = {k: v for k, v in item.items() if k in allowed}
        if not fields:
            continue

        for k, v in fields.items():
            setattr(tool, k, v)
        await repo.save_tool(tool)

        catalog = service_provider("tool_catalog")
        if catalog:
            catalog.add(tool)

        if "enabled" in fields:
            await _sync_tool_to_index(tool)

        results.append(tool.to_dict())

    return {"updated": results}


# --- Tool Group endpoints ---

PROTECTED_GROUPS = {"standard"}


def _check_protected_group(name: str) -> None:
    """Reject operations on protected groups (e.g. 'standard').
    These groups contain built-in tools and must not be renamed,
    deleted, or have their tools bulk-modified."""
    if name in PROTECTED_GROUPS:
        raise HTTPException(
            status_code=403,
            detail=f"Group '{name}' is protected and cannot be modified",
        )


@router.patch("/tool-admin/groups/{name}")
async def rename_tool_group(name: str, body: dict[str, Any]):
    """Rename a tool group. If the new name maps to an existing group slug,
    returns 409 with the conflicting group name so the frontend can prompt
    for merge confirmation."""
    _check_protected_group(name)
    from moira.services.tool_ingestion.spec_parser import derive_group_slug

    new_display_name = body.get("display_name")
    if not new_display_name:
        raise HTTPException(status_code=400, detail="display_name is required")

    new_slug = derive_group_slug(new_display_name)

    if new_slug in PROTECTED_GROUPS:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot rename group to reserved name '{new_slug}'",
        )

    repo = _tool_repo()
    all_groups = await repo.get_all_groups()
    group_names = {g["name"] for g in all_groups}

    if new_slug != name and new_slug in group_names:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Renaming would merge with existing group '{new_slug}'",
                "conflicting_group": new_slug,
            },
        )

    tools = await repo.get_all_tools()
    group_tools = [t for t in tools if t.group_name == name]
    if not group_tools:
        raise HTTPException(status_code=404, detail="Group not found")

    # Update group row
    await repo.save_group({"name": new_slug, "display_name": new_display_name})

    # Update each tool's group_name and re-save
    for t in group_tools:
        t.group_name = new_slug
        await repo.save_tool(t)

    # Remove old group row if slug changed
    if new_slug != name:
        await repo.delete_group(name)

    # Update in-memory catalog
    catalog = service_provider("tool_catalog")
    if catalog:
        for t in group_tools:
            catalog.remove(t.name)
            t.group_name = new_slug
            catalog.add(t)

    # Re-embed tools with updated names
    await _reembed_group_tools(group_tools)

    return {"name": new_slug, "display_name": new_display_name}


@router.post("/tool-admin/groups/{name}/toggle")
async def toggle_tool_group(name: str, body: dict[str, Any]):
    """Enable or disable all tools in a group."""
    _check_protected_group(name)
    enabled = body.get("enabled")
    if enabled is None:
        raise HTTPException(status_code=400, detail="enabled is required")

    repo = _tool_repo()
    tools = await repo.get_all_tools()
    group_tools = [t for t in tools if t.group_name == name]
    if not group_tools:
        raise HTTPException(status_code=404, detail="Group not found")

    for t in group_tools:
        t.enabled = enabled
        await repo.save_tool(t)

    # Update in-memory catalog
    catalog = service_provider("tool_catalog")
    if catalog:
        for t in group_tools:
            catalog.remove(t.name)
            catalog.add(t)

    await _reembed_group_tools(group_tools)

    return {"toggled": len(group_tools), "enabled": enabled}


@router.delete("/tool-admin/groups/{name}")
async def delete_tool_group(name: str):
    """Delete all tools in a group and the group itself. Removes from
    SQLite, in-memory catalog, executor, and LanceDB embeddings."""
    _check_protected_group(name)
    repo = _tool_repo()
    tools = await repo.get_all_tools()
    group_tools = [t for t in tools if t.group_name == name]
    if not group_tools:
        raise HTTPException(status_code=404, detail="Group not found")

    deleted_names = [t.name for t in group_tools]

    for t in group_tools:
        await repo.delete_tool(t.name)

    await repo.delete_group(name)

    # Remove from runtime
    catalog = service_provider("tool_catalog")
    executor = service_provider("tool_executor")
    discovery = service_provider("tool_discovery")

    if catalog:
        for n in deleted_names:
            catalog.remove(n)
    if executor:
        for n in deleted_names:
            executor._tool_instances.pop(n, None)
    if discovery:
        await discovery._embedding_repo.delete(deleted_names)

    return {"deleted": deleted_names}


@router.delete("/tool-admin/tools/{name}")
async def delete_tool(name: str):
    """Delete a single tool by name. Removes from SQLite, in-memory catalog,
    executor, and LanceDB embeddings. Tools in protected groups (e.g. 'standard')
    cannot be deleted."""
    repo = _tool_repo()
    tool = await repo.get_tool(name)
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")

    _check_protected_group(tool.group_name)

    await repo.delete_tool(name)

    catalog = service_provider("tool_catalog")
    executor = service_provider("tool_executor")
    discovery = service_provider("tool_discovery")

    if catalog:
        catalog.remove(name)
    if executor:
        executor._tool_instances.pop(name, None)
    if discovery:
        await discovery._embedding_repo.delete([name])

    return {"deleted": name}


# --- Tool Ingestion endpoints ---


@router.post("/tools/ingest/start")
async def ingest_start(body: dict[str, Any]):
    """Start the ingestion wizard. Parses the spec and returns a preview
    of operations without creating any tools."""
    base_url = body.get("url")
    spec_url = body.get("spec_url")
    spec_content = body.get("spec_content")
    group_name = body.get("group_name")

    if not any([base_url, spec_url, spec_content]):
        raise HTTPException(
            status_code=400,
            detail="At least one of url, spec_url, or spec_content is required",
        )

    prov = _provisioner()
    try:
        preview = await prov.preview(
            base_url=base_url,
            spec_url=spec_url,
            spec_content=spec_content,
            group_name_override=group_name,
        )
    except Exception as e:
        from moira.services.tool_ingestion.spec_resolver import SpecResolutionError

        if isinstance(e, (SpecResolutionError, ValueError)):
            raise HTTPException(status_code=422, detail=str(e))
        logger.error("Ingest preview failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to parse spec: {e}")

    return preview


@router.post("/tools/ingest/commit")
async def ingest_commit(body: dict[str, Any]):
    """Commit selected operations as tools. Creates ToolDefinition rows,
    ApiSource row, and wires tools into the runtime."""
    source_id = body.get("source_id")
    selected_operations = body.get("selected_operations", [])
    operations_data = body.get("operations", [])
    server_url = body.get("server_url")
    group_name = body.get("group_name")
    base_url = body.get("base_url")
    spec_url = body.get("spec_url")
    spec_format = body.get("spec_format")
    auth_type = body.get("auth_type")
    is_default = body.get("is_default", False)

    if not source_id:
        raise HTTPException(status_code=400, detail="source_id is required")
    if not selected_operations:
        raise HTTPException(status_code=400, detail="selected_operations is required")
    if not operations_data:
        raise HTTPException(status_code=400, detail="operations is required")
    if not server_url:
        raise HTTPException(status_code=400, detail="server_url is required")
    if not group_name:
        raise HTTPException(status_code=400, detail="group_name is required")

    prov = _provisioner()
    try:
        result = await prov.commit(
            source_id=source_id,
            base_url=base_url or server_url,
            spec_url=spec_url,
            spec_format=spec_format or "openapi_3_0",
            group_name=group_name,
            auth_type=auth_type,
            selected_operations=selected_operations,
            operations_data=operations_data,
            server_url=server_url,
            is_default=is_default,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Ingest commit failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Provisioning failed: {e}")

    return {
        "succeeded": result.succeeded,
        "failed": result.failed,
        "disabled": result.disabled,
        "total": result.total,
    }


@router.get("/tools/ingest/sources")
async def list_sources():
    """List all ingested API sources."""
    prov = _provisioner()
    sources = await prov.list_sources()
    return {
        "sources": [
            {
                "id": s.id,
                "name": s.name,
                "base_url": s.base_url,
                "spec_url": s.spec_url,
                "spec_format": s.spec_format,
                "auth_type": s.auth_type,
                "group_name": s.group_name,
                "tool_count": s.tool_count,
                "enabled": s.enabled,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
            for s in sources
        ]
    }


@router.get("/tools/ingest/sources/{source_id}")
async def get_source(source_id: str):
    """Get a single API source by ID."""
    prov = _provisioner()
    source = await prov.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="API source not found")
    return {
        "id": source.id,
        "name": source.name,
        "base_url": source.base_url,
        "spec_url": source.spec_url,
        "spec_format": source.spec_format,
        "auth_type": source.auth_type,
        "group_name": source.group_name,
        "tool_count": source.tool_count,
        "enabled": source.enabled,
        "created_at": source.created_at,
        "updated_at": source.updated_at,
    }


@router.delete("/tools/ingest/sources/{source_id}")
async def delete_source(source_id: str):
    """Delete an API source and all its tools."""
    prov = _provisioner()
    try:
        deleted = await prov.delete_source(source_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Source deletion failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Deletion failed: {e}")

    return {"status": "deleted", "deleted_tools": deleted}
