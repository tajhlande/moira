import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, cast

from langgraph.graph.state import CompiledStateGraph, RunnableConfig

from moira.config import MoiraConfig
from moira.persistence.interfaces import ConversationRepository, WorkflowRun

logger = logging.getLogger(__name__)

STAGE_LABELS = {
    "decomposition": "Analyzing question",
    "tool_identification": "Identifying tools",
    "planning": "Planning research approach",
    "research": "Researching",
    "synthesis": "Synthesizing conclusions",
    "verification": "Verifying facts and conclusions",
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
        budget_update, verification_report, run_complete)."""
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
                ):
                    if key in payload:
                        self._current_step[key] = payload[key]
                self._current_step["elapsed_ms"] = (
                    int((time.monotonic() - self._step_started_at) * 1000)
                    if self._step_started_at
                    else 0
                )
                self._current_step["step_version"] = self._current_step.get("step_version", 1) + 1
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
                generation_path="",
                started_at=self.started_at,
                completed_at=self.completed_at or None,
                total_elapsed_ms=self.total_elapsed_ms or None,
                updated_at=self.updated_at,
                knowledge_snapshot=json.dumps(self.knowledge) if self.knowledge else "",
                state_version=self.state_version,
                report=dict(self.report) if self.report else None,
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
                    "data": json.dumps({"conversation_id": cid, "status": "running"}),
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
        graph_config: RunnableConfig,
        config: MoiraConfig,
        conversation_repo: ConversationRepository,
    ) -> str:
        """Create an ActiveRun, persist initial state, launch the graph.
        Returns the run_id."""
        run_id = str(uuid.uuid4())
        thread_id = graph_config.get("configurable", {}).get("thread_id", run_id)
        started_at = datetime.now(timezone.utc).isoformat()
        raw_budget_limit = initial_state.get("execution_state", {}).get("budget_limit")
        if isinstance(raw_budget_limit, (int, float)):
            budget_limit = float(raw_budget_limit)
        else:
            budget_limit = float(config.budget.default_limit)

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
        graph_config = RunnableConfig(graph_config)
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
                "data": json.dumps({"conversation_id": conversation_id, "status": "running"}),
            }
        )
        return run_id

    async def _resolve_cost_weights(
            self, config: MoiraConfig
    ) -> dict[str, int | float | bool | str]:
        """Resolve cost weights from the settings service with config fallback.

        Ensures resumed runs have cost_weights available even if the
        original checkpoint predates the cost_weights field."""
        try:
            from moira.persistence.interfaces import SCOPE_SYSTEM, SYSTEM_SCOPE_ID
            from moira.services.settings.settings_service import SettingsService

            settings_svc = cast(SettingsService, self._get_service("settings_service"))
            return await settings_svc.get_typed_prefix(
                "budget.cost.", scope=SCOPE_SYSTEM, scope_id=SYSTEM_SCOPE_ID
            )
        except Exception:
            logger.debug("Settings service unavailable, falling back to config for cost_weights")
            cw = config.budget.cost_weights
            return {
                "decomposition": cw.decomposition,
                "tool_identification": cw.tool_identification,
                "planning": cw.planning,
                "research": cw.research,
                "synthesis": cw.synthesis,
                "verification": cw.verification,
                "report_generation": cw.report_generation,
            }

    async def resume_run(
        self,
        conversation_id: str,
        config: MoiraConfig,
        conversation_repo: ConversationRepository,
    ) -> str:
        """Resume a previously stopped or errored run. Loads the thread_id
        from the persisted WorkflowRun, creates a new ActiveRun, and starts
        the graph from the checkpoint using ``Command(resume=True)``.
        Returns the new run_id."""
        from langgraph.graph.state import CompiledStateGraph
        from langgraph.types import Command

        runs = await conversation_repo.get_workflow_runs(conversation_id)
        resumable_run = None
        for run in reversed(runs):
            if run.status in ("stopped", "error"):
                resumable_run = run
                break
        if resumable_run is None:
            raise ValueError(f"No stopped or errored run found for conversation {conversation_id}")

        graph = cast(CompiledStateGraph, self._get_service("research_graph"))

        # Resolve cost_weights so resumed runs always have them, even if
        # the checkpoint predates the cost_weights field addition.
        cost_weights = await self._resolve_cost_weights(config)

        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()

        active_run = ActiveRun(
            run_id=run_id,
            conversation_id=conversation_id,
            user_message_id=resumable_run.user_message_id,
            thread_id=resumable_run.thread_id,
            started_at=started_at,
            budget_limit=float(resumable_run.budget_limit),
            conversation_repo=conversation_repo,
            run_manager=self,
        )
        active_run.budget_remaining = resumable_run.budget_limit - resumable_run.budget_consumed
        active_run.budget_consumed = resumable_run.budget_consumed

        self._active_runs[run_id] = active_run
        self._conversation_runs[conversation_id] = run_id

        graph_config = RunnableConfig({
            "configurable": {
                "thread_id": resumable_run.thread_id,
                "run_id": run_id,
                "moira_config": config,
            },
        })

        await active_run._persist()

        # Inject cost_weights via Command.update so the checkpoint state
        # always has them, regardless of when the original run was created.
        active_run.start_graph_resume(
            graph,
            Command(resume=True, update={
                "execution_state": {"step_costs": cost_weights},
            }),
            graph_config,
        )
        logger.info(
            "Resumed run %s for conversation %s (thread %s, previous status=%s)",
            run_id,
            conversation_id,
            resumable_run.thread_id,
            resumable_run.status,
        )
        self._broadcast_global(
            {
                "event": "run_status",
                "data": json.dumps({"conversation_id": conversation_id, "status": "running"}),
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
