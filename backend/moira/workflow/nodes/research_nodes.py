import logging
from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.config import MoiraConfig
from moira.models.state import ResearchState, VerificationReport
from moira.prompts import get_prompt
from moira.workflow.budget import can_execute, deduct_cost

logger = logging.getLogger(__name__)


def _log_prompts(node: str, messages: list[dict[str, str]]) -> None:
    """Log the system and user prompts at DEBUG level for transparency."""
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        logger.debug("[%s] %s prompt:\n%s", node, role.upper(), content)


def _log_response(node: str, response: str) -> None:
    """Log the model's response at DEBUG level for transparency."""
    logger.debug("[%s] model response:\n%s", node, response[:4000])


def _extract(node: str, result) -> tuple[str, dict[str, str]]:
    """Extract content and thinking from a ChatResponse. Logs both at DEBUG.
    Returns (content, thinking_update) where thinking_update is a dict suitable
    for merging into state's thinking_traces."""
    from moira.inference.client import ChatResponse

    if isinstance(result, ChatResponse):
        if result.thinking:
            logger.debug("[%s] model thinking:\n%s", node, result.thinking[:4000])
        _log_response(node, result.content)
        return result.content, {"thinking_traces": {node: result.thinking}}
    # Fallback for tests that return plain strings
    _log_response(node, str(result))
    return str(result), {}


async def planning(state: ResearchState, config: RunnableConfig) -> dict:
    """Planning node: analyzes the question and produces a research plan.
    Uses the intelligence model. Cost weight: 2."""
    moira_config = _get_config(config)
    budget = state.get("budget_remaining", 0.0)
    node = "planning"
    logger.debug("PLANNING Start")

    if not can_execute(moira_config, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(moira_config, node, budget)

    registry = _get_registry(config)
    question = state.get("question", "")
    verification_history = state.get("verification_history", [])
    prior_report = config.get("configurable", {}).get("prior_report")

    messages = _build_planning_messages(question, verification_history, prior_report)
    _log_prompts(node, messages)
    resolved = await registry.resolve("intelligence")
    raw = await resolved.client.chat_completion(
        model=resolved.model_id, messages=messages, temperature=0.7
    )
    plan, thinking_update = _extract(node, raw)

    writer(
        {
            "event": "node_end",
            "payload": {"node": node, "timestamp": _now(), "budget_remaining": new_budget},
        }
    )

    logger.info("PLANNING complete, plan length=%d", len(plan))
    return {"plan": plan, "budget_remaining": new_budget, **thinking_update}


async def tool_discovery(state: ResearchState, config: RunnableConfig) -> dict:
    """Tool Discovery node: embeds the plan and searches LanceDB for
    relevant tools. No model call. Cost weight: 1."""
    moira_config = _get_config(config)
    budget = state.get("budget_remaining", 0.0)
    node = "tool_discovery"

    logger.debug("TOOL DISCOVERY Start")
    if not can_execute(moira_config, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(moira_config, node, budget)

    discovery = _get_discovery(config)
    plan = state.get("plan", "")
    discovered = await discovery.discover(plan, top_k=5)

    writer(
        {
            "event": "node_end",
            "payload": {"node": node, "timestamp": _now(), "budget_remaining": new_budget},
        }
    )

    logger.info("Tool discovery found %d tools", len(discovered))
    logger.debug("TOOL DISCOVERY Complete")
    return {"active_tools": discovered, "budget_remaining": new_budget}


async def tool_selection(state: ResearchState, config: RunnableConfig) -> dict:
    """Tool Selection node: intelligence model selects the best tools from
    the discovered candidates. Cost weight: 2."""
    moira_config = _get_config(config)
    budget = state.get("budget_remaining", 0.0)
    node = "tool_selection"

    logger.debug("TOOL SELECTION Start")
    if not can_execute(moira_config, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(moira_config, node, budget)

    active_tools = state.get("active_tools", [])
    if not active_tools:
        writer(
            {
                "event": "node_end",
                "payload": {"node": node, "timestamp": _now(), "budget_remaining": new_budget},
            }
        )
        return {"budget_remaining": new_budget}

    question = state.get("question", "")
    plan = state.get("plan", "")
    tool_descriptions = "\n".join(f"- {t.name}: {t.description}" for t in active_tools)

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
    response, thinking_update = _extract(node, raw)

    selected_names = _parse_json_list(response)
    selected_tools = [t for t in active_tools if t.name in selected_names]
    if not selected_tools:
        selected_tools = active_tools

    writer(
        {
            "event": "node_end",
            "payload": {"node": node, "timestamp": _now(), "budget_remaining": new_budget},
        }
    )

    logger.info("Tool selection chose %d tools", len(selected_tools))
    logger.debug("TOOL SELECTION Complete")
    return {"active_tools": selected_tools, "budget_remaining": new_budget, **thinking_update}


async def research_execution(state: ResearchState, config: RunnableConfig) -> dict:
    """Research Execution node: intelligence model uses selected tools to
    gather evidence. Cost weight: 5."""
    moira_config = _get_config(config)
    budget = state.get("budget_remaining", 0.0)
    node = "research_execution"

    logger.debug("RESEARCH EXECUTION Start")
    if not can_execute(moira_config, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(moira_config, node, budget)

    active_tools = state.get("active_tools", [])
    question = state.get("question", "")
    plan = state.get("plan", "")

    if not active_tools:
        writer(
            {
                "event": "node_end",
                "payload": {"node": node, "timestamp": _now(), "budget_remaining": new_budget},
            }
        )
        return {"findings": [], "budget_remaining": new_budget}

    executor = _get_executor(config)
    registry = _get_registry(config)

    tool_descriptions = "\n".join(f"- {t.name}: {t.description}" for t in active_tools)

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

    resolved = await registry.resolve("intelligence")
    _log_prompts(node, messages)
    raw = await resolved.client.chat_completion(
        model=resolved.model_id, messages=messages, temperature=0.5
    )
    response, thinking_update = _extract(node, raw)

    tool_calls = _parse_tool_calls(response)
    results = await executor.execute_batch(tool_calls)

    from moira.models.state import Finding

    findings: list[Finding] = []
    for result in results:
        writer(
            {
                "event": "tool_result",
                "payload": {
                    "tool": result.tool_name,
                    "output": result.output[:500],
                    "duration_ms": result.duration_ms,
                    "success": result.success,
                },
            }
        )
        findings.append(
            Finding(
                content=result.output,
                source=result.tool_name,
                type="evidence" if result.success else "note",
            )
        )

    writer(
        {
            "event": "node_end",
            "payload": {"node": node, "timestamp": _now(), "budget_remaining": new_budget},
        }
    )

    logger.info("Research execution produced %d findings", len(findings))
    logger.debug("RESEARCH EXECUTION Complete")
    return {"findings": findings, "budget_remaining": new_budget, **thinking_update}


async def compression(state: ResearchState, config: RunnableConfig) -> dict:
    """Compression node: task model compresses and deduplicates findings.
    Cost weight: 1."""
    moira_config = _get_config(config)
    budget = state.get("budget_remaining", 0.0)
    node = "compression"

    logger.debug("COMPRESSION Start")
    if not can_execute(moira_config, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(moira_config, node, budget)

    findings = state.get("findings", [])
    if not findings:
        writer(
            {
                "event": "node_end",
                "payload": {"node": node, "timestamp": _now(), "budget_remaining": new_budget},
            }
        )
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
    compressed_text, thinking_update = _extract(node, raw)

    from moira.models.state import Finding

    compressed_findings = [Finding(content=compressed_text, source="compression", type="note")]

    writer(
        {
            "event": "node_end",
            "payload": {"node": node, "timestamp": _now(), "budget_remaining": new_budget},
        }
    )

    logger.info("Compression complete, output length=%d", len(compressed_text))
    logger.debug("COMPRESSION Complete")
    return {
        "compressed_findings": compressed_findings,
        "budget_remaining": new_budget,
        **thinking_update,
    }


async def draft_synthesis(state: ResearchState, config: RunnableConfig) -> dict:
    """Draft Synthesis node: intelligence model produces a draft answer.
    Cost weight: 3."""
    moira_config = _get_config(config)
    budget = state.get("budget_remaining", 0.0)
    node = "draft_synthesis"

    logger.debug("DRAFT SYNTHESIS Start")
    if not can_execute(moira_config, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(moira_config, node, budget)

    question = state.get("question", "")
    plan = state.get("plan", "")
    compressed = state.get("compressed_findings", [])
    evidence = "\n\n".join(f["content"] for f in compressed)

    messages = [
        {
            "role": "system",
            "content": get_prompt("draft_synthesis.system"),
        },
        {
            "role": "user",
            "content": get_prompt("draft_synthesis.user").format(
                question=question, plan=plan, evidence=evidence
            ),
        },
    ]

    registry = _get_registry(config)
    resolved = await registry.resolve("intelligence")
    _log_prompts(node, messages)
    raw = await resolved.client.chat_completion(
        model=resolved.model_id, messages=messages, temperature=0.7
    )
    draft, thinking_update = _extract(node, raw)

    writer(
        {
            "event": "node_end",
            "payload": {"node": node, "timestamp": _now(), "budget_remaining": new_budget},
        }
    )

    logger.info("Draft synthesis complete, length=%d", len(draft))
    logger.debug("DRAFT SYNTHESIS Complete")
    return {"draft": draft, "budget_remaining": new_budget, **thinking_update}


async def verification(state: ResearchState, config: RunnableConfig) -> dict:
    """Verification node: adversarial review of the draft using tools.
    Cost weight: 4. Produces a VerificationReport and may route back to
    Planning if budget permits."""
    moira_config = _get_config(config)
    budget = state.get("budget_remaining", 0.0)
    node = "verification"

    logger.debug("VERIFICATION Start")
    if not can_execute(moira_config, node, budget):
        return {"error": f"Insufficient budget for {node}"}

    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    new_budget = deduct_cost(moira_config, node, budget)

    question = state.get("question", "")
    draft = state.get("draft", "")

    messages = [
        {
            "role": "system",
            "content": get_prompt("verification.system"),
        },
        {
            "role": "user",
            "content": get_prompt("verification.user").format(question=question, draft=draft),
        },
    ]

    registry = _get_registry(config)
    resolved = await registry.resolve("intelligence")
    _log_prompts(node, messages)
    raw = await resolved.client.chat_completion(
        model=resolved.model_id, messages=messages, temperature=0.5
    )
    response, thinking_update = _extract(node, raw)

    report_dict = _parse_json_object(response)

    # If the model didn't return valid JSON, treat it as a technical
    # failure (case 11). Set outcome to "error" so routing halts.
    if not report_dict:
        logger.warning(
            "Verification response was not valid JSON, treating as error. Response: %s",
            response[:500],
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

    outcome = report_dict.get("outcome", "retry")
    if outcome not in ("accept", "retry", "error"):
        logger.warning("Invalid verification outcome '%s', defaulting to retry", outcome)
        outcome = "retry"

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
    writer(
        {
            "event": "node_end",
            "payload": {"node": node, "timestamp": _now(), "budget_remaining": new_budget},
        }
    )

    logger.info(
        "Verification complete: outcome=%s, case=%d, assessment=%s",
        report["outcome"],
        report["case"],
        report["assessment"][:100],
    )
    logger.debug("VERIFICATION Complete")

    return {
        "verification": response,
        "verification_history": verification_history,
        "unverified_claims": report["unsupported_claims"],
        "budget_remaining": new_budget,
        "error": error_msg,
        **thinking_update,
    }


async def report_generation(state: ResearchState, config: RunnableConfig) -> dict:
    """Report Generation node: terminal node that always runs (budget-exempt).
    Produces a structured ResearchReport. Robust to partial state from
    earlier node failures. Cost weight: 3 (accounting only)."""
    moira_config = _get_config(config)
    budget = state.get("budget_remaining", 0.0)
    node = "report_generation"

    logger.debug("REPORT GENERATION Start")
    writer = get_stream_writer()
    writer({"event": "node_start", "payload": {"node": node, "timestamp": _now()}})

    # Deduct cost for accounting but never prevent execution
    new_budget = deduct_cost(moira_config, node, budget)

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
    thinking_update = {}
    try:
        resolved = await registry.resolve("intelligence")
        _log_prompts(node, messages)
        raw = await resolved.client.chat_completion(
            model=resolved.model_id, messages=messages, temperature=0.5
        )
        response, thinking_update = _extract(node, raw)
        report_data = _parse_json_object(response)
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

    writer(
        {
            "event": "run_complete",
            "payload": {"report": report},
        }
    )
    writer(
        {
            "event": "node_end",
            "payload": {"node": node, "timestamp": _now(), "budget_remaining": new_budget},
        }
    )

    logger.info("Report generation complete, answer length=%d", len(report["answer"]))
    logger.debug("REPORT GENERATION Complete")
    return {"report": report, "budget_remaining": new_budget, **thinking_update}


def _get_config(config: RunnableConfig) -> MoiraConfig:
    cfg = config.get("configurable", {}).get("moira_config")
    if cfg is None:
        from moira.service_setup import service_provider

        return cast(MoiraConfig, service_provider("config"))
    return cfg


def _get_registry(config: RunnableConfig):
    from moira.service_setup import service_provider

    return service_provider("model_registry")


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

    if prior_report:
        messages.append(
            {
                "role": "system",
                "content": get_prompt("planning.system_prior_report").format(
                    prior_report_answer=prior_report.get("answer", "")
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
    import json
    import re

    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        calls = json.loads(match.group())
        result = []
        for call in calls:
            name = call.get("tool", "")
            args = call.get("args", {})
            if name:
                result.append((name, args))
        return result
    except (json.JSONDecodeError, TypeError):
        return []


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
