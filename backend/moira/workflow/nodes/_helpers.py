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

    Quantized local models sometimes emit raw control bytes *inside* JSON
    string values instead of the proper escape sequences.  Python's
    ``json.loads`` rejects any character in the U+0000–U+001F range that
    appears inside a string (per RFC 8259 § 7).

    This function performs a single-pass scan that tracks whether the cursor
    is inside a JSON string (between unescaped double quotes) and replaces
    any literal control characters found there with their JSON escape-sequence
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
            # RFC 8259 requires all control chars (U+0000–U+001F) inside
            # strings to be escaped. Use named escapes where JSON defines
            # them; fall back to \uXXXX for the rest.
            code = ord(ch)
            if code == 0x0A:  # line feed
                result.append("\\n")
            elif code == 0x0D:  # carriage return
                result.append("\\r")
            elif code == 0x09:  # tab
                result.append("\\t")
            elif code == 0x08:  # backspace
                result.append("\\b")
            elif code == 0x0C:  # form feed
                result.append("\\f")
            elif code < 0x20:  # other control chars (NUL, VT, etc.)
                result.append(f"\\u{code:04x}")
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
    matches = list(re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL))
    matches.sort(key=lambda m: len(m.group(0)), reverse=True)
    for match in matches:
        try:
            result = json.loads(match.group(0))
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            continue
    logger.warning(
        "Failed to extract JSON object from model output (len=%d, first 200 chars: %s)",
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


# --- Citation content formatting (shared by evaluation and research_review) ---

_PER_CITATION_CONTENT_CAP = 2000
_TOTAL_CITATION_CONTENT_CAP = 32000


def _format_citation_content(citations: list, conclusions: list, facts: list) -> str:
    """Format citation source content for cross-referencing in prompts.

    Applies a per-citation cap and a total budget.  Citations referenced by
    the conclusions under evaluation (traced via conclusion → fact → citation)
    are prioritized; unreferenced citations are included only when budget
    remains.  Citations without ``content`` are skipped silently.
    """
    # Trace conclusion → supporting_fact_ids → fact → citation_ids to find
    # which citations are directly relevant to the conclusions being judged.
    fact_by_id = {f["id"]: f for f in facts}
    referenced_cit_ids: set[str] = set()
    for c in conclusions:
        for fid in c.get("supporting_fact_ids", []):
            fact = fact_by_id.get(fid)
            if fact:
                referenced_cit_ids.update(fact.get("citation_ids", []))

    # Partition into referenced-first, preserving citation list order.
    prioritized: list = []
    for cit in citations:
        if cit["id"] in referenced_cit_ids and cit.get("content"):
            prioritized.append(cit)
    for cit in citations:
        if cit["id"] not in referenced_cit_ids and cit.get("content"):
            prioritized.append(cit)

    lines: list[str] = []
    total = 0
    for cit in prioritized:
        if total >= _TOTAL_CITATION_CONTENT_CAP:
            break
        content = cit["content"][:_PER_CITATION_CONTENT_CAP]
        budget_left = _TOTAL_CITATION_CONTENT_CAP - total
        if len(content) > budget_left:
            content = content[:budget_left]
        lines.append(
            f"[{cit['id']}] {cit['source']} | {cit.get('url') or ''} | "
            f"{cit.get('title') or ''}\n{content}"
        )
        total += len(content)

    return "\n\n".join(lines)
