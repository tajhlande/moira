"""Research node: model-driven multi-round tool calling for fact discovery.

The model decides which tools to call, sees results, and iterates. This
matches the original research_execution design: a tool-use loop where the
model produces tool calls, they are executed, results are fed back, and
the loop continues until the model signals completion ([]) or max rounds.

Under the overhaul, the OUTPUT format changes: instead of prose findings,
the model records structured discovered_facts with fact_id, subject,
claim, relation, and value. But the loop mechanics are the same.

Phase A: uses stub prompts. Full prompts added in Phase B."""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import Citation, Fact, ResearchState, next_id
from moira.tools.base import ToolDefinition
from moira.workflow.budget import can_execute, deduct_cost
from moira.workflow.nodes._helpers import (
    _check_stop,
    _get_model,
    _now,
    _parse_json_object,
    _response_meta,
)

logger = logging.getLogger(__name__)

NODE_NAME = "research"

DEFAULT_MAX_ROUNDS = 3
DEFAULT_MAX_PARSE_RETRIES = 2

_PARSE_CORRECTION_PROMPT = (
    "Your previous response did not contain a valid JSON array of tool calls. "
    "Respond ONLY with a raw JSON array, no markdown fences. "
    'Example: [{"tool": "tool_name", "args": {"key": "value"}}] '
    "Respond with [] if you are done gathering evidence."
)

_STUB_SYSTEM = (
    "You are a research assistant performing fact discovery. Your job is to "
    "call tools to find the specific facts identified in the research plan, "
    "interpret the results, and record what you learned.\n\n"
    "You will receive unknown facts with their IDs. Call tools to discover them. "
    "After receiving results, you may request additional tool calls if results "
    "were incomplete, or identify new facts that need to be discovered.\n\n"
    "Rules:\n"
    "- You must NOT draw conclusions or synthesize answers — your only job is fact discovery\n"
    "- When a tool returns data, extract specific fact claims from it\n"
    "- If a tool call fails or returns empty results, try a different approach\n"
    "- If a specialized tool is available for the domain, prefer it over generic tools\n\n"
    "For each fact you discover, record:\n"
    "- fact_id: the ID of the fact this resolves (e.g., 'f001')\n"
    "- subject: what entity or topic this fact is about\n"
    "- claim: a specific, precise claim based on the tool output\n"
    "- optionally: relation and value\n\n"
    "For newly identified facts not in the original list, use fact_id: null and "
    "include fact_needed describing what the new fact is about.\n\n"
    "Respond with JSON:\n"
    '{tool_calls: [{"tool": "...", "args": {...}}, ...], '
    "discovered_facts: [{fact_id, subject, claim, relation?, value?}, ...], "
    "sources: [{source, url?, title?, excerpt?}, ...]}\n"
    "Use [] for tool_calls when you are done."
)

_STUB_USER = (
    "User goal: {user_goal}\n"
    "Unknown facts (ID | subject | fact_needed):\n{unknown_facts}\n"
    "Tool call plan (suggested tool | args | target fact IDs):\n{tool_call_plan}\n"
    "Available tools:\n{tool_descriptions}"
)


def _format_tool_descriptions(tools: list[ToolDefinition]) -> str:
    """Format tool definitions with argument schemas for LLM consumption."""
    def _render(tool) -> str:
        if not isinstance(tool, ToolDefinition):
            return f"- {tool.name}: {getattr(tool, 'description', '')}"
        entry = f"- {tool.name}: {tool.description}"
        schema = tool.argument_schema
        if schema and "properties" in schema:
            required = set(schema.get("required", []))
            props = schema["properties"]
            param_lines = []
            for pname, pdef in props.items():
                ptype = pdef.get("type", "any")
                req = "required" if pname in required else "optional"
                default = pdef.get("default")
                pdesc = pdef.get("description", "")
                segments = [f"    {pname} ({ptype}, {req}"]
                if default is not None:
                    segments.append(f", default: {default}")
                segments.append(f"): {pdesc}" if pdesc else ")")
                param_lines.append("".join(segments))
            if param_lines:
                entry += "\n  Parameters:\n" + "\n".join(param_lines)
        return entry

    return "\n".join(_render(t) for t in tools)


def _format_tool_call_plan(plan: list) -> str:
    lines = []
    for call in plan:
        target_ids = ", ".join(call.get("target_fact_ids", []))
        lines.append(f"- {call['tool']} | args: {call.get('args', {})} | targets: [{target_ids}]")
    return "\n".join(lines)


def _format_unknown_facts(facts: list[Fact]) -> str:
    lines = []
    for f in facts:
        if f.get("status") in ("unknown", "contradicted"):
            lines.append(f"{f['id']} | {f['subject']} | {f['fact_needed']}")
    return "\n".join(lines)


def _parse_tool_calls(text: str) -> list[tuple[str, dict]]:
    """Parse tool calls from model response. Handles formats:
    1. JSON array: [{"tool": "name", "args": {...}}, ...]
    2. Line-delimited JSON objects: {"tool": "name", "args": {...}}
    3. Markdown-fenced arrays
    Local models often produce formats 2 and 3."""
    import json
    import re

    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            calls = json.loads(match.group())
            if isinstance(calls, list):
                result = []
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    name = call.get("tool", "")
                    args = call.get("args", {})
                    if name:
                        result.append((name, args))
                if result:
                    return result
        except (json.JSONDecodeError, TypeError):
            pass

    stripped = re.sub(r"```\w*\n?", "", text).strip()
    result = []
    for line in stripped.splitlines():
        line = line.strip()
        if not line or line in ("```",):
            continue
        if line.startswith("["):
            try:
                calls = json.loads(line)
                if isinstance(calls, list):
                    for call in calls:
                        if isinstance(call, dict):
                            name = call.get("tool", "")
                            args = call.get("args", {})
                            if name:
                                result.append((name, args))
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                name = obj.get("tool", "")
                args = obj.get("args", {})
                if name:
                    result.append((name, args))
            except (json.JSONDecodeError, TypeError):
                continue
    return result


def _looks_like_failed_tool_calls(text: str) -> bool:
    """Heuristic: does this response look like the model was trying to
    produce tool calls but failed? Triggers a parse-retry."""
    stripped = text.strip()
    if _parse_tool_calls(stripped):
        return False
    if stripped == "[]" or stripped == "[]\n":
        return False
    if "```" in stripped and ("tool" in stripped.lower() or "{" in stripped):
        return True
    if stripped.startswith("["):
        return True
    return False


async def research(state: ResearchState, config: RunnableConfig) -> dict:
    """Model-driven multi-round tool calling for fact discovery.

    The model decides which tools to call from the candidate set, sees
    results, and can request additional rounds. The tool_call_plan from
    planning is provided as guidance, but the model drives execution.
    """
    _check_stop(NODE_NAME, config)
    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": NODE_NAME, "timestamp": _now()}})

    es = state["execution_state"]
    knowledge = state["knowledge"]
    if not can_execute(es["step_costs"], NODE_NAME, es["budget_remaining"]):
        writer({
            "event": "node_end",
            "payload": {
                "node": NODE_NAME,
                "budget_remaining": es["budget_remaining"],
            },
        })
        return {
            "execution_state": {
                **es,
                "error": f"Insufficient budget for {NODE_NAME}",
            },
        }

    new_budget = deduct_cost(es["step_costs"], NODE_NAME, es["budget_remaining"])
    call_counts = dict(es.get("tool_call_counts", {}))
    total_tool_cost = es.get("total_tool_cost_consumed", 0.0)
    tool_costs = es.get("tool_costs", {})

    facts = list(knowledge["facts"])
    citations = list(knowledge["citations"])

    candidate_tools = es.get("candidate_tools", [])
    tool_plan = es.get("tool_call_plan", [])

    # Get tool executor
    from moira.service_setup import service_provider

    try:
        executor = service_provider("tool_executor")
    except RuntimeError:
        executor = None

    if not candidate_tools or executor is None:
        logger.warning(
            "RESEARCH: no tools available (candidates=%d, executor=%s)",
            len(candidate_tools),
            "yes" if executor else "none",
        )
        detail = {
            "tool_results": [],
            "facts_resolved": [],
            "facts_newly_unknown": [f["id"] for f in facts if f["status"] == "unknown"],
            "tool_plan_size": len(tool_plan),
            "executor_available": executor is not None,
            "rounds": 0,
        }
        writer({
            "event": "node_end",
            "payload": {
                "node": NODE_NAME,
                "budget_remaining": new_budget,
                "detail": detail,
                "purpose": NODE_NAME,
                "model": "",
                "call_count": 0,
                "tool_call_count": 0,
            },
        })
        return {
            "knowledge": {
                "facts": facts,
                "citations": citations,
            },
            "execution_state": {
                **es,
                "budget_remaining": new_budget,
                "tool_call_counts": call_counts,
                "total_tool_cost_consumed": total_tool_cost,
            },
        }

    allowed_names = {t.name for t in candidate_tools}

    # Build initial messages
    user_prompt = _STUB_USER.format(
        user_goal=knowledge.get("user_goal", knowledge["question"]),
        unknown_facts=_format_unknown_facts(facts),
        tool_call_plan=_format_tool_call_plan(tool_plan),
        tool_descriptions=_format_tool_descriptions(candidate_tools),
    )
    messages = [
        {"role": "system", "content": _STUB_SYSTEM},
        {"role": "user", "content": user_prompt},
    ]

    # --- Multi-round tool loop ---
    tool_results_log: list[dict] = []
    total_call_count = 0
    last_response = None
    last_thinking = ""
    last_model_id = ""

    registry = _get_model(config)
    resolved = await registry.resolve("intelligence")
    last_model_id = resolved.model_id

    for round_num in range(DEFAULT_MAX_ROUNDS):
        logger.info("RESEARCH round %d/%d", round_num + 1, DEFAULT_MAX_ROUNDS)

        response = await resolved.client.chat_completion(
            messages=messages,
            model=resolved.model_id,
            temperature=DEFAULT_TEMPERATURE,
        )
        total_call_count += 1
        last_response = response
        last_thinking = getattr(response, "thinking", "") or ""
        raw = response.content or ""

        if not raw.strip():
            logger.error(
                "RESEARCH: model returned empty content in round %d (thinking=%d chars)",
                round_num + 1,
                len(last_thinking),
            )
            writer({
                "event": "run_error",
                "payload": {
                    "error": f"Model returned empty content for {NODE_NAME}",
                    "budget_remaining": new_budget,
                    "detail": {
                        "tool_results": tool_results_log,
                        "prompt": messages[-1]["content"] if messages else "",
                        "response": raw,
                        "model": resolved.model_id,
                        "thinking": last_thinking,
                        "round": round_num + 1,
                    },
                    "purpose": NODE_NAME,
                    "model": resolved.model_id,
                    "call_count": total_call_count,
                },
            })
            raise RuntimeError(
                f"Model returned empty content for {NODE_NAME} "
                f"(thinking={len(last_thinking)} chars)"
            )

        # Parse tool calls from response
        parsed_calls = _parse_tool_calls(raw)

        # If response can't be parsed but looks like a failed attempt, retry
        if not parsed_calls and raw.strip() not in ("[]", "[]\n") and _looks_like_failed_tool_calls(raw):
            parse_attempt = 0
            while parse_attempt < DEFAULT_MAX_PARSE_RETRIES:
                parse_attempt += 1
                logger.warning(
                    "RESEARCH round %d: unparseable tool calls, retry %d/%d",
                    round_num + 1,
                    parse_attempt,
                    DEFAULT_MAX_PARSE_RETRIES,
                )
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": _PARSE_CORRECTION_PROMPT})

                response = await resolved.client.chat_completion(
                    messages=messages,
                    model=resolved.model_id,
                    temperature=max(DEFAULT_TEMPERATURE - 0.2, 0.1),
                )
                total_call_count += 1
                last_response = response
                last_thinking = getattr(response, "thinking", "") or ""
                raw = response.content or ""

                if not raw.strip():
                    break

                parsed_calls = _parse_tool_calls(raw)
                if parsed_calls or raw.strip() in ("[]", "[]\n"):
                    break

        # Filter to allowed tools
        valid_calls = [(n, a) for n, a in parsed_calls if n in allowed_names]
        rejected = [(n, a) for n, a in parsed_calls if n not in allowed_names]
        if rejected:
            logger.warning(
                "Model requested disallowed tools: %s",
                [n for n, _ in rejected],
            )

        # No tool calls means model is done
        if not valid_calls:
            logger.info("RESEARCH: model signaled completion (round %d)", round_num + 1)
            break

        # Execute tool calls
        try:
            results = await executor.execute_batch(
                valid_calls,
                tools=candidate_tools,
            )
        except Exception as e:
            logger.error("Tool execution batch error: %s", e, exc_info=True)
            break

        # Process results and build summary for next round
        tool_summary_parts = []
        for result, (name, args) in zip(results, valid_calls):
            writer({
                "event": "tool_result",
                "payload": {
                    "tool": result.tool_name,
                    "args": args,
                    "output": result.output[:2000] if result.output else "",
                    "duration_ms": result.duration_ms,
                    "success": result.success,
                    "node": NODE_NAME,
                },
            })

            # Create citation
            cit_id = next_id("cit", citations)
            citations.append(
                Citation(
                    id=cit_id,
                    source=result.tool_name,
                    excerpt=result.output[:500] if result.output else "",
                )
            )

            tool_results_log.append({
                "tool": result.tool_name,
                "args": args,
                "output": result.output[:500] if result.output else "",
                "duration_ms": result.duration_ms,
                "success": result.success,
            })

            # Update any target facts from the plan that match this tool
            for planned in tool_plan:
                if planned["tool"] == name:
                    target_ids = planned.get("target_fact_ids", [])
                    for fact in facts:
                        if fact["id"] in target_ids and fact["status"] == "unknown":
                            fact["claim"] = result.output[:500] if result.output else ""
                            fact["status"] = "unverified"
                            if fact.get("citation_ids") is None:
                                fact["citation_ids"] = []
                            fact["citation_ids"].append(cit_id)

            # Track call counts and per-call tool cost
            call_counts[name] = call_counts.get(name, 0) + 1
            per_call_cost = tool_costs.get(name, 1.0)
            new_budget -= per_call_cost
            total_tool_cost += per_call_cost

            status = "SUCCESS" if result.success else "FAILED"
            tool_summary_parts.append(
                f"Tool: {name}\nStatus: {status}\nResult:\n{result.output}"
            )

        # Feed results back to model for next round
        tool_summary = "\n\n---\n\n".join(tool_summary_parts)
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": f"Tool execution results:\n\n{tool_summary}"})

        logger.info(
            "RESEARCH round %d: %d tool calls executed",
            round_num + 1,
            len(results),
        )

    # --- Post-loop: extract discovered facts from final model response ---
    if last_response is not None:
        final_raw = last_response.content or ""
        parsed = _parse_json_object(final_raw)

        for disc in parsed.get("discovered_facts", []):
            if not isinstance(disc, dict):
                continue
            fact_id = disc.get("fact_id")
            if fact_id:
                for fact in facts:
                    if fact["id"] == fact_id and fact["status"] == "unknown":
                        fact["claim"] = disc.get("claim", "")
                        if disc.get("relation"):
                            fact["relation"] = disc["relation"]
                        if disc.get("value"):
                            fact["value"] = disc["value"]
                        fact["status"] = "unverified"
                        break
            elif disc.get("fact_needed"):
                new_id = next_id("f", facts)
                facts.append(
                    Fact(
                        id=new_id,
                        subject=disc.get("subject", ""),
                        fact_needed=disc["fact_needed"],
                        status="unknown",
                    )
                )

    # --- Build detail and emit ---
    detail = {
        "tool_results": tool_results_log,
        "facts_resolved": [f["id"] for f in facts if f["status"] == "unverified"],
        "facts_newly_unknown": [f["id"] for f in facts if f["status"] == "unknown"],
        "tool_plan_size": len(tool_plan),
        "executor_available": executor is not None,
        "rounds": round_num + 1 if valid_calls else round_num,
        "prompt": user_prompt,
        "model": last_model_id,
    }
    if last_response is not None:
        detail["response"] = last_response.content or ""
    if last_thinking:
        detail["thinking"] = last_thinking

    writer({
        "event": "node_end",
        "payload": {
            "node": NODE_NAME,
            "budget_remaining": new_budget,
            "detail": detail,
            "purpose": NODE_NAME,
            "model": last_model_id,
            "call_count": total_call_count,
            "tool_call_count": len(tool_results_log),
            **(_response_meta(last_response) if last_response is not None else {}),
        },
    })
    logger.info(
        "RESEARCH Complete (%d tool calls across %d rounds, budget=%.1f)",
        len(tool_results_log),
        detail["rounds"],
        new_budget,
    )

    return {
        "knowledge": {
            "facts": facts,
            "citations": citations,
        },
        "execution_state": {
            **es,
            "budget_remaining": new_budget,
            "tool_call_counts": call_counts,
            "total_tool_cost_consumed": total_tool_cost,
        },
    }
