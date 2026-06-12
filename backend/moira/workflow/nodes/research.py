"""Research node: model-driven multi-round tool calling for fact discovery.

The model decides which tools to call, sees results, and iterates. This
matches the original research_execution design: a tool-use loop where the
model produces tool calls, they are executed, results are fed back, and
the loop continues until the model signals completion (empty tool_calls)
or max rounds.

The model returns a JSON object each round with keys:
- tool_calls: array of {tool, args} objects
- discovered_facts: array of {fact_id, subject, claim, relation?, value?}
- sources: array of {source, url?, title?, excerpt?}

Discovered facts and sources are applied each round, not just at the end.
"""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import Citation, Fact, ResearchState, next_id
from moira.prompts import get_prompt
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
    "Your previous response did not contain a valid JSON object with tool_calls, "
    "discovered_facts, and sources. "
    "Respond ONLY with a JSON object: "
    '{{"tool_calls": [...], "discovered_facts": [...], "sources": [...]}}. '
    "Use an empty tool_calls array when done: "
    '{{"tool_calls": [], "discovered_facts": [...], "sources": [...]}}'
)

_SUMMARY_PROMPT = (
    "You have exhausted your available tool call rounds. Do NOT request any more "
    "tool calls. Instead, summarize the facts you have gathered so far.\n\n"
    "Respond with a JSON object:\n"
    '- "tool_calls": [] (must be empty)\n'
    '- "discovered_facts": list all factual claims you can extract from the '
    "tool results above\n"
    '- "sources": list the tools and URLs that provided evidence'
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


def _extract_tool_calls(parsed: dict) -> list[tuple[str, dict]]:
    """Extract tool calls from the parsed JSON object response.

    The model returns {tool_calls: [{tool, args}, ...], ...}.
    Returns a list of (tool_name, args) tuples.
    """
    raw_calls = parsed.get("tool_calls", [])
    if not isinstance(raw_calls, list):
        return []
    result = []
    for call in raw_calls:
        if not isinstance(call, dict):
            continue
        name = call.get("tool", "")
        args = call.get("args", {})
        if name:
            result.append((name, args))
    return result


def _apply_discovered_facts(parsed: dict, facts: list[Fact]) -> None:
    """Apply discovered_facts from a model response to the facts list.

    For facts with a matching fact_id and status "unknown", updates the
    claim, relation, value, and status to "unverified". For new facts
    (fact_id is null/missing with fact_needed), appends them.
    """
    discovered = parsed.get("discovered_facts", [])
    if not isinstance(discovered, list):
        return
    for disc in discovered:
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


def _apply_sources(parsed: dict, citations: list[Citation]) -> None:
    """Apply sources from a model response to the citations list."""
    sources = parsed.get("sources", [])
    if not isinstance(sources, list):
        return
    seen_ids = {c["id"] for c in citations}
    for src in sources:
        if not isinstance(src, dict):
            continue
        cit_id = next_id("cit", citations)
        if cit_id not in seen_ids:
            citations.append(
                Citation(
                    id=cit_id,
                    source=src.get("source", ""),
                    url=src.get("url"),
                    title=src.get("title"),
                    excerpt=src.get("excerpt"),
                )
            )
            seen_ids.add(cit_id)


_FACT_EXTRACTION_SYSTEM = (
    "You are a fact extraction assistant. Given tool execution results and a list "
    "of unknown facts, extract the specific factual claims that were discovered.\n\n"
    "For each fact that the tool results address, provide:\n"
    '- "fact_id": the ID of the fact (e.g., "f001")\n'
    '- "subject": what entity or topic this fact is about\n'
    '- "claim": a specific, precise factual statement based on the tool output\n'
    '- "relation": optional predicate (e.g., "has_type", "equals")\n'
    '- "value": optional value\n\n'
    "For newly discovered facts not in the original list, use \"fact_id\": null and "
    'include "fact_needed" describing what the new fact is about.\n\n'
    "Also list sources:\n"
    '- "source": the tool name\n'
    '- "url": if applicable\n'
    '- "title": if applicable\n'
    '- "excerpt": relevant snippet from the tool output\n\n'
    'Respond with a JSON object: {"discovered_facts": [...], "sources": [...]}\n'
    "Only include facts where the tool results actually provide evidence. If a tool "
    "call failed or returned no useful data, do not fabricate a claim for it."
)

_FACT_EXTRACTION_USER = (
    "User goal: {user_goal}\n\n"
    "Unknown facts (ID | subject | fact_needed):\n{unknown_facts}\n\n"
    "Tool execution results:\n{tool_results_text}"
)


async def _extract_facts_from_results(
    facts: list[Fact],
    tool_results: list[dict],
    resolved: object,
    user_goal: str,
) -> list[Fact]:
    """Post-loop extraction: ask the model to interpret tool results and
    produce discovered_facts. Called when the tool loop produced results
    but the model never included discovered_facts in its responses."""
    unknown_facts_text = _format_unknown_facts(facts)
    tool_results_text = "\n\n".join(
        f"Tool: {tr['tool']}\nArgs: {tr['args']}\n"
        f"Status: {'SUCCESS' if tr['success'] else 'FAILED'}\n"
        f"Result:\n{tr['output']}"
        for tr in tool_results
        if tr.get("success")
    )

    if not tool_results_text.strip():
        logger.warning("RESEARCH: no successful tool results to extract facts from")
        return []

    messages = [
        {"role": "system", "content": _FACT_EXTRACTION_SYSTEM},
        {
            "role": "user",
            "content": _FACT_EXTRACTION_USER.format(
                user_goal=user_goal,
                unknown_facts=unknown_facts_text,
                tool_results_text=tool_results_text,
            ),
        },
    ]

    model_id = getattr(resolved, "model_id", "")
    client = getattr(resolved, "client", None)
    if client is None:
        return []

    logger.info("RESEARCH: running post-loop fact extraction")
    response = await client.chat_completion(
        messages=messages,
        model=model_id,
        temperature=DEFAULT_TEMPERATURE,
    )
    raw = response.content or ""
    if not raw.strip():
        logger.warning("RESEARCH: fact extraction returned empty content")
        return []

    parsed = _parse_json_object(raw)
    _apply_discovered_facts(parsed, facts)

    resolved_list = [f for f in facts if f["status"] != "unknown" and f.get("claim")]
    logger.info(
        "RESEARCH: post-loop extraction produced %d resolved facts",
        len(resolved_list),
    )
    return resolved_list


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

    # Build initial messages using real prompts
    system_prompt = get_prompt("research.system").format(
        max_extra_rounds=DEFAULT_MAX_ROUNDS,
    )
    user_prompt = get_prompt("research.user").format(
        user_goal=knowledge.get("user_goal", knowledge["question"]),
        unknown_facts=_format_unknown_facts(facts),
        tool_call_plan=_format_tool_call_plan(tool_plan),
        tool_descriptions=_format_tool_descriptions(candidate_tools),
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # Build required-params lookup from candidate tools for arg validation
    _required_params: dict[str, set[str]] = {}
    for t in candidate_tools:
        schema = t.argument_schema
        if schema and "required" in schema:
            _required_params[t.name] = set(schema["required"])

    # --- Multi-round tool loop ---
    tool_results_log: list[dict] = []
    total_call_count = 0
    last_response = None
    last_thinking = ""
    last_model_id = ""
    exhausted_rounds = False

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

        # Parse the JSON object response: {tool_calls, discovered_facts, sources}
        parsed = _parse_json_object(raw)

        # Apply discovered facts and sources each round
        _apply_discovered_facts(parsed, facts)
        _apply_sources(parsed, citations)

        # Extract tool calls from the parsed object
        parsed_calls = _extract_tool_calls(parsed)

        # If object parse didn't yield tool_calls, try legacy array parsing
        if not parsed_calls and "tool_calls" not in parsed:
            parsed_calls = _parse_tool_calls(raw)

        # If still unparseable but looks like failed tool calls, retry with correction
        if (
            not parsed_calls
            and raw.strip() not in ("[]", "[]\n")
            and _looks_like_failed_tool_calls(raw)
        ):
            parse_attempt = 0
            while parse_attempt < DEFAULT_MAX_PARSE_RETRIES:
                parse_attempt += 1
                logger.warning(
                    "RESEARCH round %d: unparseable response, retry %d/%d",
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

                parsed = _parse_json_object(raw)
                _apply_discovered_facts(parsed, facts)
                _apply_sources(parsed, citations)
                parsed_calls = _extract_tool_calls(parsed)
                if not parsed_calls and "tool_calls" not in parsed:
                    parsed_calls = _parse_tool_calls(raw)
                if parsed_calls or raw.strip() in ("[]", "[]\n"):
                    break

        # Filter to allowed tools, enforce call limits, validate required args
        call_limits = es.get("tool_call_limits", {})
        valid_calls = []
        rejected: list[tuple[str, dict]] = []
        for n, a in parsed_calls:
            if n not in allowed_names:
                rejected.append((n, a))
                continue
            limit = call_limits.get(n, 0)
            used = call_counts.get(n, 0)
            if limit > 0 and used >= limit:
                logger.warning(
                    "Tool %s hit call limit (%d/%d), skipping",
                    n, used, limit,
                )
                continue
            required = _required_params.get(n)
            if required:
                args_dict = a if isinstance(a, dict) else {}
                missing = [
                    p for p in required
                    if p not in args_dict or not str(args_dict[p]).strip()
                ]
                if missing:
                    logger.warning(
                        "Tool %s missing required params: %s, skipping",
                        n, missing,
                    )
                    continue
            valid_calls.append((n, a))
        rejected_names = [n for n, _ in rejected]
        if rejected_names:
            logger.warning(
                "Model requested disallowed tools: %s",
                rejected_names,
            )

        # No tool calls means model is done
        if not valid_calls:
            logger.info("RESEARCH: model signaled completion (round %d)", round_num + 1)
            exhausted_rounds = False
            break

        # Execute tool calls
        try:
            results = await executor.execute_batch(valid_calls)
        except Exception as e:
            logger.error("Tool execution batch error: %s", e, exc_info=True)
            exhausted_rounds = False
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

            # Update any target facts from the plan that match this tool.
            # Only mark as unverified if the call succeeded and produced output.
            if result.success and result.output:
                for planned in tool_plan:
                    if planned["tool"] == name:
                        target_ids = planned.get("target_fact_ids", [])
                        for fact in facts:
                            if (
                                fact["id"] in target_ids
                                and fact["status"] == "unknown"
                            ):
                                fact["claim"] = result.output[:500]
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

        # If the loop continues to the next iteration (or exhausts),
        # mark as exhausted so we do a final summary round.
        exhausted_rounds = True

    # --- Final summary round if loop exhausted max rounds ---
    # If the loop ended because we ran out of rounds (not because the model
    # signaled completion), do one more model call asking it to summarize
    # what it has gathered without requesting more tools.
    if exhausted_rounds and tool_results_log:
        logger.info("RESEARCH: max rounds exhausted, requesting final summary")
        messages.append({"role": "user", "content": _SUMMARY_PROMPT})
        try:
            summary_response = await resolved.client.chat_completion(
                messages=messages,
                model=resolved.model_id,
                temperature=DEFAULT_TEMPERATURE,
            )
            total_call_count += 1
            summary_raw = summary_response.content or ""
            if summary_raw.strip():
                last_response = summary_response
                last_thinking = getattr(summary_response, "thinking", "") or ""
                summary_parsed = _parse_json_object(summary_raw)
                _apply_discovered_facts(summary_parsed, facts)
                _apply_sources(summary_parsed, citations)
        except Exception:
            logger.warning("RESEARCH: final summary round failed", exc_info=True)

    # --- Post-loop: extract discovered facts if model never produced them ---
    resolved_facts = [f for f in facts if f["status"] != "unknown" and f.get("claim")]
    if tool_results_log and not resolved_facts:
        logger.info(
            "RESEARCH: no discovered_facts produced during loop, "
            "running post-loop extraction",
        )
        resolved_facts = await _extract_facts_from_results(
            facts=facts,
            tool_results=tool_results_log,
            resolved=resolved,
            user_goal=knowledge.get("user_goal", knowledge["question"]),
        )
        total_call_count += 1

        if resolved_facts:
            fact_text = "\n".join(
                f"- {f['id']}: {f.get('claim', '')}" for f in resolved_facts
            )
            last_response = type(
                "ChatResponse", (), {"content": fact_text, "thinking": ""}
            )()
            last_thinking = ""

    # --- Build detail and emit ---
    # NOTE: tool_results are NOT included here. The run_manager accumulates
    # them from tool_result events with full output. Including them in
    # node_end would overwrite with truncated copies.
    detail: dict = {
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
