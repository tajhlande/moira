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


def _fix_json_control_chars(text: str) -> str:
    """Escape literal control characters inside JSON string values.

    Quantized local models sometimes emit raw newline, carriage return, or
    tab bytes *inside* JSON string values instead of the proper ``\\n`` /
    ``\\r`` / ``\\t`` escape sequences.  Python's ``json.loads`` rejects
    these as "Invalid control character".

    This function performs a single-pass scan that tracks whether the cursor
    is inside a JSON string (between unescaped double quotes) and replaces
    any literal control characters found there with their escape-sequence
    equivalents.

    Characters outside strings (structural whitespace between JSON tokens)
    are left untouched — they are valid JSON whitespace.

    On already-valid JSON this is a no-op: properly escaped ``\\n`` sequences
    are seen as ``\\`` + ``n`` (escape mode), not as a literal control char.
    """
    result: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            result.append(ch)
            escaped = False
            continue
        if ch == "\\":
            result.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string:
            if ch == "\n":
                result.append("\\n")
            elif ch == "\r":
                result.append("\\r")
            elif ch == "\t":
                result.append("\\t")
            else:
                result.append(ch)
        else:
            result.append(ch)
    return "".join(result)


def _parse_json_object(text: str) -> dict:
    """Extract the first JSON object from text, handling markdown fences.

    Also strips <think>...</think> blocks and standalone </think> tags that
    some models leak into the response content, and repairs literal control
    characters inside JSON strings (common with quantized models).
    """
    import json
    import re

    text = text.strip()
    # Strip <think>...</think> blocks and standalone closing tags
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"</think>", "", text).strip()
    # Repair literal control characters inside JSON string values.
    # No-op on valid JSON; fixes broken JSON from quantized models.
    text = _fix_json_control_chars(text)
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
    # Find all { ... } matches and try longest first.  Models sometimes
    # embed small JSON fragments (e.g. {"query": "..."}) before or after
    # the real response object; trying the longest match first avoids
    # extracting the wrong fragment.
    matches = list(
        re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    )
    matches.sort(key=lambda m: len(m.group(0)), reverse=True)
    for match in matches:
        try:
            result = json.loads(match.group(0))
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            continue
    logger.warning(
        "Failed to extract JSON object from model output "
        "(len=%d, first 200 chars: %s)",
        len(text),
        text[:200],
    )
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
