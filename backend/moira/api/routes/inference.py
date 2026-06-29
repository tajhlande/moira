"""Inference provider and model management endpoints.

Owns the API surface for configuring inference providers (adding, updating,
removing OpenAI-compatible endpoints), discovering models via GET /models,
and assigning models to roles (intelligence/task).

Providers are identified by kebab-case slugs in URL paths. The POST body
takes a display_name (human-readable) from which the slug is derived.

Replaces the old GET /api/models and PUT /api/models endpoints that lived
in conversations.py."""

import logging
from typing import Any, cast

from fastapi import APIRouter, HTTPException

from moira.inference.registry import ModelRegistry
from moira.persistence.interfaces import (
    DEFAULT_USER_ID,
    InferenceProviderRepository,
    ModelPreferencesRepository,
)
from moira.service_setup import service_provider

logger = logging.getLogger(__name__)

router = APIRouter()


def _registry() -> ModelRegistry:
    return cast(ModelRegistry, service_provider("model_registry"))


def _provider_repo() -> InferenceProviderRepository:
    return cast(
        InferenceProviderRepository,
        service_provider("inference_provider_repository"),
    )


def _prefs() -> ModelPreferencesRepository:
    return cast(ModelPreferencesRepository, service_provider("model_preferences_repository"))


@router.get("/inference/providers")
async def list_providers():
    """List all configured inference providers (metadata only, no API keys)."""
    repo = _provider_repo()
    providers = await repo.get_all_providers()
    registry = _registry()
    errors = registry.get_provider_errors()
    return {
        "providers": [
            {
                "slug": p.slug,
                "display_name": p.display_name,
                "base_url": p.base_url,
                "provider_type": p.provider_type,
                "credential_name": p.credential_name,
                "last_error": errors.get(p.slug, ""),
                "created_at": p.created_at,
                "updated_at": p.updated_at,
            }
            for p in providers
        ]
    }


@router.post("/inference/providers/validate")
async def validate_provider(body: dict[str, Any]):
    """Validate that a provider URL is reachable and returns compatible models.

    Creates a temporary InferenceClient, calls GET /models, and returns
    the result. Does NOT persist anything to the database. Used by the
    frontend to validate before saving.

    When ``slug`` is provided and ``api_key`` is empty, resolves the
    existing provider's credential (edit mode — user left key blank).
    """
    base_url = body.get("base_url", "").strip()
    api_key = body.get("api_key", "")
    provider_type = body.get("provider_type", "completions")
    slug = body.get("slug", "")

    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required")

    registry = _registry()
    valid, error, model_count = await registry.validate_connection(
        base_url=base_url,
        api_key=api_key,
        provider_type=provider_type,
        slug=slug,
    )
    if valid:
        return {"valid": True, "model_count": model_count}
    return {"valid": False, "error": error}


@router.post("/inference/providers", status_code=201)
async def create_provider(body: dict[str, Any]):
    """Add a new inference provider and trigger model discovery.

    Takes a display_name (human-readable) from which a kebab-case slug is
    auto-generated. Stores the API key as an encrypted credential (when
    non-empty), persists the provider, creates and starts an InferenceClient,
    and discovers available models via GET /models.
    """
    display_name = body.get("display_name", "").strip()
    base_url = body.get("base_url", "").strip()
    api_key = body.get("api_key", "")
    provider_type = body.get("provider_type", "completions")

    if not display_name:
        raise HTTPException(status_code=400, detail="display_name is required")
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required")

    registry = _registry()
    slug = await registry.add_provider(
        display_name=display_name,
        base_url=base_url,
        api_key=api_key,
        provider_type=provider_type,
    )
    logger.info("Created inference provider '%s' (slug='%s') at %s", display_name, slug, base_url)
    return {"slug": slug, "display_name": display_name, "base_url": base_url}


@router.put("/inference/providers/{slug}")
async def update_provider(slug: str, body: dict[str, Any]):
    """Update an existing provider's connection details.

    The slug is immutable. display_name is editable and updates the
    human-readable label without changing the slug. If base_url,
    provider_type, or api_key changed, the old client is stopped and a
    new one started. Model discovery re-runs.
    """
    base_url = body.get("base_url", "").strip()
    api_key = body.get("api_key", "")
    provider_type = body.get("provider_type", "completions")
    display_name = body.get("display_name", "").strip() or None

    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required")

    repo = _provider_repo()
    existing = await repo.get_provider(slug)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Provider '{slug}' not found")

    registry = _registry()
    await registry.update_provider(
        slug=slug,
        base_url=base_url,
        api_key=api_key,
        provider_type=provider_type,
        display_name=display_name,
    )
    logger.info("Updated inference provider '%s'", slug)
    return {"slug": slug, "base_url": base_url}


@router.delete("/inference/providers/{slug}")
async def delete_provider(slug: str):
    """Remove a provider, its discovered models, and stored credential."""
    registry = _registry()
    deleted = await registry.remove_provider(slug)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Provider '{slug}' not found")
    logger.info("Deleted inference provider '%s'", slug)
    return {"status": "deleted"}


@router.post("/inference/providers/{slug}/refresh")
async def refresh_provider_models(slug: str):
    """Re-run model discovery for a single provider."""
    registry = _registry()
    if slug not in registry._clients:
        raise HTTPException(status_code=404, detail=f"Provider '{slug}' not found")
    models = await registry.refresh_models(slug)
    errors = registry.get_provider_errors()
    return {
        "provider": slug,
        "discovered_count": len(models),
        "models": [{"id": m.id, "endpoint": m.source_endpoint} for m in models],
        "error": errors.get(slug, ""),
    }


@router.post("/inference/refresh")
async def refresh_all_models():
    """Re-run model discovery for all providers."""
    registry = _registry()
    models = await registry.refresh_models()
    errors = registry.get_provider_errors()
    return {
        "discovered_count": len(models),
        "models": [{"id": m.id, "endpoint": m.source_endpoint} for m in models],
        "errors": errors,
    }


@router.get("/inference/models")
async def list_models():
    """List all discovered models grouped by provider, with current
    role assignments."""
    repo = _provider_repo()
    all_models = await repo.get_all_models()
    prefs = await _prefs().get_preferences(DEFAULT_USER_ID)

    return {
        "models": [
            {
                "id": m.model_id,
                "provider": m.provider_slug,
                "native_tool_calling": m.native_tool_calling,
            }
            for m in all_models
        ],
        "assignments": {
            "intelligence": {
                "endpoint": prefs.intelligence_endpoint,
                "model": prefs.intelligence_model,
            },
            "task": {
                "endpoint": prefs.task_endpoint,
                "model": prefs.task_model,
            },
        },
    }


@router.put("/inference/models/assign")
async def set_model_assignments(body: dict[str, Any]):
    """Assign models to roles (intelligence/task).

    Each assignment is a {endpoint, model} pair where endpoint is the
    provider slug and model is the model ID discovered from that provider.
    """
    intel = body.get("intelligence", {})
    task = body.get("task", {})
    logger.info("Setting model assignments: intelligence=%s, task=%s", intel, task)
    registry = _registry()
    await registry.set_assignments(
        intelligence_endpoint=intel.get("endpoint", ""),
        intelligence_model=intel.get("model", ""),
        task_endpoint=task.get("endpoint", ""),
        task_model=task.get("model", ""),
    )
    return {"intelligence": intel, "task": task}


@router.patch("/inference/models/{provider_slug}/{model_id:path}")
async def set_model_capability(provider_slug: str, model_id: str, body: dict[str, Any]):
    """Toggle the native_tool_calling flag for a specific model."""
    native_tool_calling = body.get("native_tool_calling")
    if native_tool_calling is None or not isinstance(native_tool_calling, bool):
        raise HTTPException(status_code=400, detail="native_tool_calling (bool) is required")

    repo = _provider_repo()
    models = await repo.get_models(provider_slug)
    if not any(m.model_id == model_id for m in models):
        raise HTTPException(
            status_code=404,
            detail=f"Model '{model_id}' not found on provider '{provider_slug}'",
        )

    registry = _registry()
    await registry.set_model_capability(provider_slug, model_id, native_tool_calling)
    return {
        "provider": provider_slug,
        "model": model_id,
        "native_tool_calling": native_tool_calling,
    }
