import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException

from moira.inference.registry import ModelRegistry
from moira.persistence.interfaces import (
    DEFAULT_USER_ID,
    ModelPreferencesRepository,
    SessionRepository,
)
from moira.service_setup import service_provider

logger = logging.getLogger(__name__)

router = APIRouter()


def _sessions() -> SessionRepository:
    return cast(SessionRepository, service_provider("session_repository"))


def _prefs() -> ModelPreferencesRepository:
    return cast(ModelPreferencesRepository, service_provider("model_preferences_repository"))


def _registry() -> ModelRegistry:
    return cast(ModelRegistry, service_provider("model_registry"))


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/sessions", response_model=dict)
async def create_session(
    sessions: SessionRepository = Depends(_sessions),
):
    logger.info("Creating new session")
    session = await sessions.create_session(user_id=DEFAULT_USER_ID, title="New Session")
    logger.info("Created session %s", session.id)
    return {"id": session.id, "title": session.title, "created_at": session.created_at}


@router.get("/sessions", response_model=list)
async def list_sessions(
    sessions: SessionRepository = Depends(_sessions),
):
    result = await sessions.list_sessions(DEFAULT_USER_ID)
    return [{"id": s.id, "title": s.title, "created_at": s.created_at} for s in result]


@router.get("/sessions/{session_id}", response_model=dict)
async def get_session(
    session_id: str,
    sessions: SessionRepository = Depends(_sessions),
):
    session = await sessions.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = await sessions.get_messages(session_id)
    return {
        "id": session.id,
        "title": session.title,
        "created_at": session.created_at,
        "messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at} for m in messages
        ],
    }


@router.post("/sessions/{session_id}/messages", response_model=dict)
async def send_message(
    session_id: str,
    body: dict[str, Any],
    sessions: SessionRepository = Depends(_sessions),
):
    # MVP implementation: proxies messages directly to the resolved LLM.
    # Does NOT invoke the LangGraph research workflow (planning -> tool
    # discovery -> tool selection -> research -> compression -> draft
    # synthesis -> verification -> report generation). When the workflow
    # engine is implemented, this handler should initiate a graph run.
    session = await sessions.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    user_content = body.get("content", "")
    if not user_content:
        raise HTTPException(status_code=400, detail="content is required")

    logger.info("Sending message to session %s", session_id)
    await sessions.insert_message(session_id, "user", user_content)

    registry = _registry()
    try:
        resolved = await registry.resolve("intelligence")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.debug("Using model %s from endpoint for session %s", resolved.model_id, session_id)
    history = await sessions.get_messages(session_id)
    messages_payload = [{"role": m.role, "content": m.content} for m in history]

    try:
        response_text = await resolved.client.chat_completion(
            model=resolved.model_id,
            messages=messages_payload,
        )
    except Exception as e:
        logger.error("Model error for session %s: %s", session_id, e)
        raise HTTPException(status_code=502, detail=f"Model error: {e}")

    await sessions.insert_message(session_id, "assistant", response_text)
    logger.info("Response received for session %s (%d chars)", session_id, len(response_text))

    return {"role": "assistant", "content": response_text}


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
