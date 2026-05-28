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
    "Given the user's message, produce a concise 3-6 word title that captures "
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
                "thinking_traces": r.thinking_traces,
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

    # Build a summary of the conversation for the title model.
    # Use the first and most recent user messages to keep the prompt small.
    first = user_messages[0].content[:500]
    last = user_messages[-1].content[:500] if len(user_messages) > 1 else ""
    context = f"First message: {first}"
    if last:
        context += f"\nLatest message: {last}"

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
    return {
        "id": updated.id,
        "title": updated.title,
        "created_at": updated.created_at,
    }
