import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException

from moira.inference.registry import ModelRegistry
from moira.persistence.interfaces import (
    DEFAULT_USER_ID,
    ConversationRepository,
    ModelPreferencesRepository,
)
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
        "runs": [
            {
                "id": r.id,
                "user_message_id": r.user_message_id,
                "execution_steps": r.execution_steps,
                "tool_executions": r.tool_executions,
                "verification_attempts": r.verification_attempts,
                "report": r.report,
                "budget_limit": r.budget_limit,
                "budget_consumed": r.budget_consumed,
                "error": r.error,
                "status": r.status,
                "started_at": r.started_at,
                "completed_at": r.completed_at,
                "total_elapsed_ms": r.total_elapsed_ms,
            }
            for r in runs
        ],
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

    embedding_repo = service_provider("tool_embedding_repo")
    embedding_provider = service_provider("embedding_provider")

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
