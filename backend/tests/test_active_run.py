"""Tests for ActiveRun event accumulation.

These tests cover the persistence of per-tool-call attribution metadata:
the ``tool_result`` handler must carry ``request_id`` from the event
payload into the step's ``detail.tool_results`` entries so attribution
survives in workflow_steps (research pops request_id out of tool args
before execution — the event payload is the only place it exists).
"""

import asyncio

from moira.workflow.active_run import ActiveRun


def _make_run() -> ActiveRun:
    """Build an ActiveRun with persistence/broadcast stubbed out.

    ``_handle_event`` schedules ``_persist`` via ``asyncio.create_task``
    and calls ``_broadcast_run_snapshot`` on tool_result events; both are
    replaced with no-ops so the accumulator can be tested in isolation.
    """
    run = ActiveRun(
        run_id="run-test",
        conversation_id="conv-test",
        user_message_id=1,
        thread_id="thread-test",
        started_at="2026-08-25T00:00:00+00:00",
        budget_limit=10.0,
        conversation_repo=None,
        run_manager=None,
    )

    async def _noop_persist() -> None:
        return None

    run._persist = _noop_persist  # type: ignore[method-assign]
    run._broadcast_run_snapshot = lambda: None  # type: ignore[method-assign]
    return run


def _handle(run: ActiveRun, event_type: str, payload: dict) -> None:
    """Run _handle_event inside a loop (tool_result schedules a task)."""
    asyncio.get_event_loop_policy()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        run._handle_event(event_type, payload)
        return
    asyncio.run(_handle_async(run, event_type, payload))


async def _handle_async(run: ActiveRun, event_type: str, payload: dict) -> None:
    run._handle_event(event_type, payload)
    # Give create_task(_persist) a chance to run (it's a no-op stub).
    await asyncio.sleep(0)


class TestToolResultAttribution:
    def test_tool_result_entry_carries_request_id(self):
        run = _make_run()
        _handle(run, "node_start", {"node": "research"})
        _handle(
            run,
            "tool_result",
            {
                "tool": "web_search",
                "args": {"query": "tariff manufacturing employment"},
                "output": "3 results",
                "duration_ms": 120,
                "success": True,
                "request_id": "req0001",
            },
        )
        entries = run._current_step["detail"]["tool_results"]
        assert len(entries) == 1
        assert entries[0]["request_id"] == "req0001"
        assert entries[0]["tool"] == "web_search"
        assert entries[0]["result"] == "3 results"

    def test_tool_result_entry_request_id_defaults_to_none(self):
        run = _make_run()
        _handle(run, "node_start", {"node": "research"})
        _handle(
            run,
            "tool_result",
            {
                "tool": "web_search",
                "args": {"query": "unattributed call"},
                "output": "0 results",
                "duration_ms": 50,
                "success": True,
            },
        )
        entries = run._current_step["detail"]["tool_results"]
        assert entries[0]["request_id"] is None

    def test_tool_call_count_tracks_entries(self):
        run = _make_run()
        _handle(run, "node_start", {"node": "research"})
        for i in range(3):
            _handle(
                run,
                "tool_result",
                {
                    "tool": "web_search",
                    "args": {"query": f"q{i}"},
                    "output": "",
                    "duration_ms": 1,
                    "success": True,
                    "request_id": f"req000{i + 1}",
                },
            )
        assert run._current_step["tool_call_count"] == 3
