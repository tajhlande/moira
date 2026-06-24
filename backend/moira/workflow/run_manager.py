"""Process-level singleton that manages active graph runs.

Registered in service_setup.py and retrieved via service_provider("run_manager").
Tracks runs by both run_id and conversation_id (one active run per conversation).

Split from active_run.py — ActiveRun handles single-run lifecycle,
RunManager handles the run registry and global subscriber broadcasting.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import cast

from langgraph.graph.state import CompiledStateGraph, RunnableConfig

from moira.config import MoiraConfig
from moira.persistence.interfaces import ConversationRepository
from moira.workflow.active_run import _STREAM_END, ActiveRun

logger = logging.getLogger(__name__)


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
                "research_review": cw.research_review,
                "evaluation": cw.evaluation,
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

        graph_config = RunnableConfig(
            {
                "configurable": {
                    "thread_id": resumable_run.thread_id,
                    "run_id": run_id,
                    "moira_config": config,
                },
            }
        )

        await active_run._persist()

        # Inject cost_weights via Command.update so the checkpoint state
        # always has them, regardless of when the original run was created.
        active_run.start_graph_resume(
            graph,
            Command(
                resume=True,
                update={
                    "execution_state": {"step_costs": cost_weights},
                },
            ),
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
