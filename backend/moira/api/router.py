import logging
from typing import Any, cast

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Request

from moira.embeddings.local import LocalEmbeddingProvider
from moira.inference.registry import ModelRegistry
from moira.persistence.interfaces import (
    DEFAULT_USER_ID,
    SCOPE_SYSTEM,
    SYSTEM_SCOPE_ID,
    ConversationRepository,
    ModelPreferencesRepository,
    SettingEntry,
    WorkflowRun,
    WorkflowStep,
)
from moira.persistence.lancedb.tool_embeddings import ToolEmbeddingRepository
from moira.service_setup import service_provider

logger = logging.getLogger(__name__)

TITLE_GENERATION_SYSTEM = (
    "You generate short conversation titles. "
    "Given the user's message, produce a concise title that captures "
    "the topic. Output ONLY the title text with no quotes, no punctuation at "
    "the end, and no explanation."
)

router = APIRouter()


def _conversations() -> ConversationRepository:
    return cast(ConversationRepository, service_provider("conversation_repository"))


def _prefs() -> ModelPreferencesRepository:
    return cast(ModelPreferencesRepository, service_provider("model_preferences_repository"))


def _registry() -> ModelRegistry:
    return cast(ModelRegistry, service_provider("model_registry"))


def _steps_repo():
    from moira.persistence.interfaces import WorkflowStepRepository

    return cast(WorkflowStepRepository, service_provider("workflow_step_repository"))


def _step_tool_call_count(step: WorkflowStep) -> int:
    if step.tool_call_count:
        return step.tool_call_count
    detail = step.detail or {}
    if not isinstance(detail, dict):
        return 0
    tool_results = detail.get("tool_results")
    return len(tool_results) if isinstance(tool_results, list) else 0


def _step_summary(step: WorkflowStep, ordinal: int = 1) -> dict[str, Any]:
    return {
        "id": str(ordinal),
        "detail_run_id": step.workflow_run_id,
        "node": step.node_name,
        "label": step.label,
        "status": step.status,
        "cost": step.cost,
        "budget_remaining": step.budget_remaining,
        "elapsed_ms": step.elapsed_ms,
        "started_at": step.started_at,
        "error": step.error if step.error else None,
        "tool_call_count": _step_tool_call_count(step),
        "step_version": step.step_version or 1,
        "has_detail": bool(step.detail),
    }


def _verification_attempts(steps: list[WorkflowStep]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    attempt_num = 0
    for step in steps:
        detail = step.detail if isinstance(step.detail, dict) else None
        if step.node_name != "verification" or not detail:
            continue
        structured = detail.get("structured_output")
        if not structured:
            continue
        attempt_num += 1
        attempts.append({"report": structured, "attempt": attempt_num})
    return attempts


def _tool_executions_from_steps(steps: list[WorkflowStep]) -> list[dict[str, Any]]:
    """Extract flattened tool execution entries from timeline step detail.

    Conversation responses derive tool executions from the rendered step
    timeline, while analytics are recorded separately and are not affected by
    this projection.
    """
    tool_executions: list[dict[str, Any]] = []
    for step in steps:
        detail = step.detail if isinstance(step.detail, dict) else None
        if not detail:
            continue
        tool_results = detail.get("tool_results")
        if not isinstance(tool_results, list):
            continue
        for result in tool_results:
            if isinstance(result, dict):
                tool_executions.append(result)
    return tool_executions


def _coalesced_run_snapshot(
    attempts: list[WorkflowRun],
    steps_by_run_id: dict[str, list[WorkflowStep]],
) -> dict[str, Any]:
    """Build one logical run snapshot from one or more persisted attempts.

    Resume creates a new workflow_runs row per attempt, but the API contract
    exposes one logical run per user_message_id with a stacked attempt
    timeline.
    """
    latest = attempts[-1]
    merged_steps: list[WorkflowStep] = []

    for attempt in attempts:
        attempt_steps = steps_by_run_id.get(attempt.id, [])
        merged_steps.extend(attempt_steps)

    merged_tool_executions = _tool_executions_from_steps(merged_steps)
    attempt_summaries = [
        {
            "run_id": attempt.id,
            "status": attempt.status,
            "started_at": attempt.started_at,
            "completed_at": attempt.completed_at,
            "updated_at": attempt.updated_at,
            "state_version": attempt.state_version,
        }
        for attempt in attempts
    ]

    # Compute ordinal step IDs per run so they match the SSE snapshot
    # format (1-based position within each workflow_run).
    ordinal_counters: dict[str, int] = {}
    step_summaries: list[dict[str, Any]] = []
    for step in merged_steps:
        rid = step.workflow_run_id
        ordinal_counters[rid] = ordinal_counters.get(rid, 0) + 1
        step_summaries.append(_step_summary(step, ordinal_counters[rid]))

    return {
        "id": latest.id,
        "conversation_id": latest.conversation_id,
        "user_message_id": latest.user_message_id,
        "status": latest.status,
        "budget_limit": latest.budget_limit,
        "budget_consumed": latest.budget_consumed,
        "total_elapsed_ms": latest.total_elapsed_ms,
        "error": latest.error,
        "report": latest.report,
        "execution_steps": step_summaries,
        "attempts": attempt_summaries,
        "tool_executions": merged_tool_executions,
        "verification_attempts": _verification_attempts(merged_steps),
        "state_version": latest.state_version,
        "started_at": latest.started_at,
        "completed_at": latest.completed_at,
        "updated_at": latest.updated_at,
        "input_tokens": sum(s.input_tokens or 0 for s in merged_steps),
        "output_tokens": sum(s.output_tokens or 0 for s in merged_steps),
        "thinking_tokens": sum(s.thinking_tokens or 0 for s in merged_steps),
    }


def _logical_runs_snapshot(
    runs: list[WorkflowRun],
    steps_by_run_id: dict[str, list[WorkflowStep]],
) -> list[dict[str, Any]]:
    runs_by_message: dict[int, list[WorkflowRun]] = {}
    for run in runs:
        runs_by_message.setdefault(run.user_message_id, []).append(run)

    logical_runs: list[dict[str, Any]] = []
    for attempts in runs_by_message.values():
        attempts.sort(key=lambda run: (run.started_at, run.id))
        logical_runs.append(_coalesced_run_snapshot(attempts, steps_by_run_id))

    return logical_runs


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/conversations", response_model=dict)
async def create_conversation(
    conversations: ConversationRepository = Depends(_conversations),
):
    logger.info("Creating new conversation")
    conversation = await conversations.create_conversation(
        user_id=DEFAULT_USER_ID, title="New Conversation"
    )
    logger.info("Created conversation %s", conversation.id)
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at,
    }


@router.get("/conversations", response_model=list)
async def list_conversations(
    conversations: ConversationRepository = Depends(_conversations),
):
    result = await conversations.list_conversations(DEFAULT_USER_ID)
    return [{"id": c.id, "title": c.title, "created_at": c.created_at} for c in result]


@router.get("/conversations/{conversation_id}", response_model=dict)
async def get_conversation(
    conversation_id: str,
    conversations: ConversationRepository = Depends(_conversations),
):
    conversation = await conversations.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await conversations.get_messages(conversation_id)
    runs = await conversations.get_workflow_runs(conversation_id)

    step_repo = _steps_repo()
    steps_by_run_id: dict[str, list[WorkflowStep]] = {}
    for run in runs:
        steps_by_run_id[run.id] = await step_repo.get_steps_for_run(run.id)

    run_responses = _logical_runs_snapshot(runs, steps_by_run_id)

    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at,
            }
            for m in messages
            if m.role in ("user", "assistant")
        ],
        "runs": run_responses,
    }


@router.get("/runs/{run_id}/steps/{step_id}/detail", response_model=dict)
async def get_run_step_detail(run_id: str, step_id: str):
    """Return the full detail payload for a single workflow step.

    Used by the frontend to lazily load expanded step details while the
    conversation API and run snapshots stay lightweight.

    ``step_id`` is the 1-based ordinal position of the step within the
    run (matching the step summary ``id`` field from both SSE snapshots
    and conversation API responses)."""
    step_repo = _steps_repo()
    steps = await step_repo.get_steps_for_run(run_id)

    try:
        ordinal = int(step_id)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"step_id must be an integer ordinal, got '{step_id}'",
        )

    if ordinal < 1 or ordinal > len(steps):
        raise HTTPException(status_code=404, detail="Step not found")

    target = steps[ordinal - 1]

    return {
        "run_id": run_id,
        "step_id": target.id,
        "step_version": target.step_version or 1,
        "has_detail": bool(target.detail),
        "detail": target.detail or {},
    }


@router.patch("/conversations/{conversation_id}", response_model=dict)
async def update_conversation(
    conversation_id: str,
    body: dict[str, Any],
    conversations: ConversationRepository = Depends(_conversations),
):
    title = body.get("title")
    if not title or not isinstance(title, str) or not title.strip():
        raise HTTPException(status_code=400, detail="title is required")
    conversation = await conversations.update_conversation(conversation_id, title.strip())
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    logger.info("Updated conversation %s title to '%s'", conversation_id, conversation.title)
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at,
    }


@router.get("/models", response_model=dict)
async def list_models():
    registry = _registry()
    models = registry.get_available_models()
    prefs = await _prefs().get_preferences(DEFAULT_USER_ID)
    return {
        "models": [
            {"id": m.id, "owned_by": m.owned_by, "endpoint": m.source_endpoint} for m in models
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


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    conversations: ConversationRepository = Depends(_conversations),
):
    deleted = await conversations.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    logger.info("Deleted conversation %s", conversation_id)
    return {"status": "deleted"}


@router.put("/models", response_model=dict)
async def set_model_assignments(body: dict[str, Any]):
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


@router.post("/conversations/{conversation_id}/generate-title")
async def generate_conversation_title(
    conversation_id: str,
    conversations: ConversationRepository = Depends(_conversations),
):
    """Use the task model to generate a short title from conversation messages."""
    conversation = await conversations.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await conversations.get_messages(conversation_id)
    user_messages = [m for m in messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No user messages in conversation")

    # Build context from all user messages so the title reflects the full
    # conversation, not just a truncated snippet that may miss the real topic.
    parts = [f"Message {i + 1}: {m.content}" for i, m in enumerate(user_messages)]
    context = "\n\n".join(parts)

    registry = _registry()
    try:
        resolved = await registry.resolve("task")
        raw = await resolved.client.chat_completion(
            model=resolved.model_id,
            messages=[
                {"role": "system", "content": TITLE_GENERATION_SYSTEM},
                {"role": "user", "content": context},
            ],
            temperature=0.5,
            max_tokens=30,
        )
    except (ValueError, Exception) as e:
        logger.warning("Title generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Title generation failed: {e}")

    title = raw.content.strip().strip('"').strip("'")[:100]
    if not title:
        raise HTTPException(status_code=502, detail="Model returned empty title")

    updated = await conversations.update_conversation(conversation_id, title)
    logger.info("Generated title for conversation %s: '%s'", conversation_id, title)
    assert updated
    return {
        "id": updated.id,
        "title": updated.title,
        "created_at": updated.created_at,
    }


def _tool_repo():
    from moira.persistence.interfaces import ToolRepository

    return cast(ToolRepository, service_provider("tool_repository"))


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


@router.patch("/tools/{name}")
async def update_tool(name: str, body: dict[str, Any]):
    """Update mutable fields on a tool. Built-in tools only allow `enabled`
    and `is_default` to be changed. Non-built-in tools allow all fields."""
    repo = _tool_repo()
    tool = await repo.get_tool(name)
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")

    allowed = (
        {"enabled", "is_default", "config"}
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
        }
    )
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="No updatable fields provided")

    for k, v in updates.items():
        setattr(tool, k, v)
    await repo.save_tool(tool)

    if "enabled" in updates:
        await _sync_tool_to_index(tool)

    return tool.to_dict()


async def _sync_tool_to_index(tool) -> None:
    """Update the LanceDB vector index when a tool's enabled state changes.
    Enabled tools are (re-)embedded and upserted. Disabled tools are removed
    from the index entirely."""
    from moira.service_setup import service_provider

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


def _credential_service():
    from moira.services.credentials.credential_service import CredentialService

    return cast(CredentialService, service_provider("credential_service"))


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


def _metrics_repo():
    from moira.tools.metrics import ToolMetricsRepository

    return cast(ToolMetricsRepository, service_provider("tool_metrics_repository"))


def _settings_service():
    from moira.services.settings.settings_service import SettingsService

    return cast(SettingsService, service_provider("settings_service"))


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
    return {
        "settings": [_enriched_setting(e.key, e.value, e.scope, e.scope_id) for e in entries]
    }


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

    return {
        "settings": [_enriched_setting(e.key, e.value, e.scope, e.scope_id) for e in entries]
    }


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
    return {
        "settings": [_enriched_setting(e.key, e.value, e.scope, e.scope_id) for e in entries]
    }


@router.get("/metrics")
async def get_metrics(
    start: str | None = None,
    end: str | None = None,
):
    """Return tool call metrics aggregated by tool name. Returns the top 20
    tools by aggregate call count over the requested period. start/end are
    ISO date strings (YYYY-MM-DD) converted to period_hour range."""
    from collections import defaultdict

    from moira.tools.metrics import ToolMetricsRow

    repo = _metrics_repo()

    period_start = f"{start}T00:00" if start else None
    period_end = f"{end}T23:59" if end else None

    rows: list[ToolMetricsRow] = await repo.get_metrics(
        period_start=period_start,
        period_end=period_end,
    )

    tool_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        tool_counts[r.tool_name] += r.call_count

    top_20 = sorted(tool_counts.keys(), key=lambda k: tool_counts[k], reverse=True)[:20]

    filtered = [r for r in rows if r.tool_name in top_20]

    return {
        "metrics": [
            {
                "tool_name": r.tool_name,
                "call_type": r.call_type,
                "period_hour": r.period_hour,
                "call_count": r.call_count,
                "success_count": r.success_count,
                "error_count": r.error_count,
                "aggregate_duration_ms": r.aggregate_duration_ms,
                "low_duration_ms": r.low_duration_ms,
                "high_duration_ms": r.high_duration_ms,
            }
            for r in filtered
        ]
    }


@router.get("/metrics/inference")
async def get_inference_metrics(
    start: str | None = None,
    end: str | None = None,
):
    """Return inference metrics aggregated by model and purpose. start/end
    are ISO date strings (YYYY-MM-DD) converted to period_hour range."""
    from moira.inference.metrics import InferenceMetricsRepository

    repo = cast(
        InferenceMetricsRepository,
        service_provider("inference_metrics_repository"),
    )

    period_start = f"{start}T00:00" if start else None
    period_end = f"{end}T23:59" if end else None

    rows = await repo.get_metrics(
        period_start=period_start,
        period_end=period_end,
    )

    return {
        "metrics": [
            {
                "model": r.model,
                "purpose": r.purpose,
                "period_hour": r.period_hour,
                "call_count": r.call_count,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "thinking_tokens": r.thinking_tokens,
                "prompt_time_ms": r.prompt_time_ms,
                "gen_time_ms": r.gen_time_ms,
            }
            for r in rows
        ]
    }
