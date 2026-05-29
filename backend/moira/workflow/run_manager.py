import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

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
        self.verification_attempts: list[dict] = []
        self.thinking_traces: dict[str, str] = {}
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

        # Completed execution steps
        for step in self.execution_steps:
            payload = {
                "node": step["node"],
                "budget_remaining": step["budget_remaining"],
                "started_at": step.get("started_at", ""),
                "elapsed_ms": step.get("elapsed_ms", 0),
            }
            events.append({"event": "node_start", "data": json.dumps(payload)})
            events.append({"event": "node_end", "data": json.dumps(payload)})

        # Currently running step (started but not yet ended)
        if self._current_step:
            payload = {
                "node": self._current_step["node"],
                "started_at": self._current_step.get("started_at", ""),
            }
            events.append({"event": "node_start", "data": json.dumps(payload)})

        # Tool executions
        for tool in self.tool_executions:
            events.append(
                {
                    "event": "tool_result",
                    "data": json.dumps(
                        {
                            "tool": tool["tool"],
                            "result": tool["result"],
                            "duration_ms": tool["duration_ms"],
                            "success": tool["success"],
                        }
                    ),
                }
            )

        # Verification attempts
        for attempt in self.verification_attempts:
            events.append(
                {
                    "event": "verification_report",
                    "data": json.dumps(attempt),
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

    async def _run_graph(
        self,
        graph: CompiledStateGraph,
        initial_state: dict,
        graph_config: dict,
    ) -> None:
        """Iterate graph.astream(), accumulate state, persist incrementally,
        and broadcast events to subscribers."""
        try:
            # Seed subscribers with initial budget state
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

            # Graph completed normally — if no explicit run_complete was
            # emitted, finalize as completed anyway.
            if self.status == "running":
                self.status = "completed"
                self._finalize_metrics()
                await self._persist()

        except Exception as e:
            logger.error(
                "Graph run error for conversation %s: %s: %s",
                self.conversation_id,
                type(e).__name__,
                e,
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

        # Persist the assistant response message.
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

        # Signal end-of-stream to all subscribers, then remove from RunManager.
        self._broadcast(_STREAM_END)
        self._run_manager.remove_run(self.run_id)
        logger.info(
            "Run %s finished (%s, %d execution_steps)",
            self.run_id,
            self.status,
            len(self.execution_steps),
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
                self.execution_steps.append(self._current_step)
                self._current_step = None
            payload["elapsed_ms"] = elapsed_ms
            self.budget_consumed = self.budget_limit - self.budget_remaining
            persist = True

        elif event_type == "tool_result":
            self.tool_executions.append(
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
            persist = True

        elif event_type == "verification_report":
            self.verification_attempts.append(
                {
                    "report": payload.get("report", {}),
                    "attempt": payload.get("attempt", 0),
                }
            )
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

        # Merge thinking traces from graph state when available
        if event_type == "node_end" and payload.get("thinking_traces"):
            self.thinking_traces.update(payload["thinking_traces"])

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
        """Upsert the current accumulated state as a WorkflowRun."""
        run = WorkflowRun(
            id=self.run_id,
            conversation_id=self.conversation_id,
            user_message_id=self.user_message_id,
            thread_id=self.thread_id,
            execution_steps=self.execution_steps,
            tool_executions=self.tool_executions,
            verification_attempts=self.verification_attempts,
            thinking_traces=self.thinking_traces,
            report=self.report,
            budget_limit=float(self.budget_limit),
            budget_consumed=self.budget_consumed,
            error=self.error,
            status=self.status,
            started_at=self.started_at,
            completed_at=self.completed_at or None,
            total_elapsed_ms=self.total_elapsed_ms or None,
        )
        await self._conversation_repo.save_workflow_run(run)
        logger.debug(
            "Persisted run %s (status=%s, %d steps)",
            self.run_id,
            self.status,
            len(self.execution_steps),
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

        # Persist initial state so the run is visible immediately
        await active_run._persist()

        active_run.start_graph(graph, initial_state, graph_config)
        logger.info("Started run %s for conversation %s", run_id, conversation_id)
        return run_id

    def get_active_run(self, conversation_id: str) -> ActiveRun | None:
        """Look up the active run for a conversation. Returns None if no
        run is in-flight."""
        run_id = self._conversation_runs.get(conversation_id)
        if run_id is None:
            return None
        return self._active_runs.get(run_id)

    def remove_run(self, run_id: str) -> None:
        """Called by ActiveRun when the graph completes. Cleans up both dicts."""
        active_run = self._active_runs.pop(run_id, None)
        if active_run:
            self._conversation_runs.pop(active_run.conversation_id, None)
