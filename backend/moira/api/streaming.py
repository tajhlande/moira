import json
import logging
import uuid
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from moira.config import MoiraConfig
from moira.persistence.interfaces import ConversationRepository
from moira.service_setup import service_provider
from moira.workflow.run_manager import RunManager

logger = logging.getLogger(__name__)

streaming_router = APIRouter()


def _conversations() -> ConversationRepository:
    return cast(ConversationRepository, service_provider("conversation_repository"))


def _config() -> MoiraConfig:
    return cast(MoiraConfig, service_provider("config"))


def _run_manager() -> RunManager:
    return cast(RunManager, service_provider("run_manager"))


@streaming_router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    body: dict[str, Any],
):
    """Start a new research run for the conversation. Returns JSON with
    run_id and user_message_id. The frontend then connects to GET /stream
    to receive SSE events."""
    conversations = _conversations()
    conversation = await conversations.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_content = body.get("content", "")
    if not user_content:
        raise HTTPException(status_code=400, detail="content is required")

    # Prevent starting a second run while one is in-flight
    run_mgr = _run_manager()
    if run_mgr.get_active_run(conversation_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="A run is already in progress for this conversation",
        )

    user_msg = await conversations.insert_message(conversation_id, "user", user_content)
    logger.info("Starting run for conversation %s", conversation_id)

    config = _config()
    budget_limit = config.budget.default_limit

    settings = body.get("settings") or {}
    raw_budget = settings.get("budget")
    if isinstance(raw_budget, (int, float)):
        budget_limit = max(35, min(150, int(raw_budget)))

    # Check for prior report to carry forward as context
    messages = await conversations.get_messages(conversation_id)
    prior_report = None
    for msg in reversed(messages):
        if msg.role == "assistant_report":
            try:
                prior_report = json.loads(msg.content)
            except (json.JSONDecodeError, TypeError):
                pass
            break

    initial_state = {
        "question": user_content,
        "plan": "",
        "active_tools": [],
        "findings": [],
        "compressed_findings": [],
        "draft": "",
        "verification": "",
        "report": None,
        "budget_remaining": float(budget_limit),
        "budget_limit": float(budget_limit),
        "verification_history": [],
        "unverified_claims": [],
        "error": "",
        "draft_retry_count": 0,
    }

    thread_id = str(uuid.uuid4())
    graph_config = {
        "configurable": {
            "thread_id": thread_id,
            "moira_config": config,
            "prior_report": prior_report,
        },
    }

    from langgraph.graph.state import CompiledStateGraph

    graph = cast(CompiledStateGraph, service_provider("research_graph"))

    run_id = await run_mgr.start_run(
        conversation_id=conversation_id,
        user_message_id=user_msg.id,
        graph=graph,
        initial_state=initial_state,
        graph_config=graph_config,
        config=config,
        conversation_repo=conversations,
    )

    return {"run_id": run_id, "user_message_id": user_msg.id}


@streaming_router.get("/conversations/{conversation_id}/stream")
async def stream_events(conversation_id: str):
    """SSE endpoint that subscribes to the active run for a conversation.
    Used for both live streaming (after POST /messages) and reconnection
    (after page reload)."""
    run_mgr = _run_manager()
    active_run = run_mgr.get_active_run(conversation_id)
    if active_run is None:
        raise HTTPException(
            status_code=404,
            detail="No active run for this conversation",
        )

    async def event_source():
        async for event in active_run.subscribe():
            yield event

    return EventSourceResponse(event_source())
