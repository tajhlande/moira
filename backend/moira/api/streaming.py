import json
import logging
import uuid
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from moira.config import MoiraConfig
from moira.persistence.interfaces import SCOPE_SYSTEM, SYSTEM_SCOPE_ID, ConversationRepository
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


async def _build_tool_cost_and_limits() -> tuple[dict[str, float], dict[str, int], dict[str, int]]:
    """Load tool_costs, tool_call_limits, and tool_call_step_limits from the
    tool catalog."""
    from moira.persistence.interfaces import ToolRepository

    repo = cast(ToolRepository, service_provider("tool_repository"))
    tools = await repo.get_all_tools()

    tool_costs: dict[str, float] = {}
    tool_call_limits: dict[str, int] = {}
    tool_call_step_limits: dict[str, int] = {}
    for t in tools:
        if not t.enabled:
            continue
        tool_costs[t.name] = t.invocation_cost
        if t.call_limit_per_run and t.call_limit_per_run > 0:
            tool_call_limits[t.name] = t.call_limit_per_run
        if t.call_limit_per_step and t.call_limit_per_step > 0:
            tool_call_step_limits[t.name] = t.call_limit_per_step
    return tool_costs, tool_call_limits, tool_call_step_limits


def _apply_run_setting_overrides(
    settings: dict[str, Any],
    budget_limit: float,
    retry_limits: dict[str, int],
) -> tuple[float, dict[str, int]]:
    """Apply per-request overrides from the conversation settings tray.

    The tray sends ``budget``, ``max_review``, and ``max_evaluation`` in the
    request body's ``settings`` dict. These override the resolved system
    settings for this run only (not persisted).
    """
    raw_budget = settings.get("budget")
    if isinstance(raw_budget, (int, float)):
        budget_limit = float(max(35, min(300, int(raw_budget))))

    rl = dict(retry_limits)
    raw_max_review = settings.get("max_review")
    if isinstance(raw_max_review, (int, float)):
        rl["max_review"] = max(1, min(10, int(raw_max_review)))
    raw_max_evaluation = settings.get("max_evaluation")
    if isinstance(raw_max_evaluation, (int, float)):
        rl["max_evaluation"] = max(1, min(10, int(raw_max_evaluation)))

    return budget_limit, rl


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

    # Resolve default budget from settings service (async boundary).
    # Falls back to config file value if the service is unavailable.
    try:
        from moira.services.settings.settings_service import SettingsService

        settings_svc = cast(SettingsService, service_provider("settings_service"))
        resolved_budget = await settings_svc.get_typed("budget.default_limit")
        if resolved_budget is not None:
            budget_limit = int(resolved_budget)
    except Exception:
        logger.warning(
            "Settings service unavailable, falling back to config for budget_limit",
            exc_info=True,
        )

    settings = body.get("settings") or {}

    # Resolve cost weights from settings service (async boundary).
    # Falls back to config file values if the service is unavailable.
    cost_weights: dict[str, int] = {}
    retry_limits: dict[str, int] = {}
    try:
        from moira.services.settings.settings_service import SettingsService

        settings_svc = cast(SettingsService, service_provider("settings_service"))
        cost_weights = await settings_svc.get_typed_prefix(
            "budget.cost.", scope=SCOPE_SYSTEM, scope_id=SYSTEM_SCOPE_ID
        )  # pyright: ignore[reportAssignmentType]
        retry_limits = await settings_svc.get_typed_prefix(
            "retry.", scope=SCOPE_SYSTEM, scope_id=SYSTEM_SCOPE_ID
        )  # pyright: ignore[reportAssignmentType]
    except Exception:
        logger.warning(
            "Settings service unavailable, falling back to config for cost_weights",
            exc_info=True,
        )
        cw = config.budget.cost_weights
        cost_weights = {
            "decomposition": cw.decomposition,
            "tool_identification": cw.tool_identification,
            "planning": cw.planning,
            "research": cw.research,
            "synthesis": cw.synthesis,
            "research_review": cw.research_review,
            "evaluation": cw.evaluation,
            "report_generation": cw.report_generation,
        }
        rl = config.budget.retry_limits
        retry_limits = {
            "max_review": rl.max_review,
            "max_evaluation": rl.max_evaluation,
        }

    # Apply per-request overrides from the conversation settings tray.
    budget_limit, retry_limits = _apply_run_setting_overrides(settings, budget_limit, retry_limits)

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
                prior_turns.append(
                    {
                        "question": pending_question,
                        "answer": report.get("answer", ""),
                    }
                )
            except (json.JSONDecodeError, TypeError):
                pass
            pending_question = None

    prior_report = prior_turns[-1] if prior_turns else None
    prior_question = prior_turns[-1]["question"] if prior_turns else None

    tool_costs, tool_call_limits, tool_call_step_limits = await _build_tool_cost_and_limits()

    initial_state = {
        "knowledge": {
            "question": user_content,
            "user_goal": "",
            "topic": "",
            "entities": [],
            "concepts": [],
            "facts": [],
            "conclusions": [],
            "citations": [],
            "review_history": [],
            "evaluation_history": [],
        },
        "execution_state": {
            "candidate_tools": [],
            "evidence_requests": [],
            "budget_remaining": float(budget_limit),
            "budget_limit": float(budget_limit),
            "step_costs": cost_weights,
            "retry_limits": retry_limits,
            "tool_costs": tool_costs,
            "tool_call_limits": tool_call_limits,
            "tool_call_step_limits": tool_call_step_limits,
            "tool_call_counts": {},
            "total_tool_cost_consumed": 0.0,
            "error": "",
            "research_retry_count": 0,
            "research_count": 0,
            "review_count": 0,
            "evaluation_count": 0,
        },
    }

    thread_id = str(uuid.uuid4())
    graph_config = {
        "configurable": {
            "thread_id": thread_id,
            "conversation_id": conversation_id,
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


@streaming_router.post("/conversations/{conversation_id}/messages/{user_message_id}/rerun")
async def rerun_message(
    conversation_id: str,
    user_message_id: int,
    body: dict[str, Any] | None = None,
):
    """Re-run from an existing user message. Truncates the conversation from
    this message onward (deleting the old run and subsequent messages, but
    keeping the user message itself), then starts a fresh run."""
    conversations = _conversations()
    conversation = await conversations.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Look up the user message to get its content
    all_messages = await conversations.get_messages(conversation_id)
    user_msg = next(
        (m for m in all_messages if m.id == user_message_id and m.role == "user"),
        None,
    )
    if user_msg is None:
        raise HTTPException(status_code=404, detail="User message not found")

    # Prevent starting a second run while one is in-flight
    run_mgr = _run_manager()
    if run_mgr.get_active_run(conversation_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="A run is already in progress for this conversation",
        )

    # Truncate: delete runs >= this message and messages > this message
    await conversations.truncate_from_message(conversation_id, user_message_id)

    user_content = user_msg.content
    config = _config()
    budget_limit = config.budget.default_limit

    try:
        from moira.services.settings.settings_service import SettingsService

        settings_svc = cast(SettingsService, service_provider("settings_service"))
        resolved_budget = await settings_svc.get_typed("budget.default_limit")
        if resolved_budget is not None:
            budget_limit = int(resolved_budget)
    except Exception:
        logger.warning(
            "Settings service unavailable, falling back to config for budget_limit",
            exc_info=True,
        )

    settings = (body or {}).get("settings") or {}

    cost_weights: dict[str, int] = {}
    retry_limits: dict[str, int] = {}
    try:
        from moira.services.settings.settings_service import SettingsService

        settings_svc = cast(SettingsService, service_provider("settings_service"))
        cost_weights = await settings_svc.get_typed_prefix(
            "budget.cost.", scope=SCOPE_SYSTEM, scope_id=SYSTEM_SCOPE_ID
        )  # pyright: ignore[reportAssignmentType]
        retry_limits = await settings_svc.get_typed_prefix(
            "retry.", scope=SCOPE_SYSTEM, scope_id=SYSTEM_SCOPE_ID
        )  # pyright: ignore[reportAssignmentType]
    except Exception:
        logger.warning(
            "Settings service unavailable, falling back to config for cost_weights",
            exc_info=True,
        )
        cw = config.budget.cost_weights
        cost_weights = {
            "decomposition": cw.decomposition,
            "tool_identification": cw.tool_identification,
            "planning": cw.planning,
            "research": cw.research,
            "synthesis": cw.synthesis,
            "research_review": cw.research_review,
            "evaluation": cw.evaluation,
            "report_generation": cw.report_generation,
        }
        rl = config.budget.retry_limits
        retry_limits = {
            "max_review": rl.max_review,
            "max_evaluation": rl.max_evaluation,
        }

    # Apply per-request overrides from the conversation settings tray.
    budget_limit, retry_limits = _apply_run_setting_overrides(settings, budget_limit, retry_limits)

    # Re-read messages after truncation to get prior Q&A turns
    remaining_messages = await conversations.get_messages(conversation_id)
    prior_turns: list[dict[str, str]] = []
    pending_question: str | None = None
    for msg in remaining_messages:
        if msg.role == "user":
            pending_question = msg.content
        elif msg.role == "assistant_report" and pending_question is not None:
            try:
                report = json.loads(msg.content)
                prior_turns.append(
                    {
                        "question": pending_question,
                        "answer": report.get("answer", ""),
                    }
                )
            except (json.JSONDecodeError, TypeError):
                pass
            pending_question = None

    prior_report = prior_turns[-1] if prior_turns else None
    prior_question = prior_turns[-1]["question"] if prior_turns else None

    tool_costs, tool_call_limits, tool_call_step_limits = await _build_tool_cost_and_limits()

    initial_state = {
        "knowledge": {
            "question": user_content,
            "user_goal": "",
            "topic": "",
            "entities": [],
            "concepts": [],
            "facts": [],
            "conclusions": [],
            "citations": [],
            "review_history": [],
            "evaluation_history": [],
        },
        "execution_state": {
            "candidate_tools": [],
            "evidence_requests": [],
            "budget_remaining": float(budget_limit),
            "budget_limit": float(budget_limit),
            "step_costs": cost_weights,
            "retry_limits": retry_limits,
            "tool_costs": tool_costs,
            "tool_call_limits": tool_call_limits,
            "tool_call_step_limits": tool_call_step_limits,
            "tool_call_counts": {},
            "total_tool_cost_consumed": 0.0,
            "error": "",
            "research_retry_count": 0,
            "research_count": 0,
            "review_count": 0,
            "evaluation_count": 0,
        },
    }

    thread_id = str(uuid.uuid4())
    graph_config = {
        "configurable": {
            "thread_id": thread_id,
            "conversation_id": conversation_id,
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
        user_message_id=user_message_id,
        graph=graph,
        initial_state=initial_state,
        graph_config=graph_config,
        config=config,
        conversation_repo=conversations,
    )

    return {"run_id": run_id, "user_message_id": user_message_id}


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
