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
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable, cast

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.adapters import get_adapter
from moira.inference.client import ChatResponse
from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.inference.registry import ResolvedModel
from moira.models.knowledge import Citation, Fact, ResearchState, next_id
from moira.prompts import render_prompt
from moira.tools.base import ToolCall, ToolDefinition
from moira.tools.executor import ToolExecutor
from moira.workflow.budget import can_execute, deduct_cost
from moira.workflow.nodes._helpers import (
    _SNIPPET_MAX_LENGTH,
    _format_established_facts,
    _format_prior_citations,
    _format_prior_conclusions,
    _now,
    _parse_json_object,
    _response_meta,
)
from moira.workflow.nodes._helpers_deps import (
    _check_stop,
    _resolve_intelligence,
)

logger = logging.getLogger(__name__)

NODE_NAME = "research"

DEFAULT_MAX_ROUNDS = 3
DEFAULT_MAX_PARSE_RETRIES = 2

_DISPLAY_OUTPUT_LIMIT = 2000

# Cap for Citation.content — the source text stored for downstream
# cross-referencing in review/evaluation.  Larger than excerpt (500) because
# this is the substantive body, but bounded to avoid state-size bloat.
_CITATION_CONTENT_LIMIT = 5000


def _truncate_for_display(text: str | None, limit: int = _DISPLAY_OUTPUT_LIMIT) -> str:
    """Truncate text for the tool_result display event.

    Adds a note at the end when content is omitted so the user knows
    the full output was longer.
    """
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... ({len(text) - limit:,} more chars not shown)"


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
    """Format non-verified facts for the research prompt.

    Includes 'unknown', 'unverified', and 'contradicted' facts — all
    represent gaps that research should address.  'verified' facts are
    excluded since they are already resolved.
    """
    lines = []
    for f in facts:
        if f.get("status") != "verified":
            lines.append(f"{f['id']} | {f['subject']} | {f['fact_needed']}")
    return "\n".join(lines)


def _make_tool_call(name: str, args: object) -> ToolCall | None:
    """Build a ToolCall with a generated ID, or return None if name is empty."""
    if not name:
        return None
    return ToolCall(
        id=f"tc_{uuid.uuid4().hex[:8]}",
        name=name,
        arguments=args if isinstance(args, dict) else {},
    )


def _parse_tool_calls(text: str) -> list[ToolCall]:
    """Parse tool calls from model response. Handles formats:
    1. JSON array: [{"tool": "name", "args": {...}}, ...]
    2. Line-delimited JSON objects: {"tool": "name", "args": {...}}
    3. Markdown-fenced arrays
    Local models often produce formats 2 and 3.

    Generates a unique ``id`` for each call so downstream code never
    needs to check for missing IDs.
    """
    import json

    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            calls = json.loads(match.group())
            if isinstance(calls, list):
                result = []
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    name = call.get("tool") or call.get("name", "")
                    args = call.get("args") or call.get("arguments", {})
                    tc = _make_tool_call(name, args)
                    if tc:
                        result.append(tc)
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
                            name = call.get("tool") or call.get("name", "")
                            args = call.get("args") or call.get("arguments", {})
                            tc = _make_tool_call(name, args)
                            if tc:
                                result.append(tc)
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                name = obj.get("tool") or obj.get("name", "")
                args = obj.get("args") or obj.get("arguments", {})
                tc = _make_tool_call(name, args)
                if tc:
                    result.append(tc)
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


def _extract_tool_calls(parsed: dict) -> list[ToolCall]:
    """Extract tool calls from the parsed JSON object response.

    The model returns {tool_calls: [{tool, args}, ...], ...}.
    Returns a list of ToolCall objects with generated IDs.
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
        tc = _make_tool_call(name, args)
        if tc:
            result.append(tc)
    return result


def _validate_and_filter_calls(
    parsed_calls: list[ToolCall],
    allowed_names: set[str],
    call_limits: dict[str, int],
    call_counts: dict[str, int],
    required_params: dict[str, set[str]],
) -> tuple[list[ToolCall], list[str]]:
    """Filter tool calls by allowed names, call limits, and required params.

    Returns ``(valid_calls, rejected_names)``.
    """
    valid_calls: list[ToolCall] = []
    rejected: list[ToolCall] = []
    for call in parsed_calls:
        if call.name not in allowed_names:
            rejected.append(call)
            continue
        limit = call_limits.get(call.name, 0)
        used = call_counts.get(call.name, 0)
        if limit > 0 and used >= limit:
            logger.warning(
                "Tool %s hit call limit (%d/%d), skipping",
                call.name,
                used,
                limit,
            )
            continue
        required = required_params.get(call.name)
        if required:
            missing = [
                p
                for p in required
                if p not in call.arguments or not str(call.arguments[p]).strip()
            ]
            if missing:
                logger.warning(
                    "Tool %s missing required params: %s, skipping",
                    call.name,
                    missing,
                )
                continue
        valid_calls.append(call)
    return valid_calls, [c.name for c in rejected]


def _process_execution_results(
    results: list,
    valid_calls: list[ToolCall],
    writer: Callable[[dict[str, Any]], None],
    citations: list[Citation],
    seen_urls: dict[str, str],
    facts: list[Fact],
    tool_plan: list,
    tool_results_log: list[dict],
    call_counts: dict[str, int],
    tool_costs: dict[str, float],
    new_budget: float,
    total_tool_cost: float,
) -> tuple[list[str], float, float]:
    """Process execution results into citations, summaries, and cost tracking.

    Mutates ``citations``, ``seen_urls``, ``facts``, ``tool_results_log``,
    and ``call_counts`` in place. Returns ``(tool_summary_parts,
    new_budget, total_tool_cost)``.
    """
    tool_summary_parts: list[str] = []
    for result, call in zip(results, valid_calls):
        name = call.name
        args = call.arguments
        writer(
            {
                "event": "tool_result",
                "payload": {
                    "tool": result.tool_name,
                    "args": args,
                    "output": _truncate_for_display(result.output),
                    "duration_ms": result.duration_ms,
                    "success": result.success,
                    "node": NODE_NAME,
                    "metadata": result.metadata,
                },
            }
        )

        # Tools that provide structured metadata (web_search, url_content)
        # always carry a "results" key — even when empty.  This distinguishes
        # "search returned 0 hits" (skip citation creation) from "tool has
        # no metadata at all" (calculator — fall to Path B).
        structured = result.metadata.get("results") if result.metadata else None
        if structured is not None:
            for sr in structured:
                cit_id, is_new = _find_or_merge_citation(
                    citations,
                    seen_urls,
                    source=result.tool_name,
                    url=sr.get("url") or None,
                    title=sr.get("title") or None,
                    snippet=sr.get("snippet") or None,
                    content=sr.get("content") or sr.get("snippet") or None,
                )
                status = "SUCCESS" if result.success else "FAILED"
                recurring = "" if is_new else " (recurring source)"
                tool_summary_parts.append(
                    f"[{cit_id}] Tool: {name}\nStatus: {status}{recurring}\n"
                    f"Title: {sr.get('title', '')}\n"
                    f"URL: {sr.get('url', '')}\n"
                    f"Snippet: {sr.get('snippet', '')}"
                )
            if not structured:
                # Tool returned structured metadata but zero results (e.g.,
                # web_search with all engines suspended).  Log the call
                # without creating a noise citation.
                status = "SUCCESS" if result.success else "FAILED"
                tool_summary_parts.append(
                    f"Tool: {name}\nStatus: {status}\n"
                    f"Result: {result.output[:_SNIPPET_MAX_LENGTH] if result.output else ''}"
                )
        else:
            cit_id = next_id("cit", citations)
            citations.append(
                Citation(
                    id=cit_id,
                    source=result.tool_name,
                    excerpt=result.output[:_SNIPPET_MAX_LENGTH] if result.output else "",
                    content=result.output[:_CITATION_CONTENT_LIMIT] if result.output else "",
                )
            )
            status = "SUCCESS" if result.success else "FAILED"
            tool_summary_parts.append(
                f"[{cit_id}] Tool: {name}\nStatus: {status}\nResult:\n{result.output}"
            )

        tool_results_log.append(
            {
                "tool": result.tool_name,
                "args": args,
                "output": result.output[:_SNIPPET_MAX_LENGTH] if result.output else "",
                "duration_ms": result.duration_ms,
                "success": result.success,
                "metadata": result.metadata,
            }
        )

        if result.success and result.output:
            for planned in tool_plan:
                if planned["tool"] == name:
                    target_ids = planned.get("target_fact_ids", [])
                    for fact in facts:
                        if fact["id"] in target_ids and fact["status"] == "unknown":
                            fact["status"] = "unverified"

        call_counts[name] = call_counts.get(name, 0) + 1
        per_call_cost = tool_costs.get(name, 1.0)
        new_budget -= per_call_cost
        total_tool_cost += per_call_cost

    return tool_summary_parts, new_budget, total_tool_cost


# ---------------------------------------------------------------------------
# Process-metadata claim detection
#
# The model sometimes writes observations about the research *process* as
# claims in discovered_facts — e.g. "Insufficient data found", "No sources
# found that define X".  These are not factual assertions extracted from
# sources; they are commentary on the search outcome.  When stored as a
# fact's claim they pollute downstream synthesis, evaluation, and reporting.
#
# The patterns below match the common phrasings the model uses.  Matching
# is anchored at the start of the normalised claim to avoid false positives
# on legitimate claims that merely contain a keyword like "insufficient".
# (e.g. "Insufficient funding led to the project's cancellation" does NOT
# match because "funding" is not a data/information/evidence keyword.)
# ---------------------------------------------------------------------------

_PROCESS_METADATA_PATTERNS: list[re.Pattern[str]] = [
    # "Insufficient/Inadequate/Limited data/information/evidence ..."
    re.compile(
        r"(?:insufficient|inadequate|incomplete|limited)\s+"
        r"(?:data|information|evidence|sources?)",
        re.I,
    ),
    # "No data/information/sources/evidence/results ..."
    re.compile(
        r"no\s+(?:data|information|sources?|evidence|results?|relevant\s+\w+)\b",
        re.I,
    ),
    # "Not enough data/information/evidence"
    re.compile(r"not enough (?:data|information|evidence|sources?)", re.I),
    # "Unable/Could not/Cannot/Failed to find/determine/..."
    re.compile(
        r"(?:unable\s+to|could\s+not|cannot|failed\s+to|can't)\s+"
        r"(?:find|locate|determine|verify|identify|retrieve)",
        re.I,
    ),
    # "Data/Information/Sources not available/found"
    re.compile(
        r"(?:data|information|sources?)\s+(?:is\s+|are\s+)?not\s+"
        r"(?:available|found|located|present)",
        re.I,
    ),
    # "No further information/data/details"
    re.compile(r"no further (?:data|information|details|sources?)", re.I),
]


def _is_process_metadata_claim(claim: str) -> bool:
    """Return ``True`` when *claim* describes the research process rather
    than a factual assertion extracted from sources.

    This catches model outputs like "Insufficient data found" or "No sources
    found that define anemoia" so they can be treated as if no claim was
    provided — the fact is left in its prior state instead of being polluted
    with process commentary.

    The check is deliberately conservative: patterns must match at the start
    of the normalised (stripped, lowercased) claim.  A purely empty string
    returns ``False`` — callers handle emptiness separately.
    """
    normalized = claim.strip().lower()
    if not normalized:
        return False
    return any(p.match(normalized) for p in _PROCESS_METADATA_PATTERNS)


def _apply_discovered_facts(parsed: dict, facts: list[Fact]) -> None:
    """Apply discovered_facts from a model response to the facts list.

    For facts with a matching fact_id and status "unknown" or "unverified",
    updates the claim, relation, value, and status to "unverified" — but
    only if the model provided a non-empty, non-process-metadata claim.
    An empty or process-metadata claim (e.g. "Insufficient data found")
    causes the entire update to be skipped so the fact stays in its prior
    state.

    For new facts (fact_id is null/missing with fact_needed), appends them.

    Also applies to "unverified" facts so the model can improve a claim that
    was initially set by plan-matching (raw tool output) with a proper
    extracted claim from discovered_facts.
    """
    discovered = parsed.get("discovered_facts", [])
    if not isinstance(discovered, list):
        return
    for disc in discovered:
        if not isinstance(disc, dict):
            continue
        fact_id = disc.get("fact_id")
        if fact_id:
            matched = False
            for fact in facts:
                if fact["id"] == fact_id and fact["status"] in ("unknown", "unverified"):
                    matched = True
                    new_claim = (disc.get("claim") or "").strip()
                    if not new_claim:
                        # Model returned no claim for this fact.  Skip the
                        # entire update so the fact keeps its current state.
                        # Without this guard, status would be set to
                        # "unverified" with an empty claim, creating a
                        # phantom fact that downstream nodes can't use.
                        logger.warning(
                            "RESEARCH: model returned empty claim for %s, skipping",
                            fact_id,
                        )
                        break
                    if _is_process_metadata_claim(new_claim):
                        # Model wrote a process observation (e.g. "Insufficient
                        # data found") instead of a factual claim.  Treat it
                        # the same as an empty claim — skip the update so the
                        # fact stays in its prior state and can be re-researched.
                        logger.warning(
                            "RESEARCH: model returned process-metadata claim "
                            "for %s: %r — skipping",
                            fact_id,
                            new_claim[:80],
                        )
                        break
                    fact["claim"] = new_claim
                    if disc.get("relation"):
                        fact["relation"] = disc["relation"]
                    if disc.get("value"):
                        fact["value"] = disc["value"]
                    # Merge citation_ids from the model's response into the
                    # fact's existing set. The model references source IDs
                    # (e.g., "cit001") that were labeled in the tool feedback.
                    disc_cites = disc.get("citation_ids", [])
                    if disc_cites:
                        existing = set(fact.get("citation_ids", []))
                        existing.update(c for c in disc_cites if isinstance(c, str))
                        fact["citation_ids"] = sorted(existing)
                    fact["status"] = "unverified"
                    break
            if not matched:
                logger.warning(
                    "RESEARCH: discovered_fact fact_id=%r does not match any "
                    "existing fact — claim will be dropped",
                    fact_id,
                )
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


def _cleanup_empty_claims(facts: list[Fact]) -> None:
    """Revert "unverified" facts whose claims are empty or process metadata.

    Two cases are handled:

    1. **Empty claims** — During research, ``_process_execution_results``
       auto-promotes facts from "unknown" to "unverified" when a tool runs
       for them (so the model doesn't re-research them in subsequent rounds).
       If the model then fails to write a claim across all remaining rounds,
       the fact is left as "unverified" with an empty claim — a phantom fact
       that downstream nodes can't use.

    2. **Process-metadata claims** — The model sometimes writes a process
       observation like "Insufficient data found" as a claim.  These slip
       through ``_apply_discovered_facts`` via the post-loop extraction
       path.  They provide no factual content and should be reverted.

    In both cases the fact is reverted to "unknown" so the gap is visible
    to research_review and can trigger a retry.
    """
    for fact in facts:
        if fact.get("status") == "unverified":
            claim = (fact.get("claim") or "").strip()
            if not claim:
                fact["status"] = "unknown"
                logger.warning(
                    "RESEARCH: %s has no claim after all rounds, reverting to unknown",
                    fact["id"],
                )
            elif _is_process_metadata_claim(claim):
                fact["status"] = "unknown"
                fact["claim"] = ""
                logger.warning(
                    "RESEARCH: %s has process-metadata claim after all rounds, "
                    "reverting to unknown: %r",
                    fact["id"],
                    claim[:80],
                )


def _try_merge_snippets(a: str, b: str, min_words: int = 3) -> str | None:
    """Merge two snippets if A's suffix overlaps B's prefix (or vice versa).

    Returns the merged string, or ``None`` if no meaningful overlap
    exists.  Uses word-level token comparison for robustness against
    minor whitespace/punctuation differences.
    """
    t1 = a.split()
    t2 = b.split()
    if not t1 or not t2:
        return None
    # Try A-suffix overlaps B-prefix, then B-suffix overlaps A-prefix
    for first, second in [(t1, t2), (t2, t1)]:
        max_k = min(len(first), len(second))
        for k in range(max_k, min_words - 1, -1):
            if [w.lower() for w in first[-k:]] == [w.lower() for w in second[:k]]:
                return " ".join(first + second[k:])
    return None


def _find_or_merge_citation(
    citations: list[Citation],
    seen_urls: dict[str, str],
    source: str,
    url: str | None = None,
    title: str | None = None,
    snippet: str | None = None,
    content: str | None = None,
) -> tuple[str, bool]:
    """Find an existing citation by URL, or create a new one.

    When the URL already has a citation, the snippet is merged into the
    existing citation's ``snippets`` list using overlap-aware logic:

    * Exact match (case-insensitive) → skip.
    * One is a substring of the other → keep the longer version.
    * Suffix-prefix token overlap (≥ 3 words) → merge into one.
    * Otherwise → append as a genuinely distinct snippet.

    ``content`` (source text) follows **longest-wins**: within a single
    workflow run, URL fetches are treated as idempotent, so the longest
    body seen is canonical. This deliberately differs from ``snippets``
    (overlap-dedup-append) — ``snippets`` collects genuinely distinct
    search fragments across rounds, whereas ``content`` represents one
    coherent source body for downstream cross-referencing in review and
    evaluation.

    Returns ``(citation_id, is_new)``.  ``is_new`` is ``False`` when the
    citation was found and merged — callers can use this to annotate the
    tool feedback (e.g. "recurring source").
    """
    if url and url in seen_urls:
        cit_id = seen_urls[url]
        for c in citations:
            if c["id"] == cit_id:
                if snippet:
                    snippets = c.get("snippets", [])
                    snippet_norm = snippet.strip().lower()
                    merged_into = False
                    for i, existing in enumerate(snippets):
                        existing_norm = existing.strip().lower()
                        # Exact duplicate → skip
                        if snippet_norm == existing_norm:
                            merged_into = True
                            break
                        # New is substring of existing → existing is more complete
                        if snippet_norm in existing_norm:
                            merged_into = True
                            break
                        # Existing is substring of new → replace with longer
                        if existing_norm in snippet_norm:
                            snippets[i] = snippet[:_SNIPPET_MAX_LENGTH]
                            merged_into = True
                            break
                        # Suffix-prefix overlap → merge into one
                        merged = _try_merge_snippets(existing, snippet)
                        if merged:
                            snippets[i] = merged[:_SNIPPET_MAX_LENGTH]
                            merged_into = True
                            break
                    if not merged_into:
                        snippets.append(snippet[:_SNIPPET_MAX_LENGTH])
                    c["snippets"] = snippets
                # Content follows longest-wins (see docstring).
                if content:
                    if len(content) > len(c.get("content", "")):
                        c["content"] = content
                break
        return cit_id, False

    cit_id = next_id("cit", citations)
    citation: Citation = {"id": cit_id, "source": source}
    if url:
        citation["url"] = url
        seen_urls[url] = cit_id
    if title:
        citation["title"] = title
    if snippet:
        citation["excerpt"] = snippet
        citation["snippets"] = [snippet[:_SNIPPET_MAX_LENGTH]]
    if content:
        citation["content"] = content
    citations.append(citation)
    return cit_id, True


def _apply_sources(
    parsed: dict,
    citations: list[Citation],
    seen_urls: dict[str, str],
) -> None:
    """Apply sources from a model response to the citations list.

    Deduplicates against existing citations by URL via *seen_urls*.
    """
    sources = parsed.get("sources", [])
    if not isinstance(sources, list):
        return
    for src in sources:
        if not isinstance(src, dict):
            continue
        _find_or_merge_citation(
            citations,
            seen_urls,
            source=src.get("source", ""),
            url=src.get("url"),
            title=src.get("title"),
            snippet=src.get("excerpt"),
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
        {"role": "system", "content": render_prompt("research.fact_extraction.system")},
        {
            "role": "user",
            "content": render_prompt(
                "research.fact_extraction.user",
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


@dataclass
class _LoopResult:
    """State returned by a tool-loop function for the post-loop code."""

    last_response: ChatResponse | None
    last_thinking: str
    total_call_count: int
    exhausted_rounds: bool
    rounds: int
    had_valid_calls: bool
    new_budget: float
    total_tool_cost: float


async def _run_native_tool_loop(
    resolved: ResolvedModel,
    messages: list[dict],
    candidate_tools: list[ToolDefinition],
    allowed_names: set[str],
    required_params: dict[str, set[str]],
    call_limits: dict[str, int],
    call_counts: dict[str, int],
    tool_costs: dict[str, float],
    executor: ToolExecutor,
    writer: Callable[[dict[str, Any]], None],
    facts: list[Fact],
    citations: list[Citation],
    seen_urls: dict[str, str],
    tool_plan: list,
    tool_results_log: list[dict],
    new_budget: float,
    total_tool_cost: float,
) -> _LoopResult:
    """Run the native tool-calling loop.

    Uses the server's tool-calling API (``tools`` parameter) instead of
    text-based JSON parsing. Tool calls come back in ``response.tool_calls``;
    ``discovered_facts`` and ``sources`` are still parsed from ``content``
    (hybrid approach).
    """
    total_call_count = 0
    last_response = None
    last_thinking = ""
    exhausted_rounds = False
    had_valid_calls = False
    adapter = get_adapter(resolved.provider_type)

    round_num = 0
    for round_num in range(DEFAULT_MAX_ROUNDS):
        logger.info("RESEARCH (native) round %d/%d", round_num + 1, DEFAULT_MAX_ROUNDS)

        response = await resolved.client.chat_completion(
            messages=messages,
            model=resolved.model_id,
            temperature=DEFAULT_TEMPERATURE,
            tools=candidate_tools,
        )
        total_call_count += 1
        last_response = response
        last_thinking = getattr(response, "thinking", "") or ""

        # Hybrid: parse content for facts/sources alongside tool calls
        content = response.content or ""
        if content.strip():
            parsed = _parse_json_object(content)
            _apply_discovered_facts(parsed, facts)
            _apply_sources(parsed, citations, seen_urls)

        parsed_calls = response.tool_calls

        valid_calls, rejected_names = _validate_and_filter_calls(
            parsed_calls,
            allowed_names,
            call_limits,
            call_counts,
            required_params,
        )
        if rejected_names:
            logger.warning("Model requested disallowed tools: %s", rejected_names)

        if not valid_calls:
            logger.info(
                "RESEARCH (native): model signaled completion (round %d)",
                round_num + 1,
            )
            exhausted_rounds = False
            had_valid_calls = False
            break

        had_valid_calls = True

        try:
            results = await executor.execute_batch(valid_calls)
        except Exception as e:
            logger.error("Tool execution batch error: %s", e, exc_info=True)
            exhausted_rounds = False
            break

        tool_summary_parts, new_budget, total_tool_cost = _process_execution_results(
            results,
            valid_calls,
            writer,
            citations,
            seen_urls,
            facts,
            tool_plan,
            tool_results_log,
            call_counts,
            tool_costs,
            new_budget,
            total_tool_cost,
        )

        # Feed results back using adapter message format so the model
        # sees its own tool calls and the corresponding results.
        if adapter is not None:
            messages.append(adapter.format_assistant_message(content, valid_calls))
            for summary, call in zip(tool_summary_parts, valid_calls):
                messages.append(adapter.format_tool_result(call.id, summary))
        else:
            tool_summary = "\n\n---\n\n".join(tool_summary_parts)
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": render_prompt(
                        "research.tool_feedback",
                        tool_results=tool_summary,
                    ),
                }
            )

        logger.info(
            "RESEARCH (native) round %d: %d tool calls executed",
            round_num + 1,
            len(results),
        )
        exhausted_rounds = True

    return _LoopResult(
        last_response=last_response,
        last_thinking=last_thinking,
        total_call_count=total_call_count,
        exhausted_rounds=exhausted_rounds,
        rounds=round_num + 1,
        had_valid_calls=had_valid_calls,
        new_budget=new_budget,
        total_tool_cost=total_tool_cost,
    )


async def _run_text_tool_loop(
    resolved: ResolvedModel,
    messages: list[dict],
    _candidate_tools: list[ToolDefinition],
    allowed_names: set[str],
    required_params: dict[str, set[str]],
    call_limits: dict[str, int],
    call_counts: dict[str, int],
    tool_costs: dict[str, float],
    executor: ToolExecutor,
    writer: Callable[[dict[str, Any]], None],
    facts: list[Fact],
    citations: list[Citation],
    seen_urls: dict[str, str],
    tool_plan: list,
    tool_results_log: list[dict],
    new_budget: float,
    total_tool_cost: float,
) -> _LoopResult:
    """Run the text-based tool-calling loop.

    The model emits a JSON object containing ``tool_calls``,
    ``discovered_facts``, and ``sources`` as text. Tool calls are parsed
    from the text, with defensive parsing and retry logic for malformed
    JSON. This is the legacy path used when ``native_tool_calling`` is

    ``_candidate_tools`` is accepted for parameter symmetry with
    :func:`_run_native_tool_loop` but is intentionally unused — tool
    descriptions are already baked into the system prompt before the
    loop begins.
    disabled.
    """
    total_call_count = 0
    last_response = None
    last_thinking = ""
    exhausted_rounds = False
    had_valid_calls = False

    round_num = 0
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
            writer(
                {
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
                }
            )
            raise RuntimeError(
                f"Model returned empty content for {NODE_NAME} "
                f"(thinking={len(last_thinking)} chars)"
            )

        # Parse the JSON object response: {tool_calls, discovered_facts, sources}
        parsed = _parse_json_object(raw)

        # Apply discovered facts and sources each round
        _apply_discovered_facts(parsed, facts)
        _apply_sources(parsed, citations, seen_urls)

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
                messages.append(
                    {
                        "role": "user",
                        "content": render_prompt("research.parse_correction"),
                    }
                )

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
                _apply_sources(parsed, citations, seen_urls)
                parsed_calls = _extract_tool_calls(parsed)
                if not parsed_calls and "tool_calls" not in parsed:
                    parsed_calls = _parse_tool_calls(raw)
                if parsed_calls or raw.strip() in ("[]", "[]\n"):
                    break

        # Filter to allowed tools, enforce call limits, validate required args
        valid_calls, rejected_names = _validate_and_filter_calls(
            parsed_calls,
            allowed_names,
            call_limits,
            call_counts,
            required_params,
        )
        if rejected_names:
            logger.warning("Model requested disallowed tools: %s", rejected_names)

        # No tool calls means model is done
        if not valid_calls:
            logger.info("RESEARCH: model signaled completion (round %d)", round_num + 1)
            exhausted_rounds = False
            had_valid_calls = False
            break

        had_valid_calls = True

        # Execute tool calls
        try:
            results = await executor.execute_batch(valid_calls)
        except Exception as e:
            logger.error("Tool execution batch error: %s", e, exc_info=True)
            exhausted_rounds = False
            break

        # Process results (shared helper — builds citations, tracks costs)
        tool_summary_parts, new_budget, total_tool_cost = _process_execution_results(
            results,
            valid_calls,
            writer,
            citations,
            seen_urls,
            facts,
            tool_plan,
            tool_results_log,
            call_counts,
            tool_costs,
            new_budget,
            total_tool_cost,
        )

        # Feed results back to model for next round
        tool_summary = "\n\n---\n\n".join(tool_summary_parts)
        messages.append({"role": "assistant", "content": raw})
        messages.append(
            {
                "role": "user",
                "content": render_prompt(
                    "research.tool_feedback",
                    tool_results=tool_summary,
                ),
            }
        )

        logger.info(
            "RESEARCH round %d: %d tool calls executed",
            round_num + 1,
            len(results),
        )

        # If the loop continues to the next iteration (or exhausts),
        # mark as exhausted so we do a final summary round.
        exhausted_rounds = True

    return _LoopResult(
        last_response=last_response,
        last_thinking=last_thinking,
        total_call_count=total_call_count,
        exhausted_rounds=exhausted_rounds,
        rounds=round_num + 1,
        had_valid_calls=had_valid_calls,
        new_budget=new_budget,
        total_tool_cost=total_tool_cost,
    )


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
        writer(
            {
                "event": "node_end",
                "payload": {
                    "node": NODE_NAME,
                    "budget_remaining": es["budget_remaining"],
                },
            }
        )
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
    citations = cast(list[Citation], list(knowledge["citations"]))

    # Build URL → citation_id index for deduplication.  When multiple
    # searches return the same URL, the snippet is merged into the
    # existing citation rather than creating a duplicate.
    _seen_urls: dict[str, str] = {url: c["id"] for c in citations if (url := c.get("url"))}

    candidate_tools = es.get("candidate_tools", [])
    tool_plan = es.get("tool_call_plan", [])

    # Get tool executor
    from moira.service_setup import service_provider

    try:
        executor = cast(ToolExecutor, service_provider("tool_executor"))
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
        writer(
            {
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
            }
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

    allowed_names = {t.name for t in candidate_tools}

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

    resolved = await _resolve_intelligence(config)
    last_model_id = resolved.model_id

    # Select prompts based on tool-calling mode
    if resolved.native_tool_calling:
        system_prompt = render_prompt(
            "research.system_native_tools",
            max_extra_rounds=DEFAULT_MAX_ROUNDS,
        )
        user_prompt = render_prompt(
            "research.user_native",
            user_goal=knowledge.get("user_goal", knowledge["question"]),
            unknown_facts=_format_unknown_facts(facts),
        )
    else:
        system_prompt = render_prompt(
            "research.system",
            max_extra_rounds=DEFAULT_MAX_ROUNDS,
        )
        user_prompt = render_prompt(
            "research.user",
            user_goal=knowledge.get("user_goal", knowledge["question"]),
            unknown_facts=_format_unknown_facts(facts),
            tool_call_plan=_format_tool_call_plan(tool_plan),
            tool_descriptions=_format_tool_descriptions(candidate_tools),
        )

    # When re-entered from research_review retry, add feedback about gaps
    # and context from the previous research pass so the model can avoid
    # re-discovering what is already established.
    review_count = es.get("review_count", 0)
    review_history = knowledge.get("review_history", [])
    if review_count > 0 and bool(review_history) and review_history[-1].get("route") == "retry":
        last_review = review_history[-1]
        system_prompt += "\n\n" + render_prompt(
            "research.system_retry_review",
            coverage_assessment=last_review.get("coverage_assessment", ""),
            missing_areas="\n".join(f"- {area}" for area in last_review.get("missing_areas", [])),
        )
        system_prompt += "\n\n" + render_prompt(
            "research.system_retry_context",
            established_facts=_format_established_facts(facts),
            prior_conclusions=_format_prior_conclusions(knowledge.get("conclusions", [])),
            prior_citations=_format_prior_citations(citations),
        )

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    call_limits = es.get("tool_call_limits", {})
    tool_costs = es.get("tool_costs", {})

    if resolved.native_tool_calling:
        loop_result = await _run_native_tool_loop(
            resolved=resolved,
            messages=messages,
            candidate_tools=candidate_tools,
            allowed_names=allowed_names,
            required_params=_required_params,
            call_limits=call_limits,
            call_counts=call_counts,
            tool_costs=tool_costs,
            executor=executor,
            writer=writer,
            facts=facts,
            citations=citations,
            seen_urls=_seen_urls,
            tool_plan=tool_plan,
            tool_results_log=tool_results_log,
            new_budget=new_budget,
            total_tool_cost=total_tool_cost,
        )
    else:
        loop_result = await _run_text_tool_loop(
            resolved=resolved,
            messages=messages,
            _candidate_tools=candidate_tools,
            allowed_names=allowed_names,
            required_params=_required_params,
            call_limits=call_limits,
            call_counts=call_counts,
            tool_costs=tool_costs,
            executor=executor,
            writer=writer,
            facts=facts,
            citations=citations,
            seen_urls=_seen_urls,
            tool_plan=tool_plan,
            tool_results_log=tool_results_log,
            new_budget=new_budget,
            total_tool_cost=total_tool_cost,
        )

    total_call_count = loop_result.total_call_count
    last_response = loop_result.last_response
    last_thinking = loop_result.last_thinking
    exhausted_rounds = loop_result.exhausted_rounds
    new_budget = loop_result.new_budget
    total_tool_cost = loop_result.total_tool_cost
    round_num = loop_result.rounds - 1

    # --- Final summary round if loop exhausted max rounds ---
    # If the loop ended because we ran out of rounds (not because the model
    # signaled completion), do one more model call asking it to summarize
    # what it has gathered without requesting more tools.
    if exhausted_rounds and tool_results_log:
        logger.info("RESEARCH: max rounds exhausted, requesting final summary")
        messages.append({"role": "user", "content": render_prompt("research.summary")})
        try:
            summary_response: ChatResponse = await resolved.client.chat_completion(
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
                _apply_sources(summary_parsed, citations, _seen_urls)
        except Exception:
            logger.warning("RESEARCH: final summary round failed", exc_info=True)

    # --- Post-loop: extract discovered facts if model never produced them ---
    # A fact is considered resolved if it has been marked unverified (matched
    # to tool results) whether or not it has a claim yet. The claim may be
    # absent if the model's discovered_facts were empty but the plan-matching
    # code still marked the fact as having evidence.
    resolved_facts = [f for f in facts if f["status"] == "unverified"]
    if tool_results_log and not resolved_facts:
        logger.info(
            "RESEARCH: no discovered_facts produced during loop, running post-loop extraction",
        )
        resolved_facts = await _extract_facts_from_results(
            facts=facts,
            tool_results=tool_results_log,
            resolved=resolved,
            user_goal=knowledge.get("user_goal", knowledge["question"]),
        )
        total_call_count += 1

        if resolved_facts:
            fact_text = "\n".join(f"- {f['id']}: {f.get('claim', '')}" for f in resolved_facts)
            last_response = ChatResponse(content=fact_text)
            last_thinking = ""

    # Revert any facts that were auto-promoted to "unverified" by tool
    # execution but never received a claim from the model.  Must run
    # BEFORE the detail dict is built so facts_resolved and
    # facts_newly_unknown reflect the true final state.
    _cleanup_empty_claims(facts)

    # --- Build detail and emit ---
    # NOTE: tool_results are NOT included here. The run_manager accumulates
    # them from tool_result events with full output. Including them in
    # node_end would overwrite with truncated copies.
    detail: dict = {
        "facts_resolved": [f["id"] for f in facts if f["status"] == "unverified"],
        "facts_newly_unknown": [f["id"] for f in facts if f["status"] == "unknown"],
        "tool_plan_size": len(tool_plan),
        "executor_available": executor is not None,
        "rounds": round_num + 1 if loop_result.had_valid_calls else round_num,
        "tool_calling_mode": "native" if resolved.native_tool_calling else "emulated",
        "prompt": user_prompt,
        "model": last_model_id,
    }
    if last_response is not None:
        detail["response"] = last_response.content or ""
    if last_thinking:
        detail["thinking"] = last_thinking

    writer(
        {
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
        }
    )
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
            "research_count": es.get("research_count", 0) + 1,
        },
    }
