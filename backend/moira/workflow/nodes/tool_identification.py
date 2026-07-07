"""Tool identification node: matches unknown facts to candidate tools.

For each unknown fact, embeds fact_needed + subject and searches LanceDB
individually, then merges and deduplicates results. No model call needed.
Uses vector search only. Default tools are always included.

Phase C: per-fact LanceDB queries, tool costs and call limits from catalog
are surfaced into execution_state for downstream planning and enforcement.
"""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.models.knowledge import ResearchState
from moira.tools.base import ToolDefinition
from moira.workflow.budget import deduct_cost
from moira.workflow.nodes._helpers import _now
from moira.workflow.nodes._helpers_deps import _check_stop

logger = logging.getLogger(__name__)

NODE_NAME = "tool_identification"


async def tool_identification(state: ResearchState, config: RunnableConfig) -> dict:
    """Identify candidate tools for resolving unknown facts via per-fact
    LanceDB vector search. No model call needed."""
    _check_stop(NODE_NAME, config)
    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": NODE_NAME, "timestamp": _now()}})

    es = state["execution_state"]
    knowledge = state["knowledge"]

    unknown_facts = [f for f in knowledge["facts"] if f["status"] in ("unknown", "contradicted")]

    candidate_tools: list[ToolDefinition] = []
    seen_names: set[str] = set()
    queries_detail: list[dict] = []

    from moira.service_setup import service_provider

    # Per-fact LanceDB search: embed each fact individually and merge results
    try:
        discovery = service_provider("tool_discovery")
        if unknown_facts:
            for fact in unknown_facts:
                query = f"{fact['subject']} {fact['fact_needed']}"
                discovered = await discovery.discover(query)
                top_names = []
                for tool in discovered:
                    if tool.name not in seen_names:
                        candidate_tools.append(tool)
                        seen_names.add(tool.name)
                    if tool.name not in top_names:
                        top_names.append(tool.name)
                queries_detail.append(
                    {
                        "fact_id": fact["id"],
                        "query": query,
                        "top_results": top_names[:5],
                    }
                )
    except Exception:
        logger.warning("Tool discovery failed, proceeding with defaults only", exc_info=True)

    # Always include default tools
    default_names: list[str] = []
    try:
        catalog = service_provider("tool_catalog")
        defaults = catalog.get_default_tools()
        for tool in defaults:
            if tool.name not in seen_names:
                candidate_tools.append(tool)
                seen_names.add(tool.name)
            default_names.append(tool.name)
    except Exception:
        logger.debug("Tool catalog unavailable for defaults")

    # Re-hydrate candidate tools from the catalog.  LanceDB search returns
    # truncated ToolDefinitions (name + description only); the catalog has
    # full definitions from SQLite including argument_schema,
    # invocation_cost, and call_limit_per_run.
    try:
        catalog = service_provider("tool_catalog")
        rehydrated: list[ToolDefinition] = []
        for tool in candidate_tools:
            full = catalog.get(tool.name)
            if isinstance(full, ToolDefinition) and full.argument_schema:
                rehydrated.append(full)
            else:
                rehydrated.append(tool)
        candidate_tools = rehydrated
    except Exception:
        logger.debug("Tool catalog unavailable for re-hydration")

    new_budget = deduct_cost(es["step_costs"], NODE_NAME, es["budget_remaining"])

    # Build tool_costs and tool_call_limits from candidate tools so
    # downstream planning and research nodes have the data even if
    # streaming.py did not populate them (e.g. tests, direct invocation).
    tool_costs = {t.name: t.invocation_cost for t in candidate_tools}
    tool_call_limits = {
        t.name: t.call_limit_per_run
        for t in candidate_tools
        if t.call_limit_per_run and t.call_limit_per_run > 0
    }

    detail = {
        "queries": queries_detail,
        "candidate_tools": [t.name for t in candidate_tools],
        "default_tools_included": default_names,
    }

    writer(
        {
            "event": "node_end",
            "payload": {
                "node": NODE_NAME,
                "budget_remaining": new_budget,
                "detail": detail,
            },
        }
    )
    logger.info(
        "TOOL IDENTIFICATION Complete (%d candidates for %d facts)",
        len(candidate_tools),
        len(unknown_facts),
    )

    # Merge tool_costs/tool_call_limits with any pre-existing values from
    # initial_state so tools not in the candidate set (but present in the
    # catalog) are still tracked.
    merged_tool_costs = {**es.get("tool_costs", {}), **tool_costs}
    merged_call_limits = {**es.get("tool_call_limits", {}), **tool_call_limits}

    return {
        "execution_state": {
            **es,
            "candidate_tools": candidate_tools,
            "budget_remaining": new_budget,
            "tool_costs": merged_tool_costs,
            "tool_call_limits": merged_call_limits,
        },
    }
