"""Verification node: evaluates facts, conclusions, and goal attainment.

Performs fact verification, conclusion verification, and goal assessment.
May call tools to re-check claims. Produces a VerificationOutcome that
determines routing.

When the model returns tool_calls instead of a verification result, the
node executes those calls and re-prompts with the tool evidence so the
model can produce a grounded verification verdict.
"""

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.models.knowledge import (
    Fact,
    ResearchState,
    VerificationOutcome,
    next_id,
)
from moira.prompts import get_prompt
from moira.workflow.budget import can_execute, deduct_cost
from moira.workflow.nodes._helpers import (
    _check_stop,
    _get_model,
    _now,
    _parse_json_object,
    _response_meta,
)

logger = logging.getLogger(__name__)

NODE_NAME = "verification"

MAX_FACT_CHECK_ROUNDS = 2


def _format_facts_for_verification(facts: list[Fact]) -> str:
    lines = []
    for f in facts:
        if f.get("claim"):
            cit = ", ".join(f.get("citation_ids", []))
            lines.append(
                f"{f['id']} | {f['subject']} | {f['fact_needed']} | "
                f"{f['claim']} | {f['status']} | [{cit}]"
            )
    return "\n".join(lines)


def _format_conclusions(conclusions: list) -> str:
    lines = []
    for c in conclusions:
        facts_str = ", ".join(c.get("supporting_fact_ids", []))
        lines.append(
            f"{c['id']} | {c['conclusion']} | [{facts_str}] | "
            f"{c.get('reasoning', '')} | {c['status']}"
        )
    return "\n".join(lines)


def _format_tools(tools: list) -> str:
    if not tools:
        return "(no tools available)"
    return "\n".join(f"- {t.name}: {t.description[:80]}" for t in tools)


def _extract_tool_calls(parsed: dict) -> list[tuple[str, dict]]:
    """Extract tool calls from the parsed JSON response.

    Handles two formats:
    1. {tool_calls: [{tool, args}, ...]} — the expected format
    2. {tool: "name", query/args/arguments: {...}} — a single bare tool call
       that some models produce instead of the wrapper format.
    Returns a list of (tool_name, args) tuples.
    """
    # Format 1: explicit tool_calls array
    raw_calls = parsed.get("tool_calls", [])
    if isinstance(raw_calls, list):
        result = []
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            name = call.get("name") or call.get("tool", "")
            args = call.get("args") or call.get("arguments", {})
            if name:
                result.append((name, args if isinstance(args, dict) else {}))
        if result:
            return result

    # Format 2: bare single tool call — has "tool" key but no verification fields
    tool_name = parsed.get("tool") or parsed.get("name", "")
    if tool_name and "fact_results" not in parsed and "route" not in parsed:
        # Collect non-meta keys as args (model may use query, args, etc.)
        meta_keys = {"tool", "name", "id", "call_id"}
        args = {k: v for k, v in parsed.items() if k not in meta_keys}
        return [(tool_name, args)]

    return []


def _format_evidence(tool_results_log: list[dict]) -> str:
    """Format tool results as evidence text for the verification prompt."""
    parts = []
    for tr in tool_results_log:
        status = "SUCCESS" if tr.get("success") else "FAILED"
        parts.append(
            f"Tool: {tr['tool']}\nStatus: {status}\nResult:\n{tr.get('output', '')}"
        )
    return "\n\n---\n\n".join(parts)


async def _execute_tool_calls(
    tool_calls: list[tuple[str, dict]],
    candidate_tools: list,
    call_limits: dict[str, int],
    call_counts: dict[str, int],
    tool_costs: dict[str, float],
    budget: float,
) -> tuple[list[dict], dict[str, int], float, float]:
    """Execute a batch of tool calls with limit enforcement.

    Returns (tool_results_log, updated_call_counts, total_tool_cost, remaining_budget).
    """
    from moira.service_setup import service_provider

    tool_results_log: list[dict] = []
    total_tool_cost = 0.0

    try:
        executor = service_provider("tool_executor")
    except RuntimeError:
        logger.warning("VERIFICATION: tool executor not available")
        return tool_results_log, call_counts, total_tool_cost, budget

    allowed_names = {t.name for t in candidate_tools} if candidate_tools else set()
    valid_calls = []
    for name, args in tool_calls:
        if allowed_names and name not in allowed_names:
            logger.warning("VERIFICATION: tool %s not in candidate list, skipping", name)
            continue
        limit = call_limits.get(name, 0)
        used = call_counts.get(name, 0)
        if limit > 0 and used >= limit:
            logger.warning(
                "VERIFICATION: tool %s hit call limit (%d/%d), skipping",
                name, used, limit,
            )
            continue
        valid_calls.append((name, args))

    if not valid_calls:
        return tool_results_log, call_counts, total_tool_cost, budget

    try:
        results = await executor.execute_batch(valid_calls)
    except Exception as e:
        logger.error("VERIFICATION: tool execution batch error: %s", e, exc_info=True)
        return tool_results_log, call_counts, total_tool_cost, budget

    for result, (name, args) in zip(results, valid_calls):
        tool_results_log.append({
            "tool": result.tool_name,
            "args": args,
            "output": result.output[:500] if result.output else "",
            "duration_ms": result.duration_ms,
            "success": result.success,
        })
        call_counts[name] = call_counts.get(name, 0) + 1
        per_call_cost = tool_costs.get(name, 1.0)
        budget -= per_call_cost
        total_tool_cost += per_call_cost

    return tool_results_log, call_counts, total_tool_cost, budget


async def verification(state: ResearchState, config: RunnableConfig) -> dict:
    """Verify facts, conclusions, and assess goal attainment."""
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

    facts = list(knowledge["facts"])
    conclusions = list(knowledge.get("conclusions", []))

    tool_results_log: list[dict] = []
    candidate_tools = es.get("candidate_tools", [])
    call_counts = dict(es.get("tool_call_counts", {}))
    tool_costs = es.get("tool_costs", {})
    call_limits = es.get("tool_call_limits", {})
    total_tool_cost = es.get("total_tool_cost_consumed", 0.0)
    new_budget = es["budget_remaining"]

    user_prompt = get_prompt("verification.user").format(
        user_goal=knowledge.get("user_goal", knowledge["question"]),
        question=knowledge["question"],
        facts_with_claims_and_sources=_format_facts_for_verification(facts),
        conclusions_with_supporting_facts=_format_conclusions(conclusions),
        tool_descriptions=_format_tools(candidate_tools),
    )

    registry = _get_model(config)
    resolved = await registry.resolve("intelligence")
    messages = [
        {"role": "system", "content": get_prompt("verification.system")},
        {"role": "user", "content": user_prompt},
    ]

    verification_attempts = es.get("verification_attempts", 0) + 1
    logger.info("VERIFICATION Start (attempt=%d)", verification_attempts)

    total_call_count = 0

    response = await resolved.client.chat_completion(
        messages=messages,
        model=resolved.model_id,
        temperature=DEFAULT_TEMPERATURE,
    )
    total_call_count += 1

    raw = response.content or ""
    thinking = getattr(response, "thinking", "") or ""
    new_budget = deduct_cost(es["step_costs"], NODE_NAME, new_budget)

    detail = {
        "prompt": user_prompt,
        "response": raw,
        "model": resolved.model_id,
        "tool_results": tool_results_log,
    }
    if thinking:
        detail["thinking"] = thinking

    if not raw.strip():
        logger.error(
            "VERIFICATION: model returned empty content (thinking=%d chars)",
            len(thinking),
        )
        writer({
            "event": "run_error",
            "payload": {
                "error": f"Model returned empty content for {NODE_NAME}",
                "budget_remaining": new_budget,
                "detail": detail,
                "purpose": NODE_NAME,
                "model": resolved.model_id,
                "call_count": total_call_count,
                "tool_call_count": len(tool_results_log),
            },
        })
        raise RuntimeError(
            f"Model returned empty content for {NODE_NAME} "
            f"(thinking={len(thinking)} chars)"
        )

    parsed = _parse_json_object(raw)

    # If the model returned tool_calls instead of a verification result,
    # execute them and re-prompt with the evidence.
    fact_check_round = 0
    while (
        _extract_tool_calls(parsed)
        and "fact_results" not in parsed
        and fact_check_round < MAX_FACT_CHECK_ROUNDS
    ):
        fact_check_round += 1
        tool_calls = _extract_tool_calls(parsed)
        logger.info(
            "VERIFICATION: model requested %d tool calls (round %d)",
            len(tool_calls),
            fact_check_round,
        )

        # Emit tool_call events so the frontend can show them in the step
        for name, args in tool_calls:
            writer({
                "event": "tool_result",
                "payload": {
                    "tool": name,
                    "args": args,
                    "output": "",
                    "duration_ms": 0,
                    "success": False,
                    "node": NODE_NAME,
                    "status": "pending",
                },
            })

        round_results, call_counts, round_cost, new_budget = await _execute_tool_calls(
            tool_calls=tool_calls,
            candidate_tools=candidate_tools,
            call_limits=call_limits,
            call_counts=call_counts,
            tool_costs=tool_costs,
            budget=new_budget,
        )
        total_tool_cost += round_cost

        # Emit actual results
        for tr in round_results:
            writer({
                "event": "tool_result",
                "payload": {
                    "tool": tr["tool"],
                    "args": tr.get("args", {}),
                    "output": tr["output"][:2000] if tr.get("output") else "",
                    "duration_ms": tr.get("duration_ms", 0),
                    "success": tr.get("success", False),
                    "node": NODE_NAME,
                },
            })
            tool_results_log.append(tr)

        if not round_results:
            logger.warning("VERIFICATION: no tool calls executed, proceeding without evidence")
            break

        # Re-prompt with evidence
        evidence_text = _format_evidence(round_results)
        evidence_prompt = get_prompt("verification.evidence").format(
            evidence=evidence_text,
        )
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": evidence_prompt})

        response = await resolved.client.chat_completion(
            messages=messages,
            model=resolved.model_id,
            temperature=DEFAULT_TEMPERATURE,
        )
        total_call_count += 1

        raw = response.content or ""
        thinking = getattr(response, "thinking", "") or ""

        detail["response"] = raw
        if thinking:
            detail["thinking"] = thinking

        if not raw.strip():
            logger.warning("VERIFICATION: re-prompt returned empty content")
            break

        parsed = _parse_json_object(raw)

    # Apply fact results
    for fr in parsed.get("fact_results", []):
        fid = fr.get("fact_id", "")
        result = fr.get("result", "unverified")
        for fact in facts:
            if fact["id"] == fid:
                fact["status"] = result
                if fr.get("evidence"):
                    fact["verification_note"] = fr["evidence"]
                break

    # Apply conclusion results
    for cr in parsed.get("conclusion_results", []):
        cid = cr.get("conclusion_id", "")
        result = cr.get("result", "unverified")
        for conclusion in conclusions:
            if conclusion["id"] == cid:
                conclusion["status"] = result
                if cr.get("reason"):
                    conclusion["verification_note"] = cr["reason"]
                break

    # Build verification outcome
    new_facts_descriptions = parsed.get("new_unknown_facts", [])
    new_unknown_facts: list[Fact] = []
    for desc in new_facts_descriptions:
        if isinstance(desc, str) and desc.strip():
            new_id = next_id("f", facts + new_unknown_facts)
            new_unknown_facts.append(
                Fact(
                    id=new_id,
                    subject="",
                    fact_needed=desc.strip(),
                    status="unknown",
                )
            )

    outcome = VerificationOutcome(
        fact_results=parsed.get("fact_results", []),
        conclusion_results=parsed.get("conclusion_results", []),
        new_unknown_facts=[f["fact_needed"] for f in new_unknown_facts],
        goal_met=parsed.get("goal_met", False),
        goal_assessment=parsed.get("goal_assessment", ""),
        route=parsed.get("route", "accept"),
    )

    verification_history = list(knowledge.get("verification_history", []))
    verification_history.append(outcome)

    # Add new unknown facts to the facts list
    all_facts = facts + new_unknown_facts

    # Always build structured_output from the verification schema so the
    # frontend renderer shows the right fields — not raw tool_calls.
    detail["structured_output"] = {
        "fact_results": parsed.get("fact_results", []),
        "conclusion_results": parsed.get("conclusion_results", []),
        "new_unknown_facts": parsed.get("new_unknown_facts", []),
        "goal_met": parsed.get("goal_met", False),
        "goal_assessment": parsed.get("goal_assessment", ""),
        "route": parsed.get("route", "accept"),
    }

    writer({
        "event": "node_end",
        "payload": {
            "node": NODE_NAME,
            "budget_remaining": new_budget,
            "detail": detail,
            "purpose": NODE_NAME,
            "model": resolved.model_id,
            "call_count": total_call_count,
            "tool_call_count": len(tool_results_log),
            **_response_meta(response),
        },
    })
    logger.info(
        "VERIFICATION Complete (route=%s, goal_met=%s, tool_calls=%d, fact_check_rounds=%d)",
        outcome["route"],
        outcome["goal_met"],
        len(tool_results_log),
        fact_check_round,
    )

    return {
        "knowledge": {
            "facts": all_facts,
            "conclusions": conclusions,
            "verification_history": verification_history,
        },
        "execution_state": {
            **es,
            "budget_remaining": new_budget,
            "verification_attempts": verification_attempts,
            "tool_call_counts": call_counts,
            "total_tool_cost_consumed": total_tool_cost,
        },
    }
