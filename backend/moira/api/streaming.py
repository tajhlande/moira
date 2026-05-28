import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from langgraph.graph.state import CompiledStateGraph
from sse_starlette.sse import EventSourceResponse

from moira.config import MoiraConfig
from moira.persistence.interfaces import ConversationRepository, WorkflowRun
from moira.service_setup import service_provider

logger = logging.getLogger(__name__)

streaming_router = APIRouter()

STAGE_LABELS = {
    "planning": "Planning",
    "tool_discovery": "Discovering Tools",
    "tool_selection": "Selecting Tools",
    "research_execution": "Researching",
    "compression": "Summarizing",
    "draft_synthesis": "Drafting",
    "verification": "Verifying",
    "report_generation": "Generating Report",
}


def _conversations() -> ConversationRepository:
    return cast(ConversationRepository, service_provider("conversation_repository"))


def _config() -> MoiraConfig:
    return cast(MoiraConfig, service_provider("config"))


def _graph() -> CompiledStateGraph:
    return cast(CompiledStateGraph, service_provider("research_graph"))


@streaming_router.post("/conversations/{conversation_id}/messages")
async def send_message_streaming(
    conversation_id: str,
    body: dict[str, Any],
):
    """SSE endpoint: starts a graph run and streams workflow progress.
    Accumulates workflow artifacts and persists them as a WorkflowRun."""
    conversations = _conversations()
    conversation = await conversations.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_content = body.get("content", "")
    if not user_content:
        raise HTTPException(status_code=400, detail="content is required")

    user_msg = await conversations.insert_message(conversation_id, "user", user_content)
    logger.info("Starting streaming graph run for conversation %s", conversation_id)

    config = _config()
    budget_limit = config.budget.default_limit

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
        "thinking_traces": {},
    }

    thread_id = str(uuid.uuid4())
    graph_config = {
        "configurable": {
            "thread_id": thread_id,
            "moira_config": config,
            "prior_report": prior_report,
        },
    }

    graph = _graph()
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()

    # Accumulator for workflow artifacts that will be persisted as a WorkflowRun
    run_execution_steps: list[dict] = []
    run_tool_executions: list[dict] = []
    run_verification_attempts: list[dict] = []
    run_thinking_traces: dict[str, str] = {}
    current_step: dict | None = None
    run_report: dict | None = None
    run_error: str = ""
    run_status = "running"
    budget_remaining = float(budget_limit)
    run_started_at = time.monotonic()
    step_started_at: float | None = None

    async def event_generator():
        nonlocal current_step, run_report, run_error, run_status, budget_remaining
        nonlocal step_started_at
        try:
            # Seed the frontend with the initial budget before any nodes run
            yield {
                "event": "budget_update",
                "data": json.dumps(
                    {
                        "budget_remaining": budget_limit,
                        "budget_consumed": 0,
                    }
                ),
            }
            async for event in graph.astream(
                initial_state,
                config=graph_config,
                stream_mode="custom",
            ):
                event_type = event.get("event", "unknown")
                payload = event.get("payload", {})

                if event_type == "node_start":
                    if current_step:
                        current_step["status"] = "completed"
                        run_execution_steps.append(current_step)
                    step_started_at = time.monotonic()
                    current_step = {
                        "node": payload.get("node", ""),
                        "label": STAGE_LABELS.get(
                            payload.get("node", ""), payload.get("node", "")
                        ),
                        "status": "running",
                        "cost": 0,
                        "budget_remaining": budget_remaining,
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "elapsed_ms": 0,
                    }
                    payload["started_at"] = current_step["started_at"]
                elif event_type == "node_end":
                    if payload.get("budget_remaining") is not None:
                        budget_remaining = payload["budget_remaining"]
                    elapsed_ms = (
                        int((time.monotonic() - step_started_at) * 1000) if step_started_at else 0
                    )
                    if current_step:
                        prev = current_step["budget_remaining"]
                        current_step["budget_remaining"] = budget_remaining
                        current_step["cost"] = prev - budget_remaining
                        current_step["status"] = "completed"
                        current_step["elapsed_ms"] = elapsed_ms
                        run_execution_steps.append(current_step)
                        current_step = None
                    payload["elapsed_ms"] = elapsed_ms
                elif event_type == "tool_result":
                    run_tool_executions.append(
                        {
                            "tool": payload.get("tool", ""),
                            "result": payload.get("output", ""),
                            "duration_ms": payload.get("duration_ms", 0),
                            "success": payload.get("success", False),
                        }
                    )
                    # Rename key for SSE consumers: backend uses "output" internally
                    # from the tool executor, but the conceptual model calls it "result"
                    payload["result"] = payload.pop("output", "")
                elif event_type == "verification_report":
                    run_verification_attempts.append(
                        {
                            "report": payload.get("report", {}),
                            "attempt": payload.get("attempt", 0),
                        }
                    )
                elif event_type == "run_complete":
                    if payload.get("report"):
                        run_report = payload["report"]
                    run_status = "completed"
                    payload["total_elapsed_ms"] = int((time.monotonic() - run_started_at) * 1000)
                elif event_type == "run_error":
                    run_error = payload.get("error", "")
                    run_status = "error"
                    if current_step:
                        current_step["status"] = "error"
                        current_step["error"] = run_error
                        current_step["elapsed_ms"] = (
                            int((time.monotonic() - step_started_at) * 1000)
                            if step_started_at
                            else 0
                        )
                        run_execution_steps.append(current_step)
                        current_step = None
                    payload["total_elapsed_ms"] = int((time.monotonic() - run_started_at) * 1000)

                # Merge thinking traces from graph state when available
                if event_type == "node_end" and payload.get("thinking_traces"):
                    run_thinking_traces.update(payload["thinking_traces"])

                yield {
                    "event": event_type,
                    "data": json.dumps(payload),
                }
        except Exception as e:
            logger.error(
                "Graph run error for conversation %s: %s: %s",
                conversation_id,
                type(e).__name__,
                e,
            )
            run_error = f"{type(e).__name__}: {e}"
            run_status = "error"
            if current_step:
                current_step["status"] = "error"
                current_step["error"] = run_error
                run_execution_steps.append(current_step)
                current_step = None
            yield {
                "event": "run_error",
                "data": json.dumps(
                    {
                        "node": "unknown",
                        "error": run_error,
                        "conversation_id": conversation_id,
                    }
                ),
            }

    async def streaming_response():
        report = None
        async for sse_event in event_generator():
            yield sse_event
            if sse_event.get("event") == "run_complete":
                try:
                    payload = json.loads(sse_event.get("data", "{}"))
                    report = payload.get("report")
                except (json.JSONDecodeError, TypeError):
                    pass

        # Persist the assistant response
        if report:
            report_text = report.get("answer", "")
            await conversations.insert_message(conversation_id, "assistant", report_text)
            await conversations.insert_message(
                conversation_id, "assistant_report", json.dumps(report)
            )
            logger.info("Persisted report for conversation %s", conversation_id)
        else:
            await conversations.insert_message(
                conversation_id,
                "assistant",
                "Unable to generate a research report.",
            )

        # Persist the workflow run
        budget_consumed = budget_limit - budget_remaining
        total_elapsed_ms = int((time.monotonic() - run_started_at) * 1000)
        completed_at = datetime.now(timezone.utc).isoformat()
        run = WorkflowRun(
            id=run_id,
            conversation_id=conversation_id,
            user_message_id=user_msg.id,
            thread_id=thread_id,
            execution_steps=run_execution_steps,
            tool_executions=run_tool_executions,
            verification_attempts=run_verification_attempts,
            thinking_traces=run_thinking_traces,
            report=run_report,
            budget_limit=float(budget_limit),
            budget_consumed=budget_consumed,
            error=run_error,
            status=run_status,
            started_at=started_at,
            completed_at=completed_at,
            total_elapsed_ms=total_elapsed_ms,
        )
        await conversations.save_workflow_run(run)
        logger.info(
            "Saved workflow run %s (%s, %d execution_steps)",
            run_id,
            run_status,
            len(run_execution_steps),
        )

    return EventSourceResponse(streaming_response())
