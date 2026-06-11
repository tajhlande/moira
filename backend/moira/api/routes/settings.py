"""Settings and credentials endpoints.

Owns the API surface for the Settings tab in the frontend: dynamic
setting definitions, scoped setting values (system/conversation), and
credential management. Settings are resolved once at async boundaries
and enriched here with definition metadata for the UI."""

import logging
from typing import Any, cast

import jsonschema
from fastapi import APIRouter, HTTPException, Request

from moira.persistence.interfaces import (
    SCOPE_SYSTEM,
    SYSTEM_SCOPE_ID,
    SettingEntry,
)
from moira.service_setup import service_provider

logger = logging.getLogger(__name__)

router = APIRouter()


def _settings_service():
    from moira.services.settings.settings_service import SettingsService

    return cast(SettingsService, service_provider("settings_service"))


def _credential_service():
    from moira.services.credentials.credential_service import CredentialService

    return cast(CredentialService, service_provider("credential_service"))


def _resolve_scope(request_or_body: Request | dict, is_request: bool = True) -> tuple[str, str]:
    """Extract scope and scope_id from a request or body dict.

    Defaults to system scope with the system scope_id. Non-system scopes
    must provide scope_id or a 400 is raised."""
    if is_request:
        scope = request_or_body.query_params.get("scope", SCOPE_SYSTEM)
        raw_sid = request_or_body.query_params.get("scope_id")
    else:
        scope = request_or_body.get("scope", SCOPE_SYSTEM)
        raw_sid = request_or_body.get("scope_id")
    scope_id = raw_sid or (SYSTEM_SCOPE_ID if scope == SCOPE_SYSTEM else None)
    if not scope_id:
        raise HTTPException(
            status_code=400,
            detail=f"scope_id is required when scope is not '{SCOPE_SYSTEM}'",
        )
    return scope, scope_id


def _enriched_setting(key: str, value: str, scope: str, scope_id: str) -> dict[str, Any]:
    """Combine a raw DB value with its definition metadata for API responses.

    The service layer returns plain SettingEntry objects (key/value/scope/scope_id).
    The API contract includes type, label, description, group, and constraints from
    the definitions registry. Enrichment happens here in the router rather than in
    the service so that internal callers (budget.py, run manager) can work with
    lightweight entries without carrying UI metadata through the backend."""
    svc = _settings_service()
    defn = svc.get_definition(key)
    return {
        "key": key,
        "value": value,
        "type": defn.type if defn else "string",
        "label": defn.label if defn else key,
        "description": defn.description if defn else "",
        "group": defn.group if defn else "",
        "constraints": defn.constraints if defn else {},
        "scope": scope,
        "scope_id": scope_id,
    }


# --- Settings endpoints ---


@router.get("/settings/definitions")
async def get_setting_definitions():
    """Return all known setting definitions for dynamic UI rendering.

    Serializes each SettingDefinition dataclass into a plain dict because
    dataclasses are not JSON-serializable by FastAPI's response handler and
    the frontend expects a flat JSON shape, not a Python object."""
    svc = _settings_service()
    definitions = svc.get_all_definitions()
    return {
        "definitions": [
            {
                "key": d.key,
                "type": d.type,
                "default": d.default,
                "label": d.label,
                "description": d.description,
                "group": d.group,
                "constraints": d.constraints,
            }
            for d in definitions
        ]
    }


@router.get("/settings/{key}")
async def get_setting(key: str, request: Request):
    from moira.services.settings.definitions import SETTING_DEFINITIONS

    if key not in SETTING_DEFINITIONS:
        raise HTTPException(status_code=404, detail=f"Unknown setting: {key}")

    scope, scope_id = _resolve_scope(request)

    svc = _settings_service()
    resolved = await svc.get(key, scopes=[(scope, scope_id)])
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"Setting not found: {key}")

    defn = svc.get_definition(key)
    return {
        "key": resolved.key,
        "value": resolved.value,
        "type": resolved.type,
        "label": defn.label if defn else key,
        "description": defn.description if defn else "",
        "group": defn.group if defn else "",
        "constraints": defn.constraints if defn else {},
        "scope": resolved.scope,
        "scope_id": resolved.scope_id,
    }


@router.put("/settings/{key}")
async def set_setting(key: str, body: dict[str, Any]):
    from moira.services.settings.settings_service import (
        InvalidSettingValueError,
        UnknownSettingError,
    )

    value = body.get("value")
    if value is None:
        raise HTTPException(status_code=400, detail="value is required")
    scope, scope_id = _resolve_scope(body, is_request=False)

    svc = _settings_service()
    try:
        await svc.set(key, str(value), scope=scope, scope_id=scope_id)
    except UnknownSettingError:
        raise HTTPException(status_code=404, detail=f"Unknown setting: {key}")
    except (InvalidSettingValueError, Exception) as e:
        if isinstance(e, jsonschema.ValidationError):
            raise HTTPException(status_code=422, detail=str(e.message))
        raise HTTPException(status_code=422, detail=str(e))

    return _enriched_setting(key, str(value), scope, scope_id)


@router.get("/settings")
async def list_settings(
    request: Request,
    prefix: str | None = None,
):
    scope, scope_id = _resolve_scope(request)

    svc = _settings_service()
    pfx = prefix or ""
    entries = await svc.get_prefix(pfx, scope=scope, scope_id=scope_id)
    return {"settings": [_enriched_setting(e.key, e.value, e.scope, e.scope_id) for e in entries]}


@router.put("/settings")
async def batch_set_settings(body: dict[str, Any]):
    from moira.services.settings.settings_service import (
        InvalidSettingValueError,
        UnknownSettingError,
    )

    scope, scope_id = _resolve_scope(body, is_request=False)
    raw_settings = body.get("settings", [])
    if not isinstance(raw_settings, list):
        raise HTTPException(status_code=400, detail="settings must be an array")

    entries = [
        SettingEntry(key=s["key"], value=str(s["value"]), scope=scope, scope_id=scope_id)
        for s in raw_settings
        if "key" in s and "value" in s
    ]

    svc = _settings_service()
    try:
        await svc.set_batch(entries)
    except UnknownSettingError as e:
        raise HTTPException(status_code=404, detail=f"Unknown setting: {e}")
    except (InvalidSettingValueError, Exception) as e:
        if isinstance(e, jsonschema.ValidationError):
            raise HTTPException(status_code=422, detail=str(e.message))
        raise HTTPException(status_code=422, detail=str(e))

    return {"settings": [_enriched_setting(e.key, e.value, e.scope, e.scope_id) for e in entries]}


@router.delete("/settings")
async def reset_settings(
    request: Request,
    keys: str | None = None,
):
    scope, scope_id = _resolve_scope(request)

    svc = _settings_service()
    key_list = keys.split(",") if keys else None
    await svc.reset_defaults(keys=key_list, scope=scope, scope_id=scope_id)

    if key_list:
        entries = await svc.get_prefix("", scope=scope, scope_id=scope_id)
        return {
            "settings": [
                _enriched_setting(e.key, e.value, e.scope, e.scope_id)
                for e in entries
                if e.key in key_list
            ]
        }

    entries = await svc.get_prefix("", scope=scope, scope_id=scope_id)
    return {"settings": [_enriched_setting(e.key, e.value, e.scope, e.scope_id) for e in entries]}


# --- Credentials endpoints ---


@router.get("/credentials")
async def list_credentials(owner: str | None = None):
    svc = _credential_service()
    creds = await svc.list_credentials(owner=owner)
    return {"credentials": [c.to_dict() for c in creds]}


@router.post("/credentials", status_code=201)
async def create_credential(body: dict[str, Any]):
    name = body.get("name")
    value = body.get("value")
    owner = body.get("owner")
    if not name or not isinstance(name, str):
        raise HTTPException(status_code=400, detail="name is required")
    if not value or not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="value is required")
    svc = _credential_service()
    info = await svc.store_credential(name=name, value=value, owner=owner)
    return info.to_dict()


@router.get("/credentials/{name}")
async def get_credential(name: str, owner: str | None = None):
    svc = _credential_service()
    creds = await svc.list_credentials(owner=owner)
    for c in creds:
        if c.name == name:
            return c.to_dict()
    raise HTTPException(status_code=404, detail="Credential not found")


@router.delete("/credentials/{name}")
async def delete_credential(name: str, owner: str | None = None):
    svc = _credential_service()
    deleted = await svc.delete_credential(name=name, owner=owner)
    if not deleted:
        raise HTTPException(status_code=404, detail="Credential not found")
    return {"status": "deleted"}
