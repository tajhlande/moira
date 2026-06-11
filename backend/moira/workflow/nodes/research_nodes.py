import logging
from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from moira.inference.metrics import TokenCounts
from moira.models.state import ResearchState, VerificationReport
from moira.prompts import get_prompt
from moira.workflow.budget import can_execute, deduct_cost, full_cycle_cost

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOOL_ROUNDS = 3
DEFAULT_MAX_PARSE_RETRIES = 2


def _check_stop(node: str, config: RunnableConfig) -> None:
    """Cooperative stop check for user-initiated run cancellation.

    Checks the ActiveRun's ``_stop_requested`` flag via the RunManager.
    If set, emits ``node_start`` so the UI shows which step was active,
    then calls ``interrupt("user_stop")`` to pause the graph and save
    the checkpoint.  LangGraph re-executes the interrupted node from
    scratch on resume, so any partial work in this node is discarded.

    Must be called at the *start* of every node function, before any
    model calls or tool execution."""
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
    interrupt("user_stop")


_PARSE_CORRECTION_PROMPT = (
    "Your previous response did not contain a valid JSON array of tool calls. "
    "Respond ONLY with a raw JSON array, no markdown fences. "
    'Example: [{"tool": "tool_name", "args": {"key": "value"}}] '
    "Respond with [] if you are done gathering evidence."
)


def _format_tool_descriptions(
    tools: list,
    discovered: list | None = None,
    defaults: list | None = None,
) -> str:
    """Format tool definitions with their argument schemas for LLM consumption.

    Produces a structured description including parameter names, types,
    required/optional status, defaults, and descriptions so the model knows
    exactly what arguments to pass.

    When ``discovered`` and ``defaults`` are both provided, the output is
    split into two labelled groups so the model can see which tools were
    selected for the task and which are always available. Otherwise the
    tools are rendered as a single flat list (back-compat).
    """
    from moira.tools.base import ToolDefinition

    def _render(tool) -> str:
        if not isinstance(tool, ToolDefinition):
            return f"- {tool.name}: {getattr(tool, 'description', '')}"
        entry = f"- {tool.name}: {tool.description}"
        schema = tool.argument_schema
        if schema and "properties" in schema:
            required = set(schema.get("required", []))
            props = schema["properties"]
            param_lines: list[str] = []
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

    if discovered is None and defaults is None:
        return "\n".join(_render(t) for t in tools)

    sections: list[str] = []
    if discovered:
        sections.append(
            "[Tools you selected for this task at an earlier step]\n"
            + "\n".join(_render(t) for t in discovered)
        )
    if defaults:
        sections.append(
            "[Standard tools that are always available to you]\n"
            + "\n".join(_render(t) for t in defaults)
        )
    return "\n\n".join(sections)


def _log_prompts(node: str, messages: list[dict[str, str]]) -> None:
    """Log the system and user prompts at DEBUG level for transparency."""
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        logger.debug("[%s] %s prompt:\n%s", node, role.upper(), content)


def _log_response(node: str, response: str) -> None:
    """Log the model's response at DEBUG level for transparency."""
    logger.debug("[%s] model response:\n%s", node, response[:4000])


def _extract(node: str, result) -> tuple[str, str | None]:
    """Extract content and thinking from a ChatResponse. Logs both at DEBUG.
    Returns (content, thinking_text) where thinking_text is None when the
    model did not produce a thinking trace."""
    from moira.inference.client import ChatResponse

    if isinstance(result, ChatResponse):
        if result.thinking:
            logger.debug("[%s] model thinking:\n%s", node, result.thinking[:4000])
        _log_response(node, result.content)
        return result.content, result.thinking
    _log_response(node, str(result))
    return str(result), None


def _emit_node_end(
    writer,
    node: str,
    budget_remaining: float,
    detail: dict | None = None,
    *,
    purpose: str | None = None,
    model: str | None = None,
    call_count: int | None = None,
    input_tokens: int | None = None,
    thinking_tokens: int | None = None,
    output_tokens: int | None = None,
    prompt_time_ms: float | None = None,
    gen_time_ms: float | None = None,
) -> None:
    """Emit a node_end event with optional detail dict."""
    payload: dict = {"node": node, "timestamp": _now(), "budget_remaining": budget_remaining}
    if detail:
        payload["detail"] = detail
    if purpose is not None:
        payload["purpose"] = purpose
    if model is not None:
        payload["model"] = model
    if call_count is not None:
        payload["call_count"] = call_count
    if input_tokens is not None:
        payload["input_tokens"] = input_tokens
    if thinking_tokens is not None:
        payload["thinking_tokens"] = thinking_tokens
    if output_tokens is not None:
        payload["output_tokens"] = output_tokens
    if prompt_time_ms is not None:
        payload["prompt_time_ms"] = prompt_time_ms
    if gen_time_ms is not None:
        payload["gen_time_ms"] = gen_time_ms
    writer({"event": "node_end", "payload": payload})


def _build_detail(
    response: str,
    thinking: str | None = None,
    *,
    messages: list[dict[str, str]] | None = None,
    structured_output: dict | None = None,
) -> dict:
    """Build a step detail dict with prompt, response, thinking, and optional
    structured output. Keys with falsy values are omitted to keep payloads
    small."""
    detail: dict = {"response": response}
    if messages:
        detail["prompt"] = {"messages": messages}
    if thinking:
        detail["thinking"] = thinking
    if structured_output:
        detail["structured_output"] = structured_output
    return detail


async def _execute_tool_round(
    node: str,
    response: str,
    active_tools: list,
    executor,
    writer,
) -> list:
    """Parse tool calls from a model response, filter against the active tool
    set, execute them, emit tool_result events, and return the results.

    Returns a list of (ToolResult, valid_calls) tuples where valid_calls is
    the list of (name, args) pairs that were actually dispatched."""
    tool_calls = _parse_tool_calls(response)
    allowed_names = {t.name for t in active_tools}
    valid_calls, rejected = [], []
    for name, args in tool_calls:
        (valid_calls if name in allowed_names else rejected).append((name, args))
    if rejected:
        logger.warning(
            "LLM requested tools not in active set: %s",
            [n for n, _ in rejected],
        )

    if not valid_calls:
        return []

    results = await executor.execute_batch(valid_calls, allowed_tools=allowed_names)
    for result, (name, args) in zip(results, valid_calls):
        writer(
            {
                "event": "tool_result",
                "payload": {
                    "node": node,
                    "tool": result.tool_name,
                    "args": args,
                    "output": result.output[:500],
                    "duration_ms": result.duration_ms,
                    "success": result.success,
                },
            }
        )
    return list(zip(results, valid_calls))


async def _run_tool_loop(
    node: str,
    messages: list[dict[str, str]],
    active_tools: list,
    executor,
    registry,
    model_key: str,
    writer,
    max_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    temperature: float = 0.5,
    max_parse_retries: int = DEFAULT_MAX_PARSE_RETRIES,
) -> tuple[str, str | None, list[dict], TokenCounts, int]:
    """Run a multi-round tool-use loop. The model produces tool calls, they
    are executed, and results are fed back as assistant/user messages. The
    loop continues until the model stops producing tool calls or max_rounds
    is reached.

    If the model response cannot be parsed as tool calls (and is not a
    termination signal []), the model is given a correction prompt and
    retried up to max_parse_retries times before treating the round as
    a no-call (loop stops).

    Returns (final_response, thinking, all_tool_results, token_counts, call_count) where
    all_tool_results is a flat list of dicts with 'tool', 'args', 'output',
    'success', 'duration_ms' keys for every tool call across all rounds."""
    all_tool_results: list[dict] = []
    response = ""
    thinking = None
    token_counts = TokenCounts()
    call_count = 0

    for round_num in range(max_rounds):
        _log_prompts(node, messages)
        resolved = await registry.resolve(model_key)
        raw = await resolved.client.chat_completion(
            model=resolved.model_id, messages=messages, temperature=temperature
        )
        token_counts.accumulate(raw)
        call_count += 1
        response, thinking = _extract(node, raw)

        # If the response can't be parsed as tool calls and isn't a
        # termination signal, check if it looks like a failed attempt
        # to produce tool calls (contains JSON-like structures or
        # markdown code fences). If so, retry with a correction prompt.
        parsed = _parse_tool_calls(response)
        if (
            not parsed
            and response.strip() not in ("[]", "[]\n")
            and _looks_like_failed_tool_calls(response)
        ):
            parse_attempt = 0
            while parse_attempt < max_parse_retries:
                parse_attempt += 1
                logger.warning(
                    "[%s] Round %d: model response not parseable as tool calls, retry %d/%d",
                    node,
                    round_num + 1,
                    parse_attempt,
                    max_parse_retries,
                )
                messages = list(messages)
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": _PARSE_CORRECTION_PROMPT})
                _log_prompts(node, messages)
                raw = await resolved.client.chat_completion(
                    model=resolved.model_id,
                    messages=messages,
                    temperature=max(temperature - 0.2, 0.1),
                )
                token_counts.accumulate(raw)
                call_count += 1
                response, thinking = _extract(node, raw)
                parsed = _parse_tool_calls(response)
                if parsed or response.strip() in ("[]", "[]\n"):
                    break

        round_results = await _execute_tool_round(node, response, active_tools, executor, writer)

        if not round_results:
            logger.debug("[%s] No tool calls in round %d, stopping loop", node, round_num + 1)
            break

        for result, (name, args) in round_results:
            all_tool_results.append(
                {
                    "tool": name,
                    "args": args,
                    "output": result.output,
                    "success": result.success,
                    "duration_ms": result.duration_ms,
                }
            )

        tool_summary_parts: list[str] = []
        for result, (name, args) in round_results:
            status = "SUCCESS" if result.success else "FAILED"
            tool_summary_parts.append(f"Tool: {name}\nStatus: {status}\nResult:\n{result.output}")
        tool_summary = "\n\n---\n\n".join(tool_summary_parts)

        messages = list(messages)
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": f"Tool execution results:\n\n{tool_summary}"})

        logger.info(
            "[%s] Tool loop round %d: %d tool calls executed",
            node,
            round_num + 1,
            len(round_results),
        )

    return response, thinking, all_tool_results, token_counts, call_count


async def planning(state: ResearchState, config: RunnableConfig) -> dict:
    """Planning node: analyzes the question and produces a research plan.
    Uses the intelligence model. Cost weight: 2."""
    _check_stop("planning", config)
    cost_weights = state.get("cost_weights", {})
    budget = state.get("budget_remaining", 0.0)
    node = "planning"
    logger.debug("PLANNING Start")

    if not can_execute(cost_weights, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(cost_weights, node, budget)

    registry = _get_registry(config)
    question = state.get("question", "")
    verification_history = state.get("verification_history", [])
    prior_report = config.get("configurable", {}).get("prior_report")
    prior_question = config.get("configurable", {}).get("prior_question")
    prior_turns = config.get("configurable", {}).get("prior_turns")

    messages = _build_planning_messages(
        question,
        verification_history,
        prior_report,
        prior_question,
        prior_turns,
    )
    _log_prompts(node, messages)
    resolved = await registry.resolve("intelligence")
    raw = await resolved.client.chat_completion(
        model=resolved.model_id, messages=messages, temperature=0.7
    )
    plan, thinking = _extract(node, raw)

    _emit_node_end(
        writer,
        node,
        new_budget,
        detail=_build_detail(plan, thinking, messages=messages),
        purpose="intelligence",
        model=raw.model or resolved.model_id,
        call_count=1,
        input_tokens=raw.input_tokens,
        thinking_tokens=raw.thinking_tokens,
        output_tokens=raw.output_tokens,
        prompt_time_ms=raw.prompt_time_ms,
        gen_time_ms=raw.gen_time_ms,
    )

    logger.info("PLANNING complete, plan length=%d", len(plan))
    return {"plan": plan, "budget_remaining": new_budget}


async def tool_discovery(state: ResearchState, config: RunnableConfig) -> dict:
    """Tool Discovery node: embeds the plan and searches LanceDB for
    relevant tools. Optionally uses the task model to rewrite the plan
    into a tool-oriented search query for better semantic matching.
    Default tools are always included. Cost weight: 1."""
    _check_stop("tool_discovery", config)
    cost_weights = state.get("cost_weights", {})
    budget = state.get("budget_remaining", 0.0)
    node = "tool_discovery"

    logger.debug("TOOL DISCOVERY Start")
    if not can_execute(cost_weights, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(cost_weights, node, budget)

    from moira.service_setup import service_provider

    catalog = service_provider("tool_catalog")
    default_tools = catalog.get_default_tools()

    discovery = _get_discovery(config)
    plan = state.get("plan", "")

    # Resolve query rewriting setting. Falls back to true (enabled).
    rewrite_enabled = True
    try:
        from moira.services.settings.settings_service import SettingsService

        settings_svc = cast(SettingsService | None, service_provider("settings_service"))
        if settings_svc is not None:
            val = await settings_svc.get_typed("tool_discovery.query_rewriting")
            if val is not None:
                rewrite_enabled = bool(val)
    except Exception:
        logger.debug("Could not resolve query_rewriting setting, defaulting to true")

    search_query = plan
    rewrite_messages: list[dict[str, str]] = []

    if rewrite_enabled and plan:
        search_query, rewrite_messages = await _rewrite_discovery_query(plan, config)
        if not search_query:
            search_query = plan

    logger.info(
        "Tool discovery query: %s",
        search_query[:100] if search_query else "(empty)",
    )

    discovered = await discovery.discover(search_query, top_k=5)

    # Discovery returns stub ToolDefinitions (name + description only)
    # from the vector index. Hydrate them with full definitions from
    # the catalog so argument_schema and config are available downstream.
    from moira.tools.base import ToolDefinition as _TD

    hydrated = []
    for tool in discovered:
        full = catalog.get(tool.name)
        hydrated.append(full if isinstance(full, _TD) else tool)
    discovered = hydrated

    # Merge: discovered (domain-specific) tools first, then defaults. The
    # ordering biases the model toward domain-specific tools when picking
    # from the list, with the standard tools available as a fallback.
    default_names = {t.name for t in default_tools}
    active_tools = [t for t in discovered if t.name not in default_names]
    active_tools.extend(default_tools)

    detail = _build_detail(
        search_query if search_query != plan else "",
        messages=rewrite_messages or None,
        structured_output={
            "search_query": search_query,
            "query_rewritten": search_query != plan,
            "default_tools": [t.name for t in default_tools],
            "discovered_tools": [t.name for t in discovered if t.name not in default_names],
        },
    )
    _emit_node_end(writer, node, new_budget, detail=detail)

    logger.info(
        "Tool discovery: %d defaults + %d discovered = %d active",
        len(default_tools),
        len(discovered),
        len(active_tools),
    )
    logger.debug("TOOL DISCOVERY Complete")
    return {
        "active_tools": active_tools,
        "default_tool_names": [t.name for t in default_tools],
        "budget_remaining": new_budget,
    }


async def tool_selection(state: ResearchState, config: RunnableConfig) -> dict:
    """Tool Selection node: intelligence model selects the best tools from
    the discovered candidates. Cost weight: 2."""
    _check_stop("tool_selection", config)
    cost_weights = state.get("cost_weights", {})
    budget = state.get("budget_remaining", 0.0)
    node = "tool_selection"

    logger.debug("TOOL SELECTION Start")
    if not can_execute(cost_weights, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(cost_weights, node, budget)

    active_tools = state.get("active_tools", [])
    default_tool_names = set(state.get("default_tool_names") or [])
    if not active_tools:
        _emit_node_end(writer, node, new_budget)
        return {"budget_remaining": new_budget}

    question = state.get("question", "")
    plan = state.get("plan", "")
    tool_descriptions = _format_tool_descriptions(
        active_tools,
        discovered=[t for t in active_tools if t.name not in default_tool_names],
        defaults=[t for t in active_tools if t.name in default_tool_names],
    )

    messages = [
        {
            "role": "system",
            "content": get_prompt("tool_selection.system"),
        },
        {
            "role": "user",
            "content": get_prompt("tool_selection.user").format(
                question=question, plan=plan, tool_descriptions=tool_descriptions
            ),
        },
    ]

    registry = _get_registry(config)
    resolved = await registry.resolve("intelligence")
    _log_prompts(node, messages)
    raw = await resolved.client.chat_completion(
        model=resolved.model_id, messages=messages, temperature=0.3
    )
    response, thinking = _extract(node, raw)

    selected_names = _parse_json_list(response)
    selected_tools = [t for t in active_tools if t.name in selected_names]
    if not selected_tools:
        selected_tools = active_tools

    _emit_node_end(
        writer,
        node,
        new_budget,
        detail=_build_detail(
            response,
            thinking,
            messages=messages,
            structured_output={"selected_tools": [t.name for t in selected_tools]},
        ),
        purpose="intelligence",
        model=raw.model or resolved.model_id,
        call_count=1,
        input_tokens=raw.input_tokens,
        thinking_tokens=raw.thinking_tokens,
        output_tokens=raw.output_tokens,
        prompt_time_ms=raw.prompt_time_ms,
        gen_time_ms=raw.gen_time_ms,
    )
    logger.info("Tool selection chose %d tools", len(selected_tools))
    logger.debug("TOOL SELECTION Complete")
    return {"active_tools": selected_tools, "budget_remaining": new_budget}


async def research_execution(state: ResearchState, config: RunnableConfig) -> dict:
    """Research Execution node: intelligence model uses selected tools to
    gather evidence. Supports multi-round tool use. Cost weight: 5."""
    _check_stop("research_execution", config)
    cost_weights = state.get("cost_weights", {})
    budget = state.get("budget_remaining", 0.0)
    node = "research_execution"

    logger.debug("RESEARCH EXECUTION Start")
    if not can_execute(cost_weights, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(cost_weights, node, budget)

    active_tools = state.get("active_tools", [])
    default_tool_names = set(state.get("default_tool_names") or [])
    question = state.get("question", "")
    plan = state.get("plan", "")

    if not active_tools:
        _emit_node_end(writer, node, new_budget)
        return {"findings": [], "budget_remaining": new_budget}

    executor = _get_executor(config)
    registry = _get_registry(config)

    tool_descriptions = _format_tool_descriptions(
        active_tools,
        discovered=[t for t in active_tools if t.name not in default_tool_names],
        defaults=[t for t in active_tools if t.name in default_tool_names],
    )

    messages = [
        {
            "role": "system",
            "content": get_prompt("research_execution.system"),
        },
        {
            "role": "user",
            "content": get_prompt("research_execution.user").format(
                question=question, plan=plan, tool_descriptions=tool_descriptions
            ),
        },
    ]

    resolved_model_id = (await registry.resolve("intelligence")).model_id
    response, thinking, all_tool_results, tokens, loop_call_count = await _run_tool_loop(
        node, messages, active_tools, executor, registry, "intelligence", writer
    )

    from moira.models.state import Finding

    findings: list[Finding] = []
    for tr in all_tool_results:
        findings.append(
            Finding(
                content=tr["output"],
                source=tr["tool"],
                type="evidence" if tr["success"] else "note",
            )
        )

    detail = _build_detail(
        response,
        thinking,
        messages=messages,
    )

    _emit_node_end(
        writer,
        node,
        new_budget,
        detail=detail,
        purpose="intelligence",
        model=resolved_model_id,
        call_count=loop_call_count,
        input_tokens=tokens.input_tokens or None,
        thinking_tokens=tokens.thinking_tokens or None,
        output_tokens=tokens.output_tokens or None,
        prompt_time_ms=tokens.prompt_time_ms or None,
        gen_time_ms=tokens.gen_time_ms or None,
    )

    logger.info("Research execution produced %d findings", len(findings))
    logger.debug("RESEARCH EXECUTION Complete")
    return {"findings": findings, "budget_remaining": new_budget}


async def compression(state: ResearchState, config: RunnableConfig) -> dict:
    """Compression node: task model compresses and deduplicates findings.
    Cost weight: 1."""
    _check_stop("compression", config)
    cost_weights = state.get("cost_weights", {})
    budget = state.get("budget_remaining", 0.0)
    node = "compression"

    logger.debug("COMPRESSION Start")
    if not can_execute(cost_weights, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(cost_weights, node, budget)

    findings = state.get("findings", [])
    if not findings:
        _emit_node_end(writer, node, new_budget)
        return {"compressed_findings": [], "budget_remaining": new_budget}

    findings_text = "\n\n".join(f"[{f['source']}] {f['content']}" for f in findings)

    messages = [
        {
            "role": "system",
            "content": get_prompt("compression.system"),
        },
        {"role": "user", "content": findings_text},
    ]

    registry = _get_registry(config)
    resolved = await registry.resolve("task")
    _log_prompts(node, messages)
    raw = await resolved.client.chat_completion(
        model=resolved.model_id, messages=messages, temperature=0.3
    )
    compressed_text, thinking = _extract(node, raw)

    from moira.models.state import Finding

    compressed_findings = [Finding(content=compressed_text, source="compression", type="note")]

    _emit_node_end(
        writer,
        node,
        new_budget,
        detail=_build_detail(compressed_text, thinking, messages=messages),
        purpose="task",
        model=raw.model or resolved.model_id,
        call_count=1,
        input_tokens=raw.input_tokens,
        thinking_tokens=raw.thinking_tokens,
        output_tokens=raw.output_tokens,
        prompt_time_ms=raw.prompt_time_ms,
        gen_time_ms=raw.gen_time_ms,
    )

    logger.info("Compression complete, output length=%d", len(compressed_text))
    logger.debug("COMPRESSION Complete")
    return {
        "compressed_findings": compressed_findings,
        "budget_remaining": new_budget,
    }


async def draft_synthesis(state: ResearchState, config: RunnableConfig) -> dict:
    """Draft Synthesis node: intelligence model produces a draft answer.
    Cost weight: 3. On draft retry, includes verification feedback so the
    synthesizer can address specific issues."""
    _check_stop("draft_synthesis", config)
    cost_weights = state.get("cost_weights", {})
    budget = state.get("budget_remaining", 0.0)
    node = "draft_synthesis"
    draft_retry_count = state.get("draft_retry_count", 0)

    logger.debug("DRAFT SYNTHESIS Start (retry=%d)", draft_retry_count)
    if not can_execute(cost_weights, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(cost_weights, node, budget)

    question = state.get("question", "")
    plan = state.get("plan", "")
    findings = state.get("findings", [])
    evidence = "\n\n".join(f"[{f['source']}] {f['content']}" for f in findings)

    system_prompt = get_prompt("draft_synthesis.system")
    user_content = get_prompt("draft_synthesis.user").format(
        question=question, plan=plan, evidence=evidence
    )

    # On draft retry, append verification feedback so the synthesizer
    # knows what went wrong with the previous draft.
    if draft_retry_count > 0:
        verification_history = state.get("verification_history", [])
        if verification_history:
            last_report = verification_history[-1]
            system_prompt = get_prompt("draft_synthesis.system_retry").format(
                case=last_report.get("case", 0),
                assessment=last_report.get("assessment", ""),
                guidance=last_report.get("guidance", ""),
            )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    registry = _get_registry(config)
    resolved = await registry.resolve("intelligence")
    _log_prompts(node, messages)
    raw = await resolved.client.chat_completion(
        model=resolved.model_id, messages=messages, temperature=0.7
    )
    draft, thinking = _extract(node, raw)

    # Increment draft_retry_count so the router can guard against loops.
    # On first pass this is 0 -> 1; on retry pass this is 1 -> 2 (but
    # the router will not allow a second retry).
    state_update: dict = {"draft": draft, "budget_remaining": new_budget}
    if draft_retry_count == 0:
        # Normal first pass — leave as default (0)
        pass
    else:
        state_update["draft_retry_count"] = draft_retry_count + 1

    _emit_node_end(
        writer,
        node,
        new_budget,
        detail=_build_detail(draft, thinking, messages=messages),
        purpose="intelligence",
        model=raw.model or resolved.model_id,
        call_count=1,
        input_tokens=raw.input_tokens,
        thinking_tokens=raw.thinking_tokens,
        output_tokens=raw.output_tokens,
        prompt_time_ms=raw.prompt_time_ms,
        gen_time_ms=raw.gen_time_ms,
    )

    logger.info("Draft synthesis complete, length=%d", len(draft))
    logger.debug("DRAFT SYNTHESIS Complete")
    return state_update


async def verification(state: ResearchState, config: RunnableConfig) -> dict:
    """Verification node: adversarial review of the draft with tool-assisted
    fact-checking. Uses the same active tools as research_execution for
    independent claim verification. Cost weight: 4. Produces a
    VerificationReport and may route back to Planning if budget permits."""
    _check_stop("verification", config)
    cost_weights = state.get("cost_weights", {})
    budget = state.get("budget_remaining", 0.0)
    node = "verification"

    logger.debug("VERIFICATION Start")
    if not can_execute(cost_weights, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(cost_weights, node, budget)

    question = state.get("question", "")
    draft = state.get("draft", "")
    active_tools = state.get("active_tools", [])
    default_tool_names = set(state.get("default_tool_names") or [])

    # Phase 1: Fact-checking with tools (if tools are available).
    # The model investigates specific claims using the same tool set
    # that research_execution had, producing independent evidence.
    all_tool_results: list[dict] = []
    fact_check_messages: list[dict[str, str]] = []
    final_response = ""
    final_thinking: str | None = None
    fc_tokens = TokenCounts()
    fc_call_count = 0

    if active_tools:
        executor = _get_executor(config)
        registry = _get_registry(config)
        tool_descriptions = _format_tool_descriptions(
            active_tools,
            discovered=[t for t in active_tools if t.name not in default_tool_names],
            defaults=[t for t in active_tools if t.name in default_tool_names],
        )

        fact_check_messages = [
            {
                "role": "system",
                "content": get_prompt("verification.fact_check.system"),
            },
            {
                "role": "user",
                "content": get_prompt("verification.fact_check.user").format(
                    question=question, draft=draft, tool_descriptions=tool_descriptions
                ),
            },
        ]

        (
            fc_response,
            fc_thinking,
            all_tool_results,
            fc_tokens,
            fc_call_count,
        ) = await _run_tool_loop(
            node,
            fact_check_messages,
            active_tools,
            executor,
            registry,
            "intelligence",
            writer,
            max_rounds=DEFAULT_MAX_TOOL_ROUNDS,
            temperature=0.3,
        )
        logger.info(
            "Verification fact-check: %d tool calls across rounds",
            len(all_tool_results),
        )

    # Phase 2: Verdict. With or without tool evidence, produce the final
    # structured verdict. When tools were used, the fact-check evidence
    # is included in the prompt so the verdict is grounded in findings.
    registry = _get_registry(config)

    if all_tool_results:
        evidence_parts: list[str] = []
        for tr in all_tool_results:
            status = "SUCCESS" if tr["success"] else "FAILED"
            evidence_parts.append(f"[{tr['tool']}] ({status}) {tr['output']}")
        fact_check_evidence = "\n\n".join(evidence_parts)
    else:
        fact_check_evidence = "No tool-based fact-checking was performed."

    verdict_messages = [
        {
            "role": "system",
            "content": get_prompt("verification.system"),
        },
        {
            "role": "user",
            "content": get_prompt("verification.user").format(question=question, draft=draft),
        },
    ]

    if all_tool_results:
        verdict_messages.append(
            {
                "role": "user",
                "content": get_prompt("verification.evidence").format(
                    evidence=fact_check_evidence
                ),
            }
        )

    resolved = await registry.resolve("intelligence")
    _log_prompts(node, verdict_messages)
    raw = await resolved.client.chat_completion(
        model=resolved.model_id, messages=verdict_messages, temperature=0.5
    )
    final_response, final_thinking = _extract(node, raw)

    report_dict = _parse_json_object(final_response)

    # If the model didn't return valid JSON, treat it as a technical
    # failure (case 11). Set outcome to "error" so routing halts.
    if not report_dict:
        logger.warning(
            "Verification response was not valid JSON, treating as error. Response: %s",
            final_response[:500],
        )
        report_dict = {
            "outcome": "error",
            "case": 11,
            "assessment": "Verification response was not parseable JSON",
            "supported_claims": [],
            "unsupported_claims": [],
            "contradictions": [],
            "relevance": "on_topic",
            "depth": "sufficient",
            "guidance": "Technical failure during verification",
        }

    outcome = report_dict.get("outcome", "retry_plan")
    if outcome not in ("accept", "retry_plan", "retry_draft", "error"):
        logger.warning("Invalid verification outcome '%s', defaulting to retry_plan", outcome)
        outcome = "retry_plan"

    # If the draft is empty/garbage, force error outcome (case 10)
    if not draft.strip():
        outcome = "error"
        report_dict["case"] = 10
        report_dict["assessment"] = "Draft is empty or contains no meaningful content"

    report: VerificationReport = {
        "outcome": outcome,
        "case": report_dict.get("case", 0),
        "assessment": report_dict.get("assessment", ""),
        "supported_claims": report_dict.get("supported_claims", []),
        "unsupported_claims": report_dict.get("unsupported_claims", []),
        "contradictions": report_dict.get("contradictions", []),
        "relevance": report_dict.get("relevance", "on_topic"),
        "depth": report_dict.get("depth", "sufficient"),
        "guidance": report_dict.get("guidance", ""),
    }

    verification_history = list(state.get("verification_history", []))
    verification_history.append(report)

    # For state compatibility: error outcome sets the error field so
    # report_generation knows this was a technical failure.
    error_msg = ""
    if outcome == "error":
        error_msg = report["assessment"]

    writer(
        {
            "event": "verification_report",
            "payload": {
                "report": report,
                "attempt": len(verification_history),
            },
        }
    )

    # Build a copy of the report for the detail's structured_output.
    # We add a retry_declined note when the outcome is "retry_plan" but
    # there isn't enough budget for another full cycle. This copy keeps
    # the state's verification_history clean — only the UI sees the note.
    structured = dict(report)
    if outcome == "retry_plan":
        cycle_cost = full_cycle_cost(cost_weights)
        if new_budget < cycle_cost:
            structured["retry_declined"] = True
            structured["retry_declined_reason"] = (
                f"Insufficient budget for another cycle "
                f"({new_budget:.1f} remaining, {cycle_cost} needed)"
            )
    elif outcome == "retry_draft":
        from moira.workflow.budget import draft_retry_cost

        dr_cost = draft_retry_cost(cost_weights)
        draft_retries = state.get("draft_retry_count", 0)
        if new_budget < dr_cost or draft_retries >= 1:
            structured["retry_declined"] = True
            structured["retry_declined_reason"] = (
                f"Cannot retry draft "
                f"({new_budget:.1f} remaining, {dr_cost} needed, "
                f"{draft_retries} draft retries used)"
            )

    # Merge detail: include prompts from both phases plus tool results.
    # The verdict phase messages are the primary prompt for display, but
    # fact-check messages and tool results are also attached for transparency.
    detail = _build_detail(
        final_response,
        final_thinking,
        messages=verdict_messages,
        structured_output=structured,
    )
    if fact_check_messages:
        detail["fact_check_prompt"] = {"messages": fact_check_messages}
    if all_tool_results:
        detail["tool_results"] = [
            {
                "tool": tr["tool"],
                "args": tr["args"],
                "result": tr["output"],
                "duration_ms": tr["duration_ms"],
                "success": tr["success"],
            }
            for tr in all_tool_results
        ]
        detail["structured_output"] = structured
    else:
        detail["structured_output"] = structured

    total_tokens = TokenCounts()
    total_tokens.input_tokens = fc_tokens.input_tokens
    total_tokens.output_tokens = fc_tokens.output_tokens
    total_tokens.thinking_tokens = fc_tokens.thinking_tokens
    total_tokens.prompt_time_ms = fc_tokens.prompt_time_ms
    total_tokens.gen_time_ms = fc_tokens.gen_time_ms
    if raw.input_tokens:
        total_tokens.input_tokens += raw.input_tokens
    if raw.output_tokens:
        total_tokens.output_tokens += raw.output_tokens
    if raw.thinking_tokens:
        total_tokens.thinking_tokens += raw.thinking_tokens
    if raw.prompt_time_ms:
        total_tokens.prompt_time_ms += raw.prompt_time_ms
    if raw.gen_time_ms:
        total_tokens.gen_time_ms += raw.gen_time_ms
    total_call_count = fc_call_count + 1

    _emit_node_end(
        writer,
        node,
        new_budget,
        detail=detail,
        purpose="intelligence",
        model=raw.model or resolved.model_id,
        call_count=total_call_count,
        input_tokens=total_tokens.input_tokens or None,
        thinking_tokens=total_tokens.thinking_tokens or None,
        output_tokens=total_tokens.output_tokens or None,
        prompt_time_ms=total_tokens.prompt_time_ms or None,
        gen_time_ms=total_tokens.gen_time_ms or None,
    )

    logger.info(
        "Verification complete: outcome=%s, case=%d, assessment=%s",
        report["outcome"],
        report["case"],
        report["assessment"][:100],
    )
    logger.debug("VERIFICATION Complete")

    return {
        "verification": final_response,
        "verification_history": verification_history,
        "unverified_claims": report["unsupported_claims"],
        "budget_remaining": new_budget,
        "error": error_msg,
    }


async def report_generation(state: ResearchState, config: RunnableConfig) -> dict:
    """Report Generation node: terminal node that always runs (budget-exempt).
    Produces a structured ResearchReport. Robust to partial state from
    earlier node failures. Cost weight: 3 (accounting only)."""
    _check_stop("report_generation", config)
    cost_weights = state.get("cost_weights", {})
    budget = state.get("budget_remaining", 0.0)
    node = "report_generation"

    logger.debug("REPORT GENERATION Start")
    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    # Deduct cost for accounting but never prevent execution
    new_budget = deduct_cost(cost_weights, node, budget)

    question = state.get("question", "")
    draft = state.get("draft", "")
    findings = state.get("findings", [])
    compressed = state.get("compressed_findings", [])
    unverified = state.get("unverified_claims", [])
    error = state.get("error", "")

    is_error_path = bool(error)
    is_budget_exhausted = not is_error_path and len(unverified) > 0

    evidence_text = "\n".join(
        f["content"] for f in (compressed or findings or []) if f.get("content")
    )

    path_instruction = ""
    if is_error_path:
        path_instruction = get_prompt("report_generation.path_error").format(error=error)
    elif is_budget_exhausted:
        path_instruction = get_prompt("report_generation.path_budget_exhausted").format(
            unverified_claims="; ".join(unverified)
        )
    else:
        path_instruction = get_prompt("report_generation.path_verified")

    messages = [
        {
            "role": "system",
            "content": get_prompt("report_generation.system").format(
                path_instruction=path_instruction
            ),
        },
        {
            "role": "user",
            "content": get_prompt("report_generation.user").format(
                question=question, draft=draft, evidence=evidence_text
            ),
        },
    ]

    registry = _get_registry(config)
    thinking: str | None = None
    response_text = ""
    raw = None
    try:
        resolved = await registry.resolve("intelligence")
        _log_prompts(node, messages)
        raw = await resolved.client.chat_completion(
            model=resolved.model_id, messages=messages, temperature=0.5
        )
        response_text, thinking = _extract(node, raw)
        report_data = _parse_json_object(response_text)
    except Exception as e:
        logger.error("Report generation model call failed: %s", e)
        report_data = {}

    budget_limit = state.get("budget_limit", 0.0)
    budget_consumed = budget_limit - new_budget

    from moira.models.state import ResearchReport

    if is_error_path:
        generation_path = "error"
    elif is_budget_exhausted:
        generation_path = "budget_exhausted"
    else:
        generation_path = "verified"

    report: ResearchReport = {
        "answer": report_data.get("answer", draft or "Unable to generate answer."),
        "citations": report_data.get("citations", []),
        "support": report_data.get("support", []),
        "critiques": report_data.get("critiques", []),
        "unverified_claims": report_data.get("unverified_claims", unverified),
        "budget_consumed": budget_consumed,
        "generation_path": generation_path,
    }

    if is_error_path:
        report["critiques"] = report.get("critiques", []) + [f"Workflow interrupted: {error}"]

    detail = _build_detail(
        response_text,
        thinking,
        messages=messages,
        structured_output=report,
    )
    if raw is not None:
        _emit_node_end(
            writer,
            node,
            new_budget,
            detail=detail,
            purpose="intelligence",
            model=raw.model or resolved.model_id,
            call_count=1,
            input_tokens=raw.input_tokens,
            thinking_tokens=raw.thinking_tokens,
            output_tokens=raw.output_tokens,
            prompt_time_ms=raw.prompt_time_ms,
            gen_time_ms=raw.gen_time_ms,
        )
    else:
        _emit_node_end(
            writer,
            node,
            new_budget,
            detail=detail,
        )
    writer(
        {
            "event": "run_complete",
            "payload": {"report": report},
        }
    )

    logger.info("Report generation complete, answer length=%d", len(report["answer"]))
    logger.debug("REPORT GENERATION Complete")
    return {"report": report, "budget_remaining": new_budget}


def _get_registry(config: RunnableConfig):
    from moira.service_setup import service_provider

    return service_provider("model_registry")


async def _rewrite_discovery_query(
    plan: str, config: RunnableConfig
) -> tuple[str, list[dict[str, str]]]:
    """Use the task model to rewrite a research plan into a concise,
    tool-oriented search query optimized for semantic matching against
    tool descriptions in LanceDB. Returns (rewritten_query, messages)
    or ("", []) on failure."""
    registry = _get_registry(config)
    try:
        resolved = await registry.resolve("task")
    except (ValueError, KeyError):
        logger.debug("Task model not available for query rewriting")
        return "", []

    messages = [
        {
            "role": "system",
            "content": get_prompt("tool_discovery.query_rewrite.system"),
        },
        {
            "role": "user",
            "content": get_prompt("tool_discovery.query_rewrite.user").format(plan=plan),
        },
    ]
    _log_prompts("tool_discovery.rewrite", messages)

    try:
        raw = await resolved.client.chat_completion(
            model=resolved.model_id, messages=messages, temperature=0.3
        )
        text, _ = _extract("tool_discovery.rewrite", raw)
        for line in text.strip().splitlines():
            cleaned = line.strip().lstrip("0123456789.-) ")
            if cleaned:
                return cleaned, messages
        return "", messages
    except Exception as e:
        logger.debug("Query rewriting failed: %s", e)
        return "", []


def _get_discovery(config: RunnableConfig):
    from moira.service_setup import service_provider

    return service_provider("tool_discovery")


def _get_executor(config: RunnableConfig):
    from moira.service_setup import service_provider

    return service_provider("tool_executor")


def _build_planning_messages(
    question: str,
    verification_history: list[VerificationReport],
    prior_report: dict | None,
    prior_question: str | None = None,
    prior_turns: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    system = get_prompt("planning.system")
    if verification_history:
        latest = verification_history[-1]
        system += "\n\n" + get_prompt("planning.system_retry").format(
            case=latest.get("case", 0),
            assessment=latest.get("assessment", ""),
            guidance=latest.get("guidance", ""),
        )

    messages = [{"role": "system", "content": system}]

    if prior_turns and len(prior_turns) > 1:
        earlier = prior_turns[:-1]
        history_lines = []
        for i, turn in enumerate(earlier, 1):
            history_lines.append(f"Turn {i} question: {turn['question']}")
            answer_preview = turn.get("answer", "")[:500]
            history_lines.append(f"Turn {i} answer: {answer_preview}")
        messages.append(
            {
                "role": "system",
                "content": get_prompt("planning.system_earlier_turns").format(
                    earlier_turns="\n\n".join(history_lines)
                ),
            }
        )

    if prior_report:
        messages.append(
            {
                "role": "system",
                "content": get_prompt("planning.system_prior_report").format(
                    prior_question=prior_question or "(not available)",
                    prior_report_answer=prior_report.get("answer", ""),
                ),
            }
        )

    messages.append({"role": "user", "content": question})
    return messages


def _parse_json_list(text: str) -> list[str]:
    import json
    import re

    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return []


def _parse_json_object(text: str) -> dict:
    import json
    import re

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def _parse_tool_calls(text: str) -> list[tuple[str, dict]]:
    """Parse tool calls from model response. Handles formats:
    1. JSON array: [{"tool": "name", "args": {...}}, ...]
    2. Line-delimited JSON objects: {"tool": "name", "args": {...}}
    3. Markdown-fenced arrays (one single-element array per line):
       ```json
       [{"tool": "name", "args": {...}}]
       [{"tool": "name", "args": {...}}]
       ```
    Local models often produce formats 2 and 3."""
    import json
    import re

    # Try JSON array format first (single valid array)
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

    # Fallback: strip markdown fences, then try each line as either
    # a JSON object or a single-element JSON array. This handles the
    # common case where the model wraps each tool call in its own
    # array on separate lines inside a code block.
    stripped = re.sub(r"```\w*\n?", "", text).strip()
    result = []
    for line in stripped.splitlines():
        line = line.strip()
        if not line or line in ("```",):
            continue
        # Try as a JSON array first (single-element like [{"tool":...}])
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
        # Try as a bare JSON object
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


def _looks_like_tool_calls(text: str) -> bool:
    """Heuristic: does this response look like a JSON array of tool calls
    rather than a prose synthesis? Used to decide whether to capture the
    final response as a synthesis finding."""
    stripped = text.strip()
    if not stripped.startswith("["):
        return False
    calls = _parse_tool_calls(stripped)
    return len(calls) > 0


def _looks_like_failed_tool_calls(text: str) -> bool:
    """Heuristic: does this response look like the model was *trying* to
    produce tool calls but failed? Triggers a parse-retry. We check for
    JSON-like structures, markdown code fences, or the word "tool" near
    structured content. Prose responses should NOT trigger this."""
    stripped = text.strip()
    # If it already parses correctly, it's not a failure
    if _looks_like_tool_calls(stripped):
        return False
    # Empty array is a valid termination signal, not a failure
    if stripped == "[]" or stripped == "[]\n":
        return False
    # Markdown code fence wrapping JSON-like content
    if "```" in stripped and ("tool" in stripped.lower() or "{" in stripped):
        return True
    # Starts with a bracket but didn't parse (malformed array)
    if stripped.startswith("["):
        return True
    return False


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
