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

    from moira.persistence.write_queue import AsyncWriteQueue

    write_queue = cast(AsyncWriteQueue, service_provider("write_queue"))
    user_msg = await write_queue.enqueue(
        lambda: conversations.insert_message(conversation_id, "user", user_content)
    )
    logger.info("Starting run for conversation %s", conversation_id)

    config = _config()
    budget_limit = config.budget.default_limit

    settings = body.get("settings") or {}
    raw_budget = settings.get("budget")
    if isinstance(raw_budget, (int, float)):
        budget_limit = max(35, min(150, int(raw_budget)))

    # Collect all prior Q&A turns for context carry-forward.
    # Each turn is a (question, answer) pair from a completed run.
    all_messages = await conversations.get_messages(conversation_id)
    prior_turns: list[dict[str, str]] = []
    pending_question: str | None = None
    for msg in all_messages:
        if msg.role == "user":
            pending_question = msg.content
        elif msg.role == "assistant_report" and pending_question is not None:
            try:
                report = json.loads(msg.content)
                prior_turns.append({
                    "question": pending_question,
                    "answer": report.get("answer", ""),
                })
            except (json.JSONDecodeError, TypeError):
                pass
            pending_question = None

    prior_report = prior_turns[-1] if prior_turns else None
    prior_question = prior_turns[-1]["question"] if prior_turns else None

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
            "prior_question": prior_question,
            "prior_turns": prior_turns,
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


@streaming_router.get("/events")
async def global_events():
    """Global SSE endpoint for sidebar run-status notifications.
    Yields run_status events when any conversation's run starts or
    completes. On connect, replays all currently-active runs so a
    fresh page load immediately shows which conversations are running."""
    run_mgr = _run_manager()

    async def event_source():
        async for event in run_mgr.subscribe_global():
            yield event

    return EventSourceResponse(event_source())


@streaming_router.post("/conversations/{conversation_id}/runs/stop")
async def stop_run(conversation_id: str):
    """Stop the active run for a conversation. Sets a cooperative flag
    checked by each node at its start. Returns immediately."""
    run_mgr = _run_manager()
    active_run = run_mgr.get_active_run(conversation_id)
    if active_run is None:
        raise HTTPException(
            status_code=404,
            detail="No active run for this conversation",
        )
    if active_run.status != "running":
        raise HTTPException(
            status_code=409,
            detail="Run is not in a running state",
        )
    active_run.stop_run()
    return {"status": "stopped"}


@streaming_router.post("/conversations/{conversation_id}/runs/resume")
async def resume_run(conversation_id: str):
    """Resume a previously stopped or errored run. Loads the thread_id
    from the persisted run, creates a new ActiveRun, and starts the
    graph from the checkpoint. This re-executes the node that was
    running when the stop/error occurred."""
    run_mgr = _run_manager()
    if run_mgr.get_active_run(conversation_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="A run is already in progress for this conversation",
        )

    conversations = _conversations()
    runs = await conversations.get_workflow_runs(conversation_id)
    resumable_run = None
    for run in reversed(runs):
        if run.status in ("stopped", "error"):
            resumable_run = run
            break
    if resumable_run is None:
        raise HTTPException(
            status_code=404,
            detail="No stopped or errored run found for this conversation",
        )

    config = _config()
    run_id = await run_mgr.resume_run(
        conversation_id=conversation_id,
        config=config,
        conversation_repo=conversations,
    )
    return {"run_id": run_id, "user_message_id": resumable_run.user_message_id}
