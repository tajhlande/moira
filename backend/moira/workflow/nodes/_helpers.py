"""Shared utilities for research loop nodes."""

import logging
from datetime import datetime, timezone
from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

logger = logging.getLogger(__name__)


def _check_stop(node: str, config: RunnableConfig) -> None:
    """Cooperative stop check for user-initiated run cancellation."""
    run_id = config.get("configurable", {}).get("run_id")
    if not run_id:
        return

    from moira.service_setup import service_provider
    from moira.workflow.run_manager import RunManager

    run_mgr = cast(RunManager, service_provider("run_manager"))
    active_run = run_mgr._active_runs.get(run_id)
    if active_run is None or not active_run._stop_requested:
        return

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})
    logger.info("Stop requested at node %s, calling interrupt()", node)
    from langgraph.types import interrupt

    interrupt("user_stop")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_model(config: RunnableConfig):
    """Resolve the intelligence model from the registry."""
    from moira.inference.registry import ModelRegistry
    from moira.service_setup import service_provider

    registry = cast(ModelRegistry, service_provider("model_registry"))
    return registry


def _parse_json_object(text: str) -> dict:
    """Extract the first JSON object from text, handling markdown fences."""
    import json
    import re

    text = text.strip()
    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    # Strip markdown fences
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        try:
            result = json.loads(fenced.group(1).strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    # Find first { ... }
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _response_meta(response) -> dict:
    """Extract inference metadata from a ChatResponse for node_end payload.

    Captures token counts and timing so they get persisted to the
    workflow_steps row via the run_manager event handler.
    """
    return {
        "input_tokens": getattr(response, "input_tokens", None),
        "output_tokens": getattr(response, "output_tokens", None),
        "thinking_tokens": getattr(response, "thinking_tokens", None),
        "prompt_time_ms": getattr(response, "prompt_time_ms", None),
        "gen_time_ms": getattr(response, "gen_time_ms", None),
    }
