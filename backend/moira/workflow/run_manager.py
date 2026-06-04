import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, cast

from langgraph.graph.state import CompiledStateGraph

from moira.config import MoiraConfig
from moira.persistence.interfaces import ConversationRepository, WorkflowRun

logger = logging.getLogger(__name__)

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

# Sentinel value placed in subscriber queues to signal end-of-stream.
_STREAM_END = object()


class ActiveRun:
    """Manages a single in-flight graph run: executes the graph as a
    background asyncio.Task, accumulates state, persists incrementally,
    and broadcasts SSE events to subscribers.

    The graph runs independently of any HTTP request lifecycle. Multiple
    subscribers (original SSE + reconnected SSE) read from per-subscriber
    asyncio.Queue instances fed by the graph task."""

    def __init__(
        self,
        run_id: str,
        conversation_id: str,
        user_message_id: int,
        thread_id: str,
        started_at: str,
        budget_limit: float,
        conversation_repo: ConversationRepository,
        run_manager: "RunManager",
    ):
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.user_message_id = user_message_id
        self.thread_id = thread_id
        self.started_at = started_at
        self.budget_limit = budget_limit

        self._conversation_repo = conversation_repo
        self._run_manager = run_manager

        # Accumulated state mirrors WorkflowRun fields
        self.execution_steps: list[dict] = []
        self.tool_executions: list[dict] = []
        self.report: dict | None = None
        self.error: str = ""
        self.status: str = "running"
        self.budget_remaining: float = budget_limit
        self.budget_consumed: float = 0.0
        self.completed_at: str = ""
        self.total_elapsed_ms: int = 0

        # Internal bookkeeping
        self._current_step: dict | None = None
        self._subscribers: list[asyncio.Queue] = []
        self._graph_task: asyncio.Task | None = None
        self._run_started_at: float = 0.0
        self._step_started_at: float | None = None
        self._persist_lock = asyncio.Lock()
        self._persist_pending: bool = False
        self._stop_requested: bool = False

    def _replay_events(self) -> list[dict]:
        """Build a list of SSE events representing all state accumulated so
        far. Used to catch up a newly-connected subscriber so they don't miss
        events that fired before their queue was created."""
        events: list[dict] = []

        # Budget state
        events.append(
            {
                "event": "budget_update",
                "data": json.dumps(
                    {
                        "budget_remaining": self.budget_remaining,
                        "budget_consumed": self.budget_consumed,
                    }
                ),
            }
        )

        # Completed execution steps.  Tool results are embedded in each
        # step's detail.tool_results, so node_end carries them without
        # needing separate tool_result events.
        for step in self.execution_steps:
            payload = {
                "node": step["node"],
                "budget_remaining": step["budget_remaining"],
                "started_at": step.get("started_at", ""),
                "elapsed_ms": step.get("elapsed_ms", 0),
            }
            events.append({"event": "node_start", "data": json.dumps(payload)})
            end_payload = {**payload}
            if step.get("detail"):
                end_payload["detail"] = step["detail"]
            events.append({"event": "node_end", "data": json.dumps(end_payload)})

        # Currently running step (started but not yet ended). Emit
        # node_start, then replay any tool_results that arrived during
        # this step so the frontend can attach them to the live step.
        if self._current_step:
            payload = {
                "node": self._current_step["node"],
                "started_at": self._current_step.get("started_at", ""),
            }
            events.append({"event": "node_start", "data": json.dumps(payload)})
            for tr in self._current_step.get("detail", {}).get(
                "tool_results", []
            ):
                events.append(
                    {
                        "event": "tool_result",
                        "data": json.dumps(
                            {
                                "tool": tr["tool"],
                                "args": tr.get("args"),
                                "result": tr.get("result", ""),
                                "duration_ms": tr.get("duration_ms", 0),
                                "success": tr.get("success", False),
                            }
                        ),
                    }
                )

        # Verification attempts
        # Verification attempts reconstructed from verification steps'
        # detail.structured_output for SSE replay compatibility.
        for step in self.execution_steps:
            if step.get("node") == "verification" and step.get("detail", {}).get(
                "structured_output"
            ):
                step_idx = self.execution_steps.index(step) + 1
                attempt_num = sum(
                    1
                    for s in self.execution_steps[:step_idx]
                    if s.get("node") == "verification"
                )
                events.append(
                    {
                        "event": "verification_report",
                        "data": json.dumps(
                            {
                                "report": step["detail"]["structured_output"],
                                "attempt": attempt_num,
                            }
                        ),
                    }
                )

        # Report (if run already completed)
        if self.report:
            events.append(
                {
                    "event": "run_complete",
                    "data": json.dumps(
                        {
                            "report": self.report,
                            "total_elapsed_ms": self.total_elapsed_ms,
                        }
                    ),
                }
            )

        return events

    async def subscribe(self) -> Any:
        """Async iterator yielding SSE event dicts. First replays all events
        that occurred before this subscriber connected (so the client never
        misses planning or other early steps), then yields live events."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        try:
            # Replay accumulated state before yielding live events.
            for replay_event in self._replay_events():
                yield replay_event

            while True:
                event = await queue.get()
                if event is _STREAM_END:
                    break
                yield event
        finally:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    def start_graph(
        self,
        graph: CompiledStateGraph,
        initial_state: dict,
        graph_config: dict,
    ) -> None:
        """Launch the graph as a background asyncio.Task."""
        self._run_started_at = time.monotonic()
        self._graph_task = asyncio.create_task(
            self._run_graph(graph, initial_state, graph_config),
            name=f"run-{self.run_id[:8]}",
        )

    def start_graph_resume(
        self,
        graph: CompiledStateGraph,
        resume_input,
        graph_config: dict,
    ) -> None:
        """Launch the graph as a background asyncio.Task, resuming from a
        previous interrupt. ``resume_input`` is a ``Command(resume=...)``."""
        self._run_started_at = time.monotonic()
        self._graph_task = asyncio.create_task(
            self._run_graph_resume(graph, resume_input, graph_config),
            name=f"run-resume-{self.run_id[:8]}",
        )

    def stop_run(self) -> None:
        """Request an immediate stop. Sets a cooperative flag (used by
        ``_check_stop`` at node boundaries) AND cancels the graph task so
        the currently-running node is interrupted."""
        self._stop_requested = True
        if self._graph_task and not self._graph_task.done():
            self._graph_task.cancel()

    async def _run_graph(
        self,
        graph: CompiledStateGraph,
        initial_state: dict,
        graph_config: dict,
    ) -> None:
        """Iterate graph.astream(), accumulate state, persist incrementally,
        and broadcast events to subscribers."""
        try:
            self._broadcast(
                {
                    "event": "budget_update",
                    "data": json.dumps(
                        {
                            "budget_remaining": self.budget_limit,
                            "budget_consumed": 0,
                        }
                    ),
                }
            )

            async for event in graph.astream(
                initial_state,
                config=graph_config,
                stream_mode="custom",
            ):
                event_type = event.get("event", "unknown")
                payload = event.get("payload", {})
                self._handle_event(event_type, payload)

            if self._stop_requested:
                await self._handle_stop()
            elif self.status == "running":
                self.status = "completed"
                self._finalize_metrics()
                await self._persist()

        except asyncio.CancelledError:
            if self._stop_requested:
                await self._handle_stop()
            else:
                logger.warning(
                    "Graph run cancelled unexpectedly for conversation %s",
                    self.conversation_id,
                )
                raise

        except Exception as e:
            current_node = (
                self._current_step.get("node", "unknown") if self._current_step else "unknown"
            )
            logger.error(
                "Graph run error for conversation %s (node=%s): %s: %s",
                self.conversation_id,
                current_node,
                type(e).__name__,
                e,
                exc_info=True,
            )
            self.error = f"{type(e).__name__}: {e}"
            self.status = "error"
            if self._current_step:
                self._current_step["status"] = "error"
                self._current_step["error"] = self.error
                self._current_step["elapsed_ms"] = (
                    int((time.monotonic() - self._step_started_at) * 1000)
                    if self._step_started_at
                    else 0
                )
                self.execution_steps.append(self._current_step)
                self._current_step = None
            self._finalize_metrics()
            await self._persist()
            self._broadcast(
                {
                    "event": "run_error",
                    "data": json.dumps(
                        {
                            "node": "unknown",
                            "error": self.error,
                            "conversation_id": self.conversation_id,
                        }
                    ),
                }
            )

        if self.status == "stopped":
            self._broadcast(_STREAM_END)
            self._run_manager.remove_run(self.run_id)
            logger.info(
                "Run %s stopped (%d execution_steps)",
                self.run_id,
                len(self.execution_steps),
            )
            return

        if self.report:
            await self._conversation_repo.insert_message(
                self.conversation_id,
                "assistant_report",
                json.dumps(self.report),
            )
            logger.info("Persisted report for conversation %s", self.conversation_id)
        else:
            await self._conversation_repo.insert_message(
                self.conversation_id,
                "assistant",
                "Unable to generate a research report.",
            )

        self._broadcast(_STREAM_END)
        self._run_manager.remove_run(self.run_id)
        logger.info(
            "Run %s finished (%s, %d execution_steps)",
            self.run_id,
            self.status,
            len(self.execution_steps),
        )

    async def _run_graph_resume(
        self,
        graph: CompiledStateGraph,
        resume_input,
        graph_config: dict,
    ) -> None:
        """Resume a previously-interrupted graph run. Structured identically
        to ``_run_graph`` but invokes ``astream`` with a ``Command`` rather
        than fresh initial state."""
        try:
            self._broadcast(
                {
                    "event": "budget_update",
                    "data": json.dumps(
                        {
                            "budget_remaining": self.budget_remaining,
                            "budget_consumed": self.budget_consumed,
                        }
                    ),
                }
            )

            async for event in graph.astream(
                resume_input,
                config=graph_config,
                stream_mode="custom",
            ):
                event_type = event.get("event", "unknown")
                payload = event.get("payload", {})
                self._handle_event(event_type, payload)

            if self._stop_requested:
                await self._handle_stop()
            elif self.status == "running":
                self.status = "completed"
                self._finalize_metrics()
                await self._persist()

        except asyncio.CancelledError:
            if self._stop_requested:
                await self._handle_stop()
            else:
                logger.warning(
                    "Graph resume cancelled unexpectedly for conversation %s",
                    self.conversation_id,
                )
                raise

        except Exception as e:
            current_node = (
                self._current_step.get("node", "unknown") if self._current_step else "unknown"
            )
            logger.error(
                "Graph resume error for conversation %s (node=%s): %s: %s",
                self.conversation_id,
                current_node,
                type(e).__name__,
                e,
                exc_info=True,
            )
            self.error = f"{type(e).__name__}: {e}"
            self.status = "error"
            if self._current_step:
                self._current_step["status"] = "error"
                self._current_step["error"] = self.error
                self._current_step["elapsed_ms"] = (
                    int((time.monotonic() - self._step_started_at) * 1000)
                    if self._step_started_at
                    else 0
                )
                self.execution_steps.append(self._current_step)
                self._current_step = None
            self._finalize_metrics()
            await self._persist()
            self._broadcast(
                {
                    "event": "run_error",
                    "data": json.dumps(
                        {
                            "node": "unknown",
                            "error": self.error,
                            "conversation_id": self.conversation_id,
                        }
                    ),
                }
            )

        if self.status == "stopped":
            self._broadcast(_STREAM_END)
            self._run_manager.remove_run(self.run_id)
            logger.info(
                "Run %s stopped (%d execution_steps)",
                self.run_id,
                len(self.execution_steps),
            )
            return

        if self.report:
            await self._conversation_repo.insert_message(
                self.conversation_id,
                "assistant_report",
                json.dumps(self.report),
            )
            logger.info("Persisted report for conversation %s", self.conversation_id)
        else:
            await self._conversation_repo.insert_message(
                self.conversation_id,
                "assistant",
                "Unable to generate a research report.",
            )

        self._broadcast(_STREAM_END)
        self._run_manager.remove_run(self.run_id)
        logger.info(
            "Run %s finished (%s, %d execution_steps)",
            self.run_id,
            self.status,
            len(self.execution_steps),
        )

    async def _handle_stop(self) -> None:
        """Finalize run state after a user-initiated stop.
        Marks the current step as stopped, persists the step AND the run
        (so the stopped step survives page reload), and broadcasts the
        ``run_stopped`` SSE event.

        Uses ``asyncio.shield`` for persistence because this runs inside
        a ``CancelledError`` handler — bare awaits would be cancelled
        immediately."""
        stopped_node = "unknown"
        if self._current_step:
            self._current_step["status"] = "stopped"
            self._current_step["elapsed_ms"] = (
                int((time.monotonic() - self._step_started_at) * 1000)
                if self._step_started_at
                else 0
            )
            stopped_node = self._current_step.get("node", "unknown")

            step_copy = dict(self._current_step)
            try:
                await asyncio.shield(self._persist_step(step_copy))
            except asyncio.CancelledError:
                pass
            self.execution_steps.append(self._current_step)
            self._current_step = None

        self.status = "stopped"
        self._finalize_metrics()
        try:
            await asyncio.shield(self._persist())
        except asyncio.CancelledError:
            pass

        self._broadcast(
            {
                "event": "run_stopped",
                "data": json.dumps(
                    {
                        "node": stopped_node,
                        "conversation_id": self.conversation_id,
                    }
                ),
            }
        )
        logger.info(
            "Run %s stopped by user at node %s",
            self.run_id,
            stopped_node,
        )

    def _handle_event(self, event_type: str, payload: dict) -> None:
        """Process a single graph event: update accumulated state,
        persist if needed, broadcast to subscribers."""
        persist = False

        if event_type == "node_start":
            if self._current_step:
                self._current_step["status"] = "completed"
                self.execution_steps.append(self._current_step)
            self._step_started_at = time.monotonic()
            self._current_step = {
                "node": payload.get("node", ""),
                "label": STAGE_LABELS.get(payload.get("node", ""), payload.get("node", "")),
                "status": "running",
                "cost": 0,
                "budget_remaining": self.budget_remaining,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": 0,
            }
            payload["started_at"] = self._current_step["started_at"]

        elif event_type == "node_end":
            if payload.get("budget_remaining") is not None:
                self.budget_remaining = payload["budget_remaining"]
            elapsed_ms = (
                int((time.monotonic() - self._step_started_at) * 1000)
                if self._step_started_at
                else 0
            )
            if self._current_step:
                prev = self._current_step["budget_remaining"]
                self._current_step["budget_remaining"] = self.budget_remaining
                self._current_step["cost"] = prev - self.budget_remaining
                self._current_step["status"] = "completed"
                self._current_step["elapsed_ms"] = elapsed_ms
                if payload.get("detail"):
                    if "detail" not in self._current_step:
                        self._current_step["detail"] = {}
                    self._current_step["detail"].update(payload["detail"])
                # Merge inference metadata from the node_end payload.
                for key in (
                    "purpose",
                    "model",
                    "call_count",
                    "input_tokens",
                    "thinking_tokens",
                    "output_tokens",
                    "prompt_time_ms",
                    "gen_time_ms",
                ):
                    if key in payload:
                        self._current_step[key] = payload[key]
                # Persist the step to the workflow_steps table. Done
                # before appending to the in-memory list so the row is
                # written even if a subsequent error interrupts the run.
                from moira.persistence.write_queue import AsyncWriteQueue

                write_queue = cast(
                    AsyncWriteQueue,
                    self._run_manager._get_service("write_queue"),
                )
                step_copy = dict(self._current_step)
                write_queue.enqueue(lambda: self._persist_step(step_copy))
                self.execution_steps.append(self._current_step)
                self._current_step = None
            payload["elapsed_ms"] = elapsed_ms
            self.budget_consumed = self.budget_limit - self.budget_remaining
            persist = True

        elif event_type == "tool_result":
            tool_entry = {
                "tool": payload.get("tool", ""),
                "args": payload.get("args"),
                "result": payload.get("output", ""),
                "duration_ms": payload.get("duration_ms", 0),
                "success": payload.get("success", False),
            }
            self.tool_executions.append(tool_entry)

            # Also attach to the current step's detail so the frontend can
            # render tool results inline under the step that produced them.
            if self._current_step:
                if "detail" not in self._current_step:
                    self._current_step["detail"] = {}
                if "tool_results" not in self._current_step["detail"]:
                    self._current_step["detail"]["tool_results"] = []
                self._current_step["detail"]["tool_results"].append(tool_entry)

            # Rename key for SSE consumers: backend uses "output" internally
            # from the tool executor, but the conceptual model calls it "result"
            payload["result"] = payload.pop("output", "")
            persist = True

        elif event_type == "verification_report":
            # Verification reports are now embedded in the verification
            # step's detail.structured_output via node_end. We still
            # broadcast the SSE event and persist run metadata, but no
            # longer maintain a separate verification_attempts list.
            persist = True

        elif event_type == "run_complete":
            if payload.get("report"):
                self.report = payload["report"]
            self.status = "completed"
            self._finalize_metrics()
            payload["total_elapsed_ms"] = self.total_elapsed_ms
            persist = True

        elif event_type == "run_error":
            self.error = payload.get("error", "")
            self.status = "error"
            if self._current_step:
                self._current_step["status"] = "error"
                self._current_step["error"] = self.error
                self._current_step["elapsed_ms"] = (
                    int((time.monotonic() - self._step_started_at) * 1000)
                    if self._step_started_at
                    else 0
                )
                self.execution_steps.append(self._current_step)
                self._current_step = None
            self._finalize_metrics()
            payload["total_elapsed_ms"] = self.total_elapsed_ms
            persist = True

        if persist:
            asyncio.create_task(self._persist())

        self._broadcast(
            {
                "event": event_type,
                "data": json.dumps(payload),
            }
        )

    def _finalize_metrics(self) -> None:
        """Compute final timing metrics."""
        self.total_elapsed_ms = int((time.monotonic() - self._run_started_at) * 1000)
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.budget_consumed = self.budget_limit - self.budget_remaining

    async def _persist(self) -> None:
        """Upsert run-level metadata as a WorkflowRun. Steps are persisted
        individually via _persist_step() on node_end, so this only writes
        status, budget, error, and other run-level fields. Serialized via
        an asyncio.Lock so concurrent create_task calls don't collide on
        SQLite."""
        import sqlite3

        async with self._persist_lock:
            run = WorkflowRun(
                id=self.run_id,
                conversation_id=self.conversation_id,
                user_message_id=self.user_message_id,
                thread_id=self.thread_id,
                tool_executions=self.tool_executions,
                report=self.report,
                budget_limit=float(self.budget_limit),
                budget_consumed=self.budget_consumed,
                error=self.error,
                status=self.status,
                started_at=self.started_at,
                completed_at=self.completed_at or None,
                total_elapsed_ms=self.total_elapsed_ms or None,
            )
            for attempt in range(3):
                try:
                    await self._conversation_repo.save_workflow_run(run)
                    logger.debug(
                        "Persisted run %s (status=%s, %d steps)",
                        self.run_id,
                        self.status,
                        len(self.execution_steps),
                    )
                    return
                except sqlite3.OperationalError as e:
                    if "locked" in str(e) and attempt < 2:
                        await asyncio.sleep(0.1 * (attempt + 1))
                        continue
                    raise

    async def _persist_step(self, step_dict: dict) -> None:
        """Write a completed step to the workflow_steps table and upsert
        aggregated metrics into inference_metrics. Swallows exceptions so
        step persistence failures never break graph execution."""
        try:
            from moira.persistence.interfaces import WorkflowStep

            step_repo = cast(
                type,
                self._run_manager._get_service("workflow_step_repository"),
            )
            detail = step_dict.get("detail")
            wf_step = WorkflowStep(
                id=None,
                workflow_run_id=self.run_id,
                node_name=step_dict.get("node", ""),
                label=step_dict.get("label", ""),
                status=step_dict.get("status", "completed"),
                cost=step_dict.get("cost", 0),
                budget_remaining=step_dict.get("budget_remaining", 0),
                started_at=step_dict.get("started_at", ""),
                elapsed_ms=step_dict.get("elapsed_ms", 0),
                purpose=step_dict.get("purpose"),
                model=step_dict.get("model"),
                call_count=step_dict.get("call_count"),
                input_tokens=step_dict.get("input_tokens"),
                thinking_tokens=step_dict.get("thinking_tokens"),
                output_tokens=step_dict.get("output_tokens"),
                prompt_time_ms=step_dict.get("prompt_time_ms"),
                gen_time_ms=step_dict.get("gen_time_ms"),
                error=step_dict.get("error", ""),
                detail=detail if isinstance(detail, dict) else None,
            )
            await step_repo.save_step(wf_step)

            model = step_dict.get("model")
            purpose = step_dict.get("purpose")
            if model and purpose:
                from datetime import datetime as dt
                from datetime import timezone

                started_at = step_dict.get("started_at", "")
                if len(started_at) >= 13:
                    period_hour = started_at[:13] + ":00"
                else:
                    period_hour = dt.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:00"
                    )

                inference_repo = cast(
                    type,
                    self._run_manager._get_service("inference_metrics_repository"),
                )
                await inference_repo.record_step(
                    model=model,
                    purpose=purpose,
                    period_hour=period_hour,
                    call_count=step_dict.get("call_count") or 1,
                    input_tokens=step_dict.get("input_tokens") or 0,
                    output_tokens=step_dict.get("output_tokens") or 0,
                    thinking_tokens=step_dict.get("thinking_tokens") or 0,
                    prompt_time_ms=step_dict.get("prompt_time_ms") or 0.0,
                    gen_time_ms=step_dict.get("gen_time_ms") or 0.0,
                )
        except Exception:
            logger.debug("Failed to persist step for run %s", self.run_id, exc_info=True)

    def _broadcast(self, event: Any) -> None:
        """Put an event into all subscriber queues. Drops the oldest event
        if a queue is full rather than blocking the graph task."""
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                queue.get_nowait()
                queue.put_nowait(event)


class RunManager:
    """Process-level singleton that manages active graph runs.

    Registered in service_setup.py and retrieved via service_provider("run_manager").
    Tracks runs by both run_id and conversation_id (one active run per conversation)."""

    def __init__(self) -> None:
        self._active_runs: dict[str, ActiveRun] = {}
        self._conversation_runs: dict[str, str] = {}
        self._global_subscribers: list[asyncio.Queue] = []

    def _broadcast_global(self, event: dict) -> None:
        """Push an event to all global subscribers (sidebar status listeners)."""
        for queue in self._global_subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                queue.get_nowait()
                queue.put_nowait(event)

    def active_conversation_ids(self) -> list[str]:
        """Return conversation IDs that currently have running workflows."""
        return list(self._conversation_runs.keys())

    async def subscribe_global(self):
        """Async iterator yielding global run_status events for the sidebar.
        First replays current active runs, then yields live events."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._global_subscribers.append(queue)
        try:
            for cid in self.active_conversation_ids():
                yield {
                    "event": "run_status",
                    "data": json.dumps(
                        {"conversation_id": cid, "status": "running"}
                    ),
                }
            while True:
                event = await queue.get()
                if event is _STREAM_END:
                    break
                yield event
        finally:
            if queue in self._global_subscribers:
                self._global_subscribers.remove(queue)

    async def start_run(
        self,
        conversation_id: str,
        user_message_id: int,
        graph: CompiledStateGraph,
        initial_state: dict,
        graph_config: dict,
        config: MoiraConfig,
        conversation_repo: ConversationRepository,
    ) -> str:
        """Create an ActiveRun, persist initial state, launch the graph.
        Returns the run_id."""
        run_id = str(uuid.uuid4())
        thread_id = graph_config.get("configurable", {}).get("thread_id", run_id)
        started_at = datetime.now(timezone.utc).isoformat()
        budget_limit = config.budget.default_limit

        active_run = ActiveRun(
            run_id=run_id,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            thread_id=thread_id,
            started_at=started_at,
            budget_limit=float(budget_limit),
            conversation_repo=conversation_repo,
            run_manager=self,
        )

        self._active_runs[run_id] = active_run
        self._conversation_runs[conversation_id] = run_id

        # Inject run_id into the graph config so nodes can identify
        # which run they belong to.
        graph_config = dict(graph_config)
        configurable = dict(graph_config.get("configurable", {}))
        configurable["run_id"] = run_id
        graph_config["configurable"] = configurable

        # Persist initial state so the run is visible immediately
        await active_run._persist()

        active_run.start_graph(graph, initial_state, graph_config)
        logger.info("Started run %s for conversation %s", run_id, conversation_id)
        self._broadcast_global(
            {
                "event": "run_status",
                "data": json.dumps(
                    {"conversation_id": conversation_id, "status": "running"}
                ),
            }
        )
        return run_id

    async def resume_run(
        self,
        conversation_id: str,
        config: MoiraConfig,
        conversation_repo: ConversationRepository,
    ) -> str:
        """Resume a previously stopped run. Loads the thread_id from the
        persisted WorkflowRun, creates a new ActiveRun, and starts the
        graph from the checkpoint using ``Command(resume=True)``.
        Returns the new run_id."""
        from langgraph.graph.state import CompiledStateGraph
        from langgraph.types import Command

        runs = await conversation_repo.get_workflow_runs(conversation_id)
        stopped_run = None
        for run in reversed(runs):
            if run.status == "stopped":
                stopped_run = run
                break
        if stopped_run is None:
            raise ValueError(f"No stopped run found for conversation {conversation_id}")

        graph = cast(CompiledStateGraph, self._get_service("research_graph"))

        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()

        active_run = ActiveRun(
            run_id=run_id,
            conversation_id=conversation_id,
            user_message_id=stopped_run.user_message_id,
            thread_id=stopped_run.thread_id,
            started_at=started_at,
            budget_limit=float(stopped_run.budget_limit),
            conversation_repo=conversation_repo,
            run_manager=self,
        )
        active_run.budget_remaining = stopped_run.budget_limit - stopped_run.budget_consumed
        active_run.budget_consumed = stopped_run.budget_consumed

        self._active_runs[run_id] = active_run
        self._conversation_runs[conversation_id] = run_id

        graph_config = {
            "configurable": {
                "thread_id": stopped_run.thread_id,
                "run_id": run_id,
                "moira_config": config,
            },
        }

        await active_run._persist()

        active_run.start_graph_resume(graph, Command(resume=True), graph_config)
        logger.info(
            "Resumed run %s for conversation %s (thread %s)",
            run_id,
            conversation_id,
            stopped_run.thread_id,
        )
        self._broadcast_global(
            {
                "event": "run_status",
                "data": json.dumps(
                    {"conversation_id": conversation_id, "status": "running"}
                ),
            }
        )
        return run_id

    def _get_service(self, name: str) -> object:
        from moira.service_setup import service_provider

        return service_provider(name)

    def get_active_run(self, conversation_id: str) -> ActiveRun | None:
        """Look up the active run for a conversation. Returns None if no
        run is in-flight."""
        run_id = self._conversation_runs.get(conversation_id)
        if run_id is None:
            return None
        return self._active_runs.get(run_id)

    def remove_run(self, run_id: str) -> None:
        """Called by ActiveRun when the graph completes. Cleans up both dicts
        and notifies global subscribers that the run is no longer active."""
        active_run = self._active_runs.pop(run_id, None)
        if active_run:
            self._conversation_runs.pop(active_run.conversation_id, None)
            self._broadcast_global(
                {
                    "event": "run_status",
                    "data": json.dumps(
                        {
                            "conversation_id": active_run.conversation_id,
                            "status": active_run.status,
                        }
                    ),
                }
            )
