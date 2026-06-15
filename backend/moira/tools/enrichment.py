"""Tool description enrichment for better LanceDB search quality.

Generates richer descriptions that describe what questions a tool answers
and what facts it provides, rather than just how to call it.  The enriched
text replaces the tool's ``description`` for embedding purposes; the original
is preserved in ``original_description`` so it can be restored if needed.

Enrichment runs once per tool.  On subsequent startups the existing enriched
description is preserved (detected via ``original_description`` being
populated).  If the source description changes in code, enrichment re-runs
automatically (see ``service_setup.py`` startup logic).
"""

import logging
from typing import cast

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.prompts import get_prompt
from moira.service_setup import service_provider
from moira.tools.base import ToolDefinition

logger = logging.getLogger(__name__)


def _format_parameter_list(tool: ToolDefinition) -> str:
    """Extract parameter names from the argument schema."""
    param_names = list(tool.argument_schema.get("properties", {}).keys())
    return ", ".join(param_names) if param_names else "(none)"


async def _enrich_single(
    tool: ToolDefinition,
    resolved: object,
) -> str | None:
    """Send a single tool to the model and return the enriched description.

    Returns the enriched text, or None on any failure (model error, empty
    response, etc.).
    """
    user_prompt = get_prompt("tool_enrichment.user").format(
        tool_name=tool.name,
        tool_description=tool.description[:500],
        tool_parameters=_format_parameter_list(tool),
    )

    try:
        raw = await resolved.client.chat_completion(
            model=resolved.model_id,
            messages=[
                {"role": "system", "content": get_prompt("tool_enrichment.system")},
                {"role": "user", "content": user_prompt},
            ],
            temperature=DEFAULT_TEMPERATURE,
        )
    except Exception as e:
        logger.warning("Enrichment call failed for '%s': %s", tool.name, e)
        return None

    content = raw.content if hasattr(raw, "content") else str(raw)
    content = content.strip()

    if not content:
        logger.warning("Enrichment for '%s' returned empty content", tool.name)
        return None

    return content


async def enrich_tool_descriptions(
    tools: list[ToolDefinition],
) -> dict[str, str]:
    """Generate enriched descriptions for tools via the task model.

    Each tool is enriched in its own model call.  This keeps prompts small
    enough for task models with limited context windows and avoids JSON
    parsing issues with small models.

    Args:
        tools: ToolDefinitions to enrich.

    Returns:
        Dict mapping tool name to enriched description string.  Tools
        that fail enrichment are silently omitted so callers can fall
        back to the original description.
    """
    from moira.inference.registry import ModelRegistry

    try:
        registry = cast(ModelRegistry | None, service_provider("model_registry"))
    except RuntimeError:
        logger.debug("Model registry not available, skipping enrichment")
        return {}

    if registry is None:
        logger.debug("No model registry available, skipping enrichment")
        return {}

    try:
        resolved = await registry.resolve("task")
    except (ValueError, KeyError) as e:
        logger.debug("Task model not available for enrichment: %s", e)
        return {}

    merged: dict[str, str] = {}
    for tool in tools:
        enriched = await _enrich_single(tool, resolved)
        if enriched:
            merged[tool.name] = enriched

    logger.info("Enriched %d/%d tool descriptions", len(merged), len(tools))
    return merged
