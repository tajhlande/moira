"""Manages a single in-flight graph run: executes the graph as a
background asyncio.Task, accumulates state, persists incrementally,
and broadcasts SSE events to subscribers.

Split from run_manager.py to separate single-run lifecycle management
from the process-level run registry.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

from langgraph.graph.state import CompiledStateGraph, RunnableConfig

from moira.models.knowledge import knowledge_summary
from moira.persistence.interfaces import ConversationRepository, WorkflowRun

if TYPE_CHECKING:
    from moira.workflow.run_manager import RunManager

logger = logging.getLogger(__name__)

STAGE_LABELS = {
    "decomposition": "Analyzing question",
    "tool_identification": "Identifying tools",
    "planning": "Planning research approach",
    "research": "Researching",
    "synthesis": "Synthesizing conclusions",
    "research_review": "Reviewing research",
    "evaluation": "Evaluating conclusions",
    "report_generation": "Generating report",
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
        self.report: dict | None = None
        self.knowledge: dict | None = None
        self.error: str = ""
        self.status: str = "running"
        self.budget_remaining: float = budget_limit
        self.budget_consumed: float = 0.0
        self.completed_at: str = ""
        self.total_elapsed_ms: int = 0
        self.state_version: int = 1
        self.updated_at: str = started_at

        # Internal bookkeeping
        self._current_step: dict | None = None
        self._subscribers: list[asyncio.Queue] = []
        self._graph_task: asyncio.Task | None = None
        self._run_started_at: float = 0.0
        self._step_started_at: float | None = None
        self._persist_lock = asyncio.Lock()
        self._persist_pending: bool = False
        self._stop_requested: bool = False
        self._next_step_ordinal: int = 1

    def _mark_state_changed(self) -> None:
        """Bump run snapshot version and refresh run updated timestamp."""
        self.state_version += 1
        self.updated_at = datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _step_tool_call_count(step: dict) -> int:
        detail = step.get("detail") or {}
        tool_results = detail.get("tool_results") if isinstance(detail, dict) else None
        return len(tool_results) if isinstance(tool_results, list) else 0

    def _step_summary(self, step: dict, fallback_id: str) -> dict:
        return {
            "id": step.get("id") or fallback_id,
            "node": step.get("node", ""),
            "label": step.get("label", ""),
            "status": step.get("status", "completed"),
            "cost": step.get("cost", 0),
            "budget_remaining": step.get("budget_remaining", self.budget_remaining),
            "elapsed_ms": step.get("elapsed_ms"),
            "started_at": step.get("started_at", ""),
            "error": step.get("error") or None,
            "tool_call_count": step.get("tool_call_count", self._step_tool_call_count(step)),
            "step_version": step.get("step_version", 1),
            "has_detail": bool(step.get("detail")),
            "detail_run_id": step.get("detail_run_id"),
        }

    def _build_run_snapshot(self) -> dict:
        step_summaries = [
            self._step_summary(step, f"{self.run_id}:{idx + 1}")
            for idx, step in enumerate(self.execution_steps)
        ]
        if self._current_step:
            step_summaries.append(self._step_summary(self._current_step, f"{self.run_id}:current"))

        all_steps = self.execution_steps + ([self._current_step] if self._current_step else [])
        input_tokens = sum(s.get("input_tokens") or 0 for s in all_steps)
        output_tokens = sum(s.get("output_tokens") or 0 for s in all_steps)
        thinking_tokens = sum(s.get("thinking_tokens") or 0 for s in all_steps)

        return {
            "id": self.run_id,
            "conversation_id": self.conversation_id,
            "user_message_id": self.user_message_id,
            "status": self.status,
            "budget_limit": self.budget_limit,
            "budget_consumed": self.budget_consumed,
            "total_elapsed_ms": self.total_elapsed_ms or None,
            "error": self.error,
            "report": self.report,
            "knowledge": self.knowledge,
            "execution_steps": step_summaries,
            "state_version": self.state_version,
            "started_at": self.started_at,
            "completed_at": self.completed_at or None,
            "updated_at": self.updated_at,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "thinking_tokens": thinking_tokens,
        }

    def _broadcast_run_snapshot(self) -> None:
        self._broadcast(
            {
                "event": "run_snapshot",
                "data": json.dumps(self._build_run_snapshot()),
            }
        )

    def _replay_events(self) -> list[dict]:
        """Build a list of SSE events representing all state accumulated so
        far. Used to catch up a newly-connected subscriber so they don't miss
        events that fired before their queue was created.

        Phase 4: only replays the canonical run_snapshot. The frontend
        ignores legacy event types (node_start, node_end, tool_result,
        budget_update, evaluation_report, run_complete)."""
        return [
            {
                "event": "run_snapshot",
                "data": json.dumps(self._build_run_snapshot()),
            }
        ]

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
        graph_config: RunnableConfig,
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
        graph_config: RunnableConfig,
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
        graph_config: RunnableConfig,
    ) -> None:
        """Iterate graph.astream(), accumulate state, persist incrementally,
        and broadcast events to subscribers."""
        try:
            async for mode, data in graph.astream(
                initial_state,
                config=graph_config,
                stream_mode=["custom", "values"],
            ):
                if mode == "custom":
                    event_type = data.get("event", "unknown")
                    payload = data.get("payload", {})
                    self._handle_event(event_type, payload)
                elif mode == "values":
                    self._handle_state_values(data)

            if self._stop_requested:
                await self._handle_stop()
            elif self.status == "running":
                self.status = "completed"
                self._finalize_metrics()
                self._mark_state_changed()
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
                self._current_step["step_version"] = self._current_step.get("step_version", 1) + 1
                # Persist the failed step — the run_error event handler
                # may not have run if the exception propagated before
                # the custom event was yielded from langgraph.
                from moira.persistence.write_queue import AsyncWriteQueue

                write_queue = cast(
                    AsyncWriteQueue,
                    self._run_manager._get_service("write_queue"),
                )
                exc_step_copy = dict(self._current_step)
                write_queue.enqueue(lambda: self._persist_step(exc_step_copy))
                self.execution_steps.append(self._current_step)
                self._current_step = None
            self._finalize_metrics()
            self._mark_state_changed()
            await self._persist()
            self._broadcast_run_snapshot()

        if self.status == "stopped":
            self._broadcast(_STREAM_END)
            self._run_manager.remove_run(self.run_id)
            logger.info(
                "Run %s stopped (%d execution_steps)",
                self.run_id,
                len(self.execution_steps),
            )
            return

        if self.status == "error":
            await self._queue_insert_message(
                "assistant",
                "The research run encountered an error and could not generate a report.",
            )
            self._broadcast(_STREAM_END)
            self._run_manager.remove_run(self.run_id)
            logger.info(
                "Run %s errored (%d execution_steps)",
                self.run_id,
                len(self.execution_steps),
            )
            return

        if self.report:
            await self._queue_insert_message(
                "assistant_report",
                json.dumps(self.report),
            )
            logger.info("Persisted report for conversation %s", self.conversation_id)

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
        graph_config: RunnableConfig,
    ) -> None:
        """Resume a previously-interrupted graph run. Structured identically
        to ``_run_graph`` but invokes ``astream`` with a ``Command`` rather
        than fresh initial state."""
        try:
            async for mode, data in graph.astream(
                resume_input,
                config=graph_config,
                stream_mode=["custom", "values"],
            ):
                if mode == "custom":
                    event_type = data.get("event", "unknown")
                    payload = data.get("payload", {})
                    self._handle_event(event_type, payload)
                elif mode == "values":
                    self._handle_state_values(data)

            if self._stop_requested:
                await self._handle_stop()
            elif self.status == "running":
                self.status = "completed"
                self._finalize_metrics()
                self._mark_state_changed()
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
                self._current_step["step_version"] = self._current_step.get("step_version", 1) + 1
                from moira.persistence.write_queue import AsyncWriteQueue

                write_queue = cast(
                    AsyncWriteQueue,
                    self._run_manager._get_service("write_queue"),
                )
                exc_step_copy = dict(self._current_step)
                write_queue.enqueue(lambda: self._persist_step(exc_step_copy))
                self.execution_steps.append(self._current_step)
                self._current_step = None
            self._finalize_metrics()
            self._mark_state_changed()
            await self._persist()
            self._broadcast_run_snapshot()

        if self.status == "stopped":
            self._broadcast(_STREAM_END)
            self._run_manager.remove_run(self.run_id)
            logger.info(
                "Run %s stopped (%d execution_steps)",
                self.run_id,
                len(self.execution_steps),
            )
            return

        if self.status == "error":
            await self._queue_insert_message(
                "assistant",
                "The research run encountered an error and could not generate a report.",
            )
            self._broadcast(_STREAM_END)
            self._run_manager.remove_run(self.run_id)
            logger.info(
                "Run %s errored (%d execution_steps)",
                self.run_id,
                len(self.execution_steps),
            )
            return

        if self.report:
            await self._queue_insert_message(
                "assistant_report",
                json.dumps(self.report),
            )
            logger.info("Persisted report for conversation %s", self.conversation_id)

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
            self._current_step["step_version"] = self._current_step.get("step_version", 1) + 1
            stopped_node = self._current_step.get("node", "unknown")

            step_copy = dict(self._current_step)
            try:
                from moira.persistence.write_queue import AsyncWriteQueue

                write_queue = cast(
                    AsyncWriteQueue,
                    self._run_manager._get_service("write_queue"),
                )
                write_future = write_queue.enqueue(lambda: self._persist_step(step_copy))
                await asyncio.shield(write_future)
            except asyncio.CancelledError:
                pass
            self.execution_steps.append(self._current_step)
            self._current_step = None

        self.status = "stopped"
        self._finalize_metrics()
        self._mark_state_changed()
        try:
            await asyncio.shield(self._persist())
        except asyncio.CancelledError:
            pass

        self._broadcast_run_snapshot()
        logger.info(
            "Run %s stopped by user at node %s",
            self.run_id,
            stopped_node,
        )

    def _handle_state_values(self, state: dict) -> None:
        """Extract knowledge from the full graph state after a node executes.

        Called when ``stream_mode='values'`` delivers the accumulated state.
        Computes a knowledge summary and broadcasts a snapshot so the
        KnowledgePanel updates live during the run.
        """
        raw_knowledge = state.get("knowledge")
        if not raw_knowledge or not isinstance(raw_knowledge, dict):
            return
        new_summary = knowledge_summary(raw_knowledge)
        if new_summary != self.knowledge:
            self.knowledge = new_summary
            self._mark_state_changed()
            self._broadcast_run_snapshot()

    def _handle_event(self, event_type: str, payload: dict) -> None:
        """Process a single graph event: update accumulated state,
        persist if needed, broadcast to subscribers."""
        persist = False
        snapshot_changed = False

        if event_type == "node_start":
            if self._current_step:
                self._current_step["status"] = "completed"
                self._current_step["step_version"] = self._current_step.get("step_version", 1) + 1
                self.execution_steps.append(self._current_step)
            self._step_started_at = time.monotonic()
            self._current_step = {
                "id": str(self._next_step_ordinal),
                "node": payload.get("node", ""),
                "label": STAGE_LABELS.get(payload.get("node", ""), payload.get("node", "")),
                "status": "running",
                "cost": 0,
                "budget_remaining": self.budget_remaining,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": 0,
                "tool_call_count": 0,
                "step_version": 1,
            }
            self._next_step_ordinal += 1
            payload["started_at"] = self._current_step["started_at"]
            self._mark_state_changed()
            snapshot_changed = True

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
                self._current_step["tool_call_count"] = self._step_tool_call_count(
                    self._current_step
                )
                self._current_step["step_version"] = self._current_step.get("step_version", 1) + 1
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
            self._mark_state_changed()
            persist = True
            snapshot_changed = True

        elif event_type == "tool_result":
            tool_entry = {
                "tool": payload.get("tool", ""),
                "args": payload.get("args"),
                "result": payload.get("output", ""),
                "duration_ms": payload.get("duration_ms", 0),
                "success": payload.get("success", False),
            }

            if self._current_step:
                if "detail" not in self._current_step:
                    self._current_step["detail"] = {}
                if "tool_results" not in self._current_step["detail"]:
                    self._current_step["detail"]["tool_results"] = []
                self._current_step["detail"]["tool_results"].append(tool_entry)
                self._current_step["tool_call_count"] = len(
                    self._current_step["detail"]["tool_results"]
                )
                self._current_step["step_version"] = self._current_step.get("step_version", 1) + 1

            self._mark_state_changed()
            persist = True
            snapshot_changed = True

        elif event_type == "run_complete":
            if payload.get("report"):
                self.report = payload["report"]
            if payload.get("knowledge"):
                self.knowledge = payload["knowledge"]
            self.status = "completed"
            self._finalize_metrics()
            self._mark_state_changed()
            persist = True
            snapshot_changed = True

        elif event_type == "run_error":
            self.error = payload.get("error", "")
            self.status = "error"
            if self._current_step:
                self._current_step["status"] = "error"
                self._current_step["error"] = self.error
                if payload.get("detail"):
                    if "detail" not in self._current_step:
                        self._current_step["detail"] = {}
                    self._current_step["detail"].update(payload["detail"])
                if payload.get("budget_remaining") is not None:
                    prev = self._current_step["budget_remaining"]
                    self._current_step["budget_remaining"] = payload["budget_remaining"]
                    self._current_step["cost"] = prev - payload["budget_remaining"]
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
                self._current_step["elapsed_ms"] = (
                    int((time.monotonic() - self._step_started_at) * 1000)
                    if self._step_started_at
                    else 0
                )
                self._current_step["step_version"] = self._current_step.get("step_version", 1) + 1
                # Persist the failed step to the workflow_steps table so
                # the error detail (including model response) is available
                # for post-mortem debugging.  Without this, failed steps
                # vanish from the DB — only node_end calls _persist_step.
                from moira.persistence.write_queue import AsyncWriteQueue

                write_queue = cast(
                    AsyncWriteQueue,
                    self._run_manager._get_service("write_queue"),
                )
                step_copy = dict(self._current_step)
                write_queue.enqueue(lambda: self._persist_step(step_copy))
                self.execution_steps.append(self._current_step)
                self._current_step = None
            self._finalize_metrics()
            self._mark_state_changed()
            persist = True
            snapshot_changed = True

        if persist:
            asyncio.create_task(self._persist())

        if snapshot_changed:
            self._broadcast_run_snapshot()

    def _finalize_metrics(self) -> None:
        """Compute final timing metrics."""
        self.total_elapsed_ms = int((time.monotonic() - self._run_started_at) * 1000)
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.budget_consumed = self.budget_limit - self.budget_remaining

    async def _queue_insert_message(self, role: str, content: str) -> None:
        """Insert a conversation message through the write queue so it
        is serialized with all other SQLite writes."""
        from moira.persistence.write_queue import AsyncWriteQueue

        write_queue = cast(
            AsyncWriteQueue,
            self._run_manager._get_service("write_queue"),
        )
        await write_queue.enqueue(
            lambda: self._conversation_repo.insert_message(
                self.conversation_id,
                role,
                content,
            )
        )

    async def _persist(self) -> None:
        """Upsert run-level metadata as a WorkflowRun."""
        from moira.persistence.write_queue import AsyncWriteQueue

        async with self._persist_lock:
            run = WorkflowRun(
                id=self.run_id,
                conversation_id=self.conversation_id,
                user_message_id=self.user_message_id,
                status=self.status,
                budget_limit=float(self.budget_limit),
                total_cost=self.budget_consumed,
                generation_reason=(self.report or {}).get("generation_reason", ""),
                started_at=self.started_at,
                completed_at=self.completed_at or None,
                total_elapsed_ms=self.total_elapsed_ms or None,
                updated_at=self.updated_at,
                knowledge_snapshot=json.dumps(self.knowledge) if self.knowledge else "",
                state_version=self.state_version,
                report=dict(self.report) if self.report else None,
                thread_id=self.thread_id,
            )
            write_queue = cast(
                AsyncWriteQueue,
                self._run_manager._get_service("write_queue"),
            )
            await write_queue.enqueue(lambda: self._conversation_repo.save_workflow_run(run))
            logger.debug(
                "Persisted run %s (status=%s, %d steps)",
                self.run_id,
                self.status,
                len(self.execution_steps),
            )

    async def _persist_step(self, step_dict: dict) -> None:
        """Write a completed step to the workflow_steps table and upsert
        aggregated metrics into inference_metrics.

        Called exclusively through ``AsyncWriteQueue`` — lets
        ``sqlite3.OperationalError`` propagate so the queue retries
        on lock contention.  Metrics recording is non-critical and
        swallows its own errors so a metrics failure never prevents
        the queue from marking the step write as successful."""
        from moira.persistence.interfaces import WorkflowStep

        step_repo = cast(
            type,
            self._run_manager._get_service("workflow_step_repository"),
        )
        detail = step_dict.get("detail")
        tool_call_count = step_dict.get("tool_call_count")
        if tool_call_count is None and isinstance(detail, dict):
            tool_results = detail.get("tool_results")
            if isinstance(tool_results, list):
                tool_call_count = len(tool_results)
        if tool_call_count is None:
            tool_call_count = 0
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
            step_version=step_dict.get("step_version", 1),
            tool_call_count=tool_call_count,
            detail=detail if isinstance(detail, dict) else None,
        )
        await step_repo.save_step(wf_step)

        try:
            await self._record_inference_metrics(step_dict)
        except Exception:
            logger.debug(
                "Failed to record inference metrics for run %s",
                self.run_id,
                exc_info=True,
            )

    async def _record_inference_metrics(self, step_dict: dict) -> None:
        """Upsert step-level inference counters into inference_metrics.

        Non-critical — callers should catch and swallow exceptions.
        Called from within the write queue consumer (via ``_persist_step``),
        so no need to re-enqueue; the write is already serialized."""
        model = step_dict.get("model")
        purpose = step_dict.get("purpose")
        if not model or not purpose:
            return

        from datetime import datetime as dt
        from datetime import timezone

        started_at = step_dict.get("started_at", "")
        if len(started_at) >= 13:
            period_hour = started_at[:13] + ":00"
        else:
            period_hour = dt.now(timezone.utc).strftime("%Y-%m-%dT%H:00")

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

    def _broadcast(self, event: Any) -> None:
        """Put an event into all subscriber queues. Drops the oldest event
        if a queue is full rather than blocking the graph task."""
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                queue.get_nowait()
                queue.put_nowait(event)
