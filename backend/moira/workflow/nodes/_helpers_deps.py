"""Node helper functions that depend on ``langgraph``, ``langchain_core``, or
``moira.inference.client`` (which depends on httpx).

Split from ``_helpers`` so that the pure functions (JSON parsing,
formatting, datetime) in ``_helpers`` can be imported by ``moira_eval``
without pulling in those heavier dependencies.
"""

import logging
from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.client import ChatResponse, InferenceClient
from moira.workflow.nodes._helpers import _now, _parse_json_object

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


def _get_model(config: RunnableConfig):
    """Resolve the intelligence model from the registry."""
    from moira.inference.registry import ModelRegistry
    from moira.service_setup import service_provider

    registry = cast(ModelRegistry, service_provider("model_registry"))
    return registry


async def _resolve_intelligence(config: RunnableConfig):
    """Resolve the intelligence model, respecting per-conversation overrides.

    Extracts conversation_id from the graph config and passes it to
    resolve() so that a conversation-level model override takes precedence
    over the global default."""
    registry = _get_model(config)
    conversation_id = config.get("configurable", {}).get("conversation_id", "")
    return await registry.resolve("intelligence", conversation_id=conversation_id)


async def _call_for_json(
    client: InferenceClient,
    model_id: str,
    messages: list[dict],
    required_key: str,
    node_name: str,
    *,
    temperature: float = 0.7,
) -> tuple[dict, ChatResponse | None, int]:
    """Call the model and parse JSON, retrying once on parse failure.

    Returns ``(parsed_dict, response, call_count)``.

    On the first attempt, calls the model with the original messages.
    If the response doesn't yield valid JSON (or lacks *required_key*),
    retries once with a repair instruction appended to the conversation.

    If both attempts fail, returns ``({}, response, 2)`` — the caller
    is responsible for raising an error or providing a safe default.
    """
    from moira.prompts import render_prompt

    call_count = 0
    response = None

    for attempt in range(2):
        call_count += 1
        response = await client.chat_completion(
            messages=messages,
            model=model_id,
            temperature=temperature,
        )
        raw = response.content or ""
        parsed = _parse_json_object(raw)

        if parsed and required_key in parsed:
            logger.info(
                "%s: JSON parsed successfully on attempt %d",
                node_name.upper(),
                call_count,
            )
            return parsed, response, call_count

        logger.warning(
            "%s: JSON parse failed on attempt %d (parsed=%d keys, response=%d chars, has_key=%s)",
            node_name.upper(),
            call_count,
            len(parsed),
            len(raw),
            required_key in parsed if parsed else False,
        )

        if attempt == 0:
            messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {"role": "user", "content": render_prompt("json_repair.user")},
            ]

    return {}, response, call_count
