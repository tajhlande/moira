"""Tool identification node: matches unknown facts to candidate tools.

For each unknown fact, embeds fact_needed + subject and searches LanceDB.
No model call needed. Uses vector search only. Default tools are always
included. Tool costs are attached to candidate tools.

Phase A: uses LanceDB search (same as current). Phase C adds enriched
descriptions and fact-based query construction."""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.models.knowledge import ResearchState
from moira.tools.base import ToolDefinition
from moira.workflow.budget import deduct_cost
from moira.workflow.nodes._helpers import _check_stop, _now

logger = logging.getLogger(__name__)

NODE_NAME = "tool_identification"


async def tool_identification(state: ResearchState, config: RunnableConfig) -> dict:
    """Identify candidate tools for resolving unknown facts via LanceDB
    vector search. No model call needed."""
    _check_stop(NODE_NAME, config)
    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": NODE_NAME, "timestamp": _now()}})

    es = state["execution_state"]
    knowledge = state["knowledge"]

    # Only search for facts that are still unknown or contradicted
    unknown_facts = [
        f for f in knowledge["facts"]
        if f["status"] in ("unknown", "contradicted")
    ]

    candidate_tools: list[ToolDefinition] = []

    from moira.service_setup import service_provider

    # Search LanceDB for each unknown fact
    try:
        discovery = service_provider("tool_discovery")
        if unknown_facts:
            # Combine all fact_needed + subject into a single query for
            # broad tool discovery. Phase C will do per-fact queries.
            query_parts = [
                f"{f['subject']} {f['fact_needed']}" for f in unknown_facts
            ]
            combined_query = ". ".join(query_parts)
            discovered = await discovery.discover(combined_query)
            seen = set()
            for tool in discovered:
                if tool.name not in seen:
                    candidate_tools.append(tool)
                    seen.add(tool.name)
    except Exception:
        logger.warning("Tool discovery failed, proceeding with defaults only", exc_info=True)

    # Always include default tools
    try:
        catalog = service_provider("tool_catalog")
        defaults = catalog.get_default_tools()
        existing_names = {t.name for t in candidate_tools}
        for tool in defaults:
            if tool.name not in existing_names:
                candidate_tools.append(tool)
    except Exception:
        logger.debug("Tool catalog unavailable for defaults")

    new_budget = deduct_cost(es["step_costs"], NODE_NAME, es["budget_remaining"])

    queries_detail = [
        {
            "fact_id": f["id"],
            "query": f"{f['subject']} {f['fact_needed']}",
        }
        for f in unknown_facts
    ]

    detail = {
        "queries": queries_detail,
        "candidate_tools": [t.name for t in candidate_tools],
    }

    writer({
        "event": "node_end",
        "payload": {
            "node": NODE_NAME,
            "budget_remaining": new_budget,
            "detail": detail,
        },
    })
    logger.info(
        "TOOL IDENTIFICATION Complete (%d candidates for %d facts)",
        len(candidate_tools),
        len(unknown_facts),
    )

    return {
        "execution_state": {
            **es,
            "candidate_tools": candidate_tools,
            "budget_remaining": new_budget,
        },
    }
