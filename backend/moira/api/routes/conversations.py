"""Conversation, run, and model-assignment endpoints.

Owns the API surface for the Conversations tab in the frontend: chat
history, run/step detail, and the model-assignment picker. Also exposes
the lazy step-detail endpoint used to expand individual workflow steps
in the run timeline."""

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException

from moira.inference.registry import ModelRegistry
from moira.persistence.interfaces import (
    DEFAULT_USER_ID,
    ConversationRepository,
    ModelPreferencesRepository,
    WorkflowRun,
    WorkflowStep,
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
