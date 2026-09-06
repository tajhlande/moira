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

import json
import logging
import math
import re
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable, cast

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from moira.inference.adapters import get_adapter
from moira.inference.client import ChatResponse
from moira.inference.defaults import DEFAULT_INTELLIGENCE_EXTRA_BODY, DEFAULT_TEMPERATURE
from moira.inference.registry import ResolvedModel
from moira.models.knowledge import (
    CITATION_CONTENT_LIMIT as _CITATION_CONTENT_LIMIT,
)
from moira.models.knowledge import Citation, Fact, ResearchState, next_id
from moira.prompts import render_prompt
from moira.tools.base import ToolCall, ToolDefinition, ToolResult
from moira.tools.executor import ToolExecutor

# Hypermedia URL pruning lives in the shared module (moira.tools.url_pruning)
# so RESTTool can prune at serialization time — pre-truncation, while the
# JSON still parses — and research can prune model-facing copies (feedback
# bodies, recall_source serving). The alias preserves the module-local name
# used by tests.
from moira.tools.url_pruning import prune_redundant_urls as _prune_redundant_urls
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

# Tool name checked against this constant for url_content-specific dedup
# logic (URL fetch memory). Defined here rather than imported from the tool
# module to keep _helpers.py dependency-free and avoid a circular import.
_URL_CONTENT_TOOL_NAME = "url_content"

# recall_source is intercepted in the research loop and never reaches the
# executor. Defined here for the same dependency-isolation reason as above.
_RECALL_SOURCE_TOOL_NAME = "recall_source"

# web_search is intercepted for near-duplicate queries (Phase 4a of
# planning-freedom): an identical or near-identical query returns ~the
# same results regardless of context reset, so re-issuing it buys no new
# information.
_WEB_SEARCH_TOOL_NAME = "web_search"

# Max IDF-weighted token overlap between a candidate web_search query and
# any query already issued this run above which the query is rejected as
# a near-duplicate. Chosen from measured history (3,475 replayed queries):
# >=0.84 is unambiguously word-shuffle dupes; the 0.52-0.63 band mixes
# lazy-modifier dupes with legitimate entity swaps; 0.65 sits just above
# the ambiguity band, flagging ~5% of historical queries (planned cost
# asymmetry: a missed dupe wastes one search call, a false reject blocks
# acquisition). Planning-freedom.md Phase 4 calibration section.
_QUERY_DUPE_THRESHOLD = 0.65

# English stopwords dropped before similarity scoring — they appear in
# nearly every query and would otherwise inflate token overlap.
_QUERY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "in",
        "on",
        "for",
        "to",
        "is",
        "are",
        "was",
        "were",
        "with",
        "by",
        "at",
        "as",
        "vs",
        "versus",
    }
)

_DISPLAY_OUTPUT_LIMIT = 2000

# Cap for Citation.content — the source text stored for downstream
# cross-referencing in review/evaluation. Canonical definition lives with
# the Citation schema (models/knowledge.py CITATION_CONTENT_LIMIT); this
# alias keeps the historical module-local name. Larger than excerpt
# (_SNIPPET_MAX_LENGTH) because this is the substantive body, but bounded
# to avoid state-size bloat and to keep recall_source re-injection safe
# for the workflow model's context window.

# Cap for tool-result text fed BACK into the research loop's message history.
# Storage (Citation.content) keeps the full body up to _CITATION_CONTENT_LIMIT,
# but the model-facing copy is bounded so a wide parallel fan-out (e.g. seven
# 10K-char REST payloads in one round) cannot blow the workflow model's context
# window — observed as a 39,457-token request vs a 32,768 limit in run
# fdaeb0e2. Truncated results carry a pointer to the stored citation so the
# model can deliberately re-read via recall_source. recall_source results are
# exempt: they are the re-read path itself (already bounded by
# _CITATION_CONTENT_LIMIT and within-batch dedup), and capping them would
# make stored content above the feedback cap permanently unreachable.
_TOOL_RESULT_FEEDBACK_LIMIT = 3_000


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


def _cap_feedback_body(body: str, cit_id: str) -> str:
    """Bound a tool-result body fed back into the loop's message history.

    Prunes redundant URL fields first (hypermedia rule — see
    _prune_redundant_urls) so the surviving window carries identifying data
    rather than link walls, then caps at _TOOL_RESULT_FEEDBACK_LIMIT. Full
    content is already stored in the citation (up to
    _CITATION_CONTENT_LIMIT); the appended pointer names the citation so the
    model can deliberately re-read the rest with recall_source instead of
    the full text sitting in context unrequested.
    """
    body = _prune_redundant_urls(body)
    if len(body) <= _TOOL_RESULT_FEEDBACK_LIMIT:
        return body
    omitted = len(body) - _TOOL_RESULT_FEEDBACK_LIMIT
    return (
        body[:_TOOL_RESULT_FEEDBACK_LIMIT]
        + f"\n... [{omitted:,} more chars stored as {cit_id} — use recall_source"
        f' with {{"citation_id": "{cit_id}"}} to read the rest]'
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


def _format_evidence_requests(requests: list) -> str:
    """Format evidence requests for the research prompt.

    Each line describes what evidence is needed for a group of facts and
    which tools to try, leaving query formulation to the model. The
    request ID prefixes each line so the model can echo it on tool calls
    (attribution) and the retry prompt can reference specific requests.
    """
    lines = []
    for req in requests:
        rid = req.get("id") or ""
        target_ids = ", ".join(req.get("target_fact_ids", []))
        evidence = req.get("evidence_needed", "")
        tools = " -> ".join(req.get("candidate_tools", []))
        fallback = " | fallback: yes" if req.get("fallback") else ""
        lines.append(f"- {rid} | Facts [{target_ids}]: {evidence} | tools: {tools}{fallback}")
    return "\n".join(lines)


def _format_request_outcomes(
    requests: list,
    request_attempts: dict[str, list[dict]],
    facts: list[Fact],
) -> str:
    """Render a per-request failure summary for the retry prompt.

    Only requests that have recorded attempts AND still-unresolved target
    facts are shown — resolved requests and never-attempted requests add
    noise, not signal. Queries already tried are listed so the model can
    formulate genuinely different strategies instead of re-rolling
    near-duplicates.
    """
    unresolved = {f["id"] for f in facts if f.get("status") != "verified"}
    lines = []
    for req in requests:
        rid = req.get("id")
        if not rid:
            continue
        attempts = request_attempts.get(rid) or []
        if not attempts:
            continue
        target_ids = [t for t in req.get("target_fact_ids", []) if t in unresolved]
        if not target_ids:
            continue
        evidence = (req.get("evidence_needed") or "").strip()
        if len(evidence) > 100:
            evidence = evidence[:97] + "..."
        lines.append(f"{rid} (Facts [{', '.join(target_ids)}] — {evidence}): still unresolved.")
        for att in attempts:
            if att.get("deduped"):
                lines.append(
                    f"  Rejected as duplicate: {att.get('tool', '?')} "
                    f'"{att.get("query", "")}" (too similar to a query already issued)'
                )
                continue
            status = "ok" if att.get("success") else "failed"
            lines.append(
                f'  Tried: {att.get("tool", "?")} "{att.get("query", "")}" '
                f"({att.get('results', 0)} results, {status})"
            )
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


def _count_newly_claimed_facts(claim_snapshot: dict[str, bool], facts: list[Fact]) -> int:
    """Count facts that gained a non-empty claim during this research pass.

    ``claim_snapshot`` maps fact ID → had-claim-at-pass-start. A fact counts
    as new progress if it has a claim now and did not have one at the start
    (this includes brand-new facts created from discovered_facts).

    The result feeds the structural progress signal (``research_progress``):
    a pass with zero newly claimed facts is exhausted no matter what else it
    did, and the graph routers use that to override retry recommendations
    instead of trusting model judgment about whether to keep researching.
    """
    return sum(
        1
        for f in facts
        if (f.get("claim") or "").strip() and not claim_snapshot.get(f["id"], False)
    )


def _make_tool_call(name: str, args: object, request_id: str | None = None) -> ToolCall | None:
    """Build a ToolCall with a generated ID, or return None if name is empty."""
    if not name:
        return None
    return ToolCall(
        id=f"tc_{uuid.uuid4().hex[:8]}",
        name=name,
        arguments=args if isinstance(args, dict) else {},
        request_id=request_id,
    )


def _request_id_from(call: dict) -> str | None:
    """Extract a clean request_id from a model-emitted call dict, if any."""
    rid = call.get("request_id")
    return _clean_request_id(rid)


def _clean_request_id(rid: object) -> str | None:
    """Normalize a raw request_id value to a non-empty stripped string."""
    return rid.strip() if isinstance(rid, str) and rid.strip() else None


def _parse_tool_calls(text: str) -> list[ToolCall]:
    """Parse tool calls from model response. Handles formats:
    1. JSON array: [{"tool": "name", "args": {...}}, ...]
    2. Line-delimited JSON objects: {"tool": "name", "args": {...}}
    3. Markdown-fenced arrays
    Local models often produce formats 2 and 3.

    Generates a unique ``id`` for each call so downstream code never
    needs to check for missing IDs.
    """
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
                    tc = _make_tool_call(name, args, _request_id_from(call))
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
                            tc = _make_tool_call(name, args, _request_id_from(call))
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
                tc = _make_tool_call(name, args, _request_id_from(obj))
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
        tc = _make_tool_call(name, args, _request_id_from(call))
        if tc:
            result.append(tc)
    return result


def _validate_and_filter_calls(
    parsed_calls: list[ToolCall],
    allowed_names: set[str],
    call_limits: dict[str, int],
    call_counts: dict[str, int],
    required_params: dict[str, set[str]],
    step_limits: dict[str, int] | None = None,
    step_baseline: dict[str, int] | None = None,
) -> tuple[list[ToolCall], list[str]]:
    """Filter tool calls by allowed names, call limits, and required params.

    ``call_counts`` is mutated in place — each accepted call increments
    the count immediately so that subsequent calls in the same batch
    see the updated value.  This prevents a single batch from
    overshooting the per-run limit (e.g., 9 calls emitted when the
    count is at 9 would all pass a limit of 10 if the count were only
    updated post-execution).

    When ``step_limits`` and ``step_baseline`` are provided, a per-step
    limit is also enforced.  The per-step usage is computed as
    ``call_counts[name] - step_baseline[name]``, which gives the number
    of calls made since the current research invocation began.  This
    naturally resets to zero each time research() is entered because
    the baseline is snapshotted from the accumulated call_counts at
    entry.

    Returns ``(valid_calls, rejected_names)``.
    """
    valid_calls: list[ToolCall] = []
    rejected: list[ToolCall] = []
    for call in parsed_calls:
        # Native mode: the model passes request_id as a regular function
        # argument (tool schemas don't declare it). Promote it to the
        # dedicated ToolCall field and strip it from arguments so tool
        # executors never see — or reject — an unknown parameter.
        if "request_id" in call.arguments:
            rid = call.arguments.pop("request_id")
            if call.request_id is None:
                call.request_id = _clean_request_id(rid)
        if call.name not in allowed_names:
            rejected.append(call)
            continue

        # --- Per-run limit check ---
        limit = call_limits.get(call.name, 0)
        used = call_counts.get(call.name, 0)
        if limit > 0 and used >= limit:
            logger.warning(
                "Tool %s hit per-run call limit (%d/%d), skipping",
                call.name,
                used,
                limit,
            )
            continue

        # --- Per-step limit check ---
        # step_used = calls made since this research invocation began
        if step_limits and step_baseline:
            step_limit = step_limits.get(call.name, 0)
            if step_limit > 0:
                step_used = used - step_baseline.get(call.name, 0)
                if step_used >= step_limit:
                    logger.warning(
                        "Tool %s hit per-step call limit (%d/%d), skipping",
                        call.name,
                        step_used,
                        step_limit,
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
        # Increment immediately so the next call in this batch sees
        # the updated count and can be properly rejected if at limit.
        call_counts[call.name] = used + 1
        valid_calls.append(call)
    return valid_calls, [c.name for c in rejected]


def _partition_url_content_calls(
    valid_calls: list[ToolCall],
    fetched_urls: dict[str, dict[str, Any]],
    citations: list[Citation],
) -> tuple[list[ToolCall], list[tuple[ToolCall, dict[str, Any]]]]:
    """Split url_content calls into those to execute vs. synthesize.

    A ``url_content`` call is deduped (synthesized instead of executed)
    when its URL was already attempted in this research session:

    - **Previously succeeded:** the citation referenced by
      ``fetched_urls[url]["cit_id"]`` must still exist in ``citations``.
      If it does, the call is deduped and the model will be pointed back
      to the existing citation. If the citation is somehow missing
      (shouldn't happen in normal operation), the call falls through to
      real execution as a defensive refetch.

    - **Previously failed:** always deduped. The model will be told the
      URL already failed and should try a different one. We treat all
      failures as permanent — paywalled and JS-rendered sites will fail
      again on retry, so re-attempting them only wastes budget.

    Non-url_content calls are never deduped.

    Returns ``(calls_to_execute, synthetics)`` where ``synthetics`` is a
    list of ``(call, fetched_url_info)`` tuples preserving the original
    call order.
    """
    to_execute: list[ToolCall] = []
    synthetics: list[tuple[ToolCall, dict[str, Any]]] = []
    for call in valid_calls:
        if call.name == _URL_CONTENT_TOOL_NAME:
            url = (call.arguments.get("url") or "").strip()
            info = fetched_urls.get(url) if url else None
            if info:
                if info.get("status") == "success":
                    cit_id = info.get("cit_id")
                    if cit_id and any(c["id"] == cit_id for c in citations):
                        synthetics.append((call, info))
                        continue
                    # Citation missing — defensive refetch
                    logger.warning(
                        "url_content dedup: citation %s for %s not found, refetching",
                        cit_id,
                        url,
                    )
                else:
                    # Previously failed — synthesize failure
                    synthetics.append((call, info))
                    continue
        to_execute.append(call)
    return to_execute, synthetics


def _build_synthetic_url_result(
    call: ToolCall,
    info: dict[str, Any],
    citations: list[Citation],
) -> ToolResult:
    """Build a synthetic ToolResult for a deduped url_content call.

    Two cases, both marked with ``metadata["synthetic"] = True`` so that
    :func:`_process_execution_results` knows to skip cost charging:

    - **Previously succeeded:** carries a minimal ``metadata["results"]``
      entry pointing at the existing citation.
      :func:`_process_execution_results` will call ``_find_or_merge_citation``
      which finds the existing citation via ``seen_urls`` and marks the
      summary as "(recurring source)". The output text explicitly tells
      the model not to refetch.

    - **Previously failed:** empty ``metadata["results"]`` with
      ``success=False``. :func:`_process_execution_results` will route
      this through the zero-results branch, surfacing the prior error
      in the summary so the model knows why the URL is blocked.
    """
    url = (call.arguments.get("url") or "").strip()
    status = info.get("status")

    if status == "success":
        cit_id = info.get("cit_id")
        citation = next((c for c in citations if c["id"] == cit_id), None)
        if citation:
            return ToolResult(
                tool_name=_URL_CONTENT_TOOL_NAME,
                output=(
                    f"URL already fetched in this session — see [{cit_id}]. "
                    f"Reuse that citation; do not refetch."
                ),
                success=True,
                duration_ms=0,
                metadata={
                    "results": [
                        {
                            "url": url,
                            "title": citation.get("title", ""),
                            "snippet": (citation.get("excerpt") or "")[:_SNIPPET_MAX_LENGTH],
                            "content": (citation.get("content") or "")[:_CITATION_CONTENT_LIMIT],
                        }
                    ],
                    "synthetic": True,
                },
            )

    # Previously failed (or defensive fallback if citation went missing)
    error = info.get("error") or "previously failed"
    return ToolResult(
        tool_name=_URL_CONTENT_TOOL_NAME,
        output=(f"URL previously failed in this session ({error}). Try a different URL."),
        success=False,
        duration_ms=0,
        error=f"deduped: previously failed ({error})",
        metadata={"results": [], "synthetic": True},
    )


def _build_recall_source_result(call: ToolCall, citations: list[Citation]) -> ToolResult:
    """Synthesize a ToolResult for recall_source from in-scope citations.

    Returns the full stored content (snippets + page content up to
    ``_CITATION_CONTENT_LIMIT``) for the requested citation ID. If the ID
    is not found, lists available citation IDs so the model can correct
    itself.

    Snippet-depth citations (search results whose page was never fetched)
    are refused: re-serving search snippets the model already saw is
    circular — it cannot contain evidence beyond the feedback the model
    generated it from. The refusal names url_content on the citation's URL
    as the way to actually get page-depth content.

    Always marked ``metadata["synthetic"] = True`` so
    :func:`_process_execution_results` skips cost charging and citation
    creation — the content comes from an existing citation, not a new fetch.
    """
    citation_id = call.arguments.get("citation_id", "")

    for c in citations:
        if c["id"] == citation_id:
            if c.get("depth") == "snippet":
                if c.get("url"):
                    hint = (
                        f"No page was ever fetched. Use url_content with "
                        f'url="{c["url"]}" to get the page content.'
                    )
                else:
                    hint = "No page was ever fetched and the citation has no URL."
                return ToolResult(
                    tool_name=_RECALL_SOURCE_TOOL_NAME,
                    output=(
                        f"Citation '{citation_id}' is search-snippet depth: it holds only "
                        f"the search-result snippets already shown to you, so recall "
                        f"cannot add anything. {hint}"
                    ),
                    success=False,
                    duration_ms=0,
                    metadata={"synthetic": True, "refused": True},
                )

            parts = [f"Source: {citation_id}"]
            if c.get("title"):
                parts.append(f"Title: {c['title']}")
            if c.get("url"):
                parts.append(f"URL: {c['url']}")

            snippets = c.get("snippets", [])
            if snippets:
                parts.append("\nSearch result snippets:")
                for s in snippets:
                    parts.append(f"  - {s}")
            elif c.get("excerpt"):
                parts.append(f"\nExcerpt: {c['excerpt']}")

            content = c.get("content", "")
            if content and content.strip():
                # Prune redundant URL fields from the model-facing copy —
                # recall re-injects up to _CITATION_CONTENT_LIMIT chars and
                # URL-heavy JSON wastes most of that window on link walls
                # (the 08-26 c815f4a1 overflow was 10 PokeAPI recalls).
                # Storage in the citation is untouched.
                parts.append(f"\nPage content:\n{_prune_redundant_urls(content)}")

            return ToolResult(
                tool_name=_RECALL_SOURCE_TOOL_NAME,
                output="\n".join(parts),
                success=True,
                duration_ms=0,
                metadata={"synthetic": True},
            )

    # Not found — list available IDs to help the model
    available = ", ".join(c["id"] for c in citations) if citations else "(none)"
    return ToolResult(
        tool_name=_RECALL_SOURCE_TOOL_NAME,
        output=(f"Citation '{citation_id}' not found. Available citations: {available}"),
        success=False,
        duration_ms=0,
        metadata={"synthetic": True},
    )


async def _execute_with_url_dedup(
    valid_calls: list[ToolCall],
    fetched_urls: dict[str, dict[str, Any]],
    executor: ToolExecutor,
    citations: list[Citation],
    call_counts: dict[str, int],
) -> list[ToolResult]:
    """Execute tool calls, deduping url_content on already-fetched URLs.

    Partitioning happens after ``_validate_and_filter_calls`` has already
    incremented ``call_counts`` for every accepted call. This function
    decrements the count for deduped calls so the per-run and per-step
    limits reflect only actual executions — a deduped call is free from
    a budgeting perspective.

    Returns results in the same order as ``valid_calls``. Synthetic
    :class:`ToolResult` objects fill in for deduped calls so downstream
    processing (:func:`_process_execution_results`, adapter message
    formatting) sees a result for every call the model emitted.
    """
    calls_to_execute, synthetics = _partition_url_content_calls(
        valid_calls, fetched_urls, citations
    )

    # Decrement call_counts for deduped calls. _validate_and_filter_calls
    # incremented them, but these calls won't actually execute. This keeps
    # the per-run/per-step limits honest — a model that emits 5 url_content
    # calls for URLs it already fetched shouldn't burn 5 calls against its
    # limit.
    for call, _info in synthetics:
        prev = call_counts.get(call.name, 0)
        if prev > 0:
            call_counts[call.name] = prev - 1

    if calls_to_execute:
        real_results = await executor.execute_batch(calls_to_execute)
    else:
        real_results = []

    synthetic_results = [
        _build_synthetic_url_result(call, info, citations) for call, info in synthetics
    ]

    # Merge in original valid_calls order so the model sees results aligned
    # with the tool calls it emitted (adapters pair calls and results by
    # position or by call.id).
    result_by_call_id: dict[str, ToolResult] = {}
    for result, call in zip(real_results, calls_to_execute):
        result_by_call_id[call.id] = result
    for result, (call, _info) in zip(synthetic_results, synthetics):
        result_by_call_id[call.id] = result
    return [result_by_call_id[call.id] for call in valid_calls]


async def _execute_tools(
    valid_calls: list[ToolCall],
    fetched_urls: dict[str, dict[str, Any]],
    executor: ToolExecutor,
    citations: list[Citation],
    call_counts: dict[str, int],
    issued_queries: list[str] | None = None,
) -> list[ToolResult]:
    """Execute tool calls with dupe interception and recall/url dedup.

    Partitioning happens in four layers:

    1. ``recall_source`` calls are synthesized from in-scope citations
       (free, no HTTP fetch) — never executed.
    2. Within a single batch, duplicate ``recall_source`` calls for the
       same citation ID are deduped: the first gets the stored content,
       repeats get a short "already recalled above" pointer. Recall
       content doesn't change between reads in the same round — the
       model sees the first result in this batch's context — so repeats
       only bloat context. Mirrors the url_content URL dedup, including
       decrementing ``call_counts`` for the deduped calls.
    3. ``web_search`` near-duplicate interception (Phase 4a,
       planning-freedom): queries are scored sequentially against the
       running accepted set seeded with ``issued_queries`` (all queries
       issued this workflow run). Exact-after-normalization or weighted
       overlap ≥ ``_QUERY_DUPE_THRESHOLD`` ⇒ synthetic rejection pointer
       + call-count decrement; rejected queries do not enter the ledger.
       Run-scoped deliberately: an identical query returns ~the same
       results regardless of context reset.
    4. Remaining calls go through :func:`_execute_with_url_dedup` which
       handles url_content URL dedup and real execution.

    Recall-dedup scope is the batch, NOT the research pass: a later round
    (or a retry pass with reset context) may legitimately re-read the
    same citation because earlier results have scrolled out of the
    model's context window. The web_search ledger is the deliberate
    exception — run-scoped (see layer 3).

    Returns results in the same order as ``valid_calls``.
    """
    # Partition out recall_source calls (always synthetic — never executed)
    recall_calls = [c for c in valid_calls if c.name == _RECALL_SOURCE_TOOL_NAME]
    non_recall_calls = [c for c in valid_calls if c.name != _RECALL_SOURCE_TOOL_NAME]

    # Partition web_search calls through near-dupe interception
    search_calls = [c for c in non_recall_calls if c.name == _WEB_SEARCH_TOOL_NAME]
    other_calls = [c for c in non_recall_calls if c.name != _WEB_SEARCH_TOOL_NAME]
    ws_rejects: list[ToolCall] = []
    if search_calls:
        to_execute_search, ws_rejects, ws_matches = _partition_web_search_dupes(
            search_calls, issued_queries or []
        )
        for call, matched in zip(ws_rejects, ws_matches):
            # Rejected duplicate is free against limits, mirroring the
            # url_content/recall dedup decrements.
            prev = call_counts.get(call.name, 0)
            if prev > 0:
                call_counts[call.name] = prev - 1

        if issued_queries is not None:
            # Accepted queries join the run-scoped ledger immediately so
            # later batch entries score against them.
            issued_queries.extend(
                (call.arguments.get("query") or "").strip() for call in to_execute_search
            )
    else:
        to_execute_search, ws_rejects, ws_matches = [], [], []
    ws_dupe_pairs = list(zip(ws_rejects, ws_matches))

    batch_calls = [*to_execute_search, *other_calls]

    # Execute non-recall calls through url_content dedup + executor
    if batch_calls:
        results = await _execute_with_url_dedup(
            batch_calls, fetched_urls, executor, citations, call_counts
        )
        results_by_id = {c.id: r for c, r in zip(batch_calls, results)}
    else:
        results_by_id = {}

    # Synthesize recall_source results from in-scope citations, deduping
    # repeated citation IDs within this batch.
    recalled_ids: set[str] = set()
    for call in recall_calls:
        citation_id = call.arguments.get("citation_id", "")
        if citation_id and citation_id in recalled_ids:
            results_by_id[call.id] = ToolResult(
                tool_name=_RECALL_SOURCE_TOOL_NAME,
                output=(
                    f"Citation '{citation_id}' was already recalled earlier in this "
                    "batch — the stored content is unchanged and appears above. "
                    "Recall a different source or take a different approach."
                ),
                success=False,
                duration_ms=0,
                metadata={"synthetic": True, "deduped": True},
            )
            # Deduped recall is free against per-run/per-step limits,
            # mirroring the url_content dedup decrement.
            prev = call_counts.get(call.name, 0)
            if prev > 0:
                call_counts[call.name] = prev - 1
            continue
        if citation_id:
            recalled_ids.add(citation_id)
        results_by_id[call.id] = _build_recall_source_result(call, citations)

    # Synthetic rejection results for near-duplicate searches
    for call, matched in ws_dupe_pairs:
        results_by_id[call.id] = _build_synthetic_dupe_result(call, matched)

    # Reassemble in original valid_calls order
    return [results_by_id[c.id] for c in valid_calls]


def _update_fetched_urls(
    fetched_urls: dict[str, dict[str, Any]],
    results: list[ToolResult],
    valid_calls: list[ToolCall],
    seen_urls: dict[str, str],
) -> None:
    """Record url_content outcomes for future dedup decisions.

    Runs AFTER :func:`_process_execution_results`, which has populated
    ``seen_urls`` with the citation ID for each successful fetch. This
    ordering lets us resolve ``cit_id`` for the synthetic success path
    in future rounds.

    Synthetic results are skipped — they're already tracked in
    ``fetched_urls`` (that's why they were deduped).
    """
    for result, call in zip(results, valid_calls):
        if call.name != _URL_CONTENT_TOOL_NAME:
            continue
        if result.metadata.get("synthetic"):
            continue
        url = (call.arguments.get("url") or "").strip()
        if not url or url in fetched_urls:
            continue
        cit_id = seen_urls.get(url) if result.success else None
        fetched_urls[url] = {
            "status": "success" if result.success else "failed",
            "cit_id": cit_id,
            "error": result.error if not result.success else None,
        }


def _tokenize_query(query: str) -> set[str]:
    """Tokenize a search query for similarity scoring.

    Lowercase alphanumeric runs of length > 1, minus stopwords. Returns a
    set — token multiplicity is irrelevant to overlap scoring.
    """
    return {
        t
        for t in re.findall(r"[a-z0-9]+", query.lower())
        if len(t) > 1 and t not in _QUERY_STOPWORDS
    }


def _normalize_query(query: str) -> str:
    """Normalize a query to a canonical form for exact-duplicate checks."""
    return " ".join(sorted(_tokenize_query(query)))


def _normalize_fact_text(text) -> str:
    """Normalize a fact field for identity comparison.

    Accepts str or arbitrary JSON-ish values (models occasionally emit
    booleans/objects into text fields); non-strings are serialized so the
    tokenizer sees their readable content rather than crashing.
    """
    if not isinstance(text, str):
        text = json.dumps(text, default=str) if text is not None else ""
    return _normalize_query(text)


def _fact_identity(subject, fact_needed) -> tuple[str, str] | None:
    """Canonical identity of an appendable fact.

    Identity = (token-normalized subject, token-normalized fact_needed).
    Token normalization (not just strip/lower) keeps entities robust to
    punctuation/case drift in model output ("Chien-Pao" vs "chien pao!").
    Used by the Phase 4b fact-append dedup: historical collision patterns
    (per-entity shell twins from the 008200e8 run; wholesale
    re-decompositions like run 9439's water f001-f006 vs f007-f012) share
    this identity exactly after normalization. Returns None when there is
    no usable question text — such entries take other hygiene paths.
    """
    s = _normalize_fact_text(subject if isinstance(subject, str) or subject else "")
    n = _normalize_fact_text(fact_needed)
    return (s, n) if n else None


_ADVISORY_FACT_SIMILARITY_THRESHOLD = 0.75


def _query_similarity(candidate: set[str], prior_tokens: list[set[str]]) -> tuple[float, int]:
    """Max IDF-weighted overlap between a candidate and any prior query.

    ``weight(w) = log((N+1)/(df+1)) + 1`` where ``df`` counts — across
    ALL priors — how many contain the token. DF is computed at scoring
    time over the priors alone so this run's own boilerplate down-weights
    itself while a fresh discriminative token pulls similarity decisively
    down (raw Jaccard treats both identically — the reason weighting was
    chosen; see planning-freedom.md Phase 4a).

    Returns ``(best_score, argmax_index)``; ``best_score`` is 0.0 and
    ``argmax_index`` is -1 when there are no usable priors.
    """
    if not prior_tokens:
        return 0.0, -1
    df: dict[str, int] = {}
    for tokens in prior_tokens:
        for tok in tokens:
            df[tok] = df.get(tok, 0) + 1
    n = len(prior_tokens)

    def weight(tok: str) -> float:
        return math.log((n + 1) / (df.get(tok, 0) + 1)) + 1.0

    best, best_idx = 0.0, -1
    for idx, prior in enumerate(prior_tokens):
        union = candidate | prior
        if not union:
            continue
        num = sum(weight(t) for t in candidate & prior)
        den = sum(weight(t) for t in union)
        if num / den > best:
            best = num / den
            best_idx = idx
    return best, best_idx


def _partition_web_search_dupes(
    calls: list[ToolCall],
    issued_queries: list[str],
) -> tuple[list[ToolCall], list[ToolCall], list[str]]:
    """Split web_search calls into executes vs near-duplicate rejects.

    Sequential over the batch against a running accepted-set seeded with
    ``issued_queries`` (every web_search query issued earlier this
    workflow run), so dupes within one fan-out batch and across retry
    passes are both caught. Accepted queries are appended to the running
    set; rejected duplicates are NOT appended — rejections must not
    poison future similarity scoring.

    Returns ``(to_execute, rejected, rejected_matches)`` where ``rejected``
    pairs each call with the prior query text it matched.
    """
    accepted = [q for q in issued_queries if q.strip()]
    accepted_norms = [_normalize_query(q) for q in accepted]
    to_execute: list[ToolCall] = []
    rejected: list[ToolCall] = []
    rejected_matches: list[str] = []
    for call in calls:
        query = (call.arguments.get("query") or "").strip()
        norm = _normalize_query(query)
        match_text: str | None = None
        matched_score = 1.0
        if norm:
            # Exact-after-normalization rejects outright (no scoring).
            if norm in accepted_norms:
                match_text = accepted[accepted_norms.index(norm)]
            elif accepted:
                prior_tokens = [_tokenize_query(q) for q in accepted]
                score, best_idx = _query_similarity(_tokenize_query(query), prior_tokens)
                if score >= _QUERY_DUPE_THRESHOLD and best_idx >= 0:
                    match_text = accepted[best_idx]
                    matched_score = score
        if match_text is not None:
            rejected.append(call)
            rejected_matches.append(match_text)
            logger.warning(
                "web_search near-duplicate rejected (%.2f vs %.2f threshold): %r",
                matched_score,
                _QUERY_DUPE_THRESHOLD,
                query[:80],
            )
            continue
        accepted.append(query)
        accepted_norms.append(norm)
        to_execute.append(call)
    return to_execute, rejected, rejected_matches


def _build_synthetic_dupe_result(call: ToolCall, matched_query: str) -> ToolResult:
    """Synthetic rejection result for a near-duplicate web_search call.

    Marked ``metadata["synthetic"] = True`` so cost is skipped; the
    matched prior query is quoted so the model can see what it already
    tried. Free against the per-run/per-step limits (call-count decrement
    happens at the partition site, mirroring the url_content/recall
    dedups).
    """
    return ToolResult(
        tool_name=_WEB_SEARCH_TOOL_NAME,
        output=(
            f'Query rejected as a duplicate: you already searched "{matched_query[:80]}" '
            "earlier in this run — the same or nearly the same query returns "
            "~the same results every time. Rephrase materially (different source "
            "type, scope, or terminology) or target a different evidence request."
        ),
        success=False,
        duration_ms=0,
        error="deduped: near-duplicate of an issued query",
        metadata={"synthetic": True, "deduped": True},
    )


def _augment_tools_with_request_id(tools: list[ToolDefinition]) -> list[ToolDefinition]:
    """Return tool copies whose argument schemas declare ``request_id``.

    Native tool-calling models emit arguments matching the declared
    schema — an undeclared parameter gets dropped even when the prompt
    asks for it (measured 0/39 attribution on the 08-25 trade run).
    Declaring ``request_id`` as an optional parameter on every tool
    aligns the schema with the prompt instruction;
    ``_validate_and_filter_calls`` pops it back off before execution so
    tool executors never see it.
    """
    augmented: list[ToolDefinition] = []
    for t in tools:
        schema = t.argument_schema if isinstance(t.argument_schema, dict) else {}
        props = schema.get("properties")
        if isinstance(props, dict) and "request_id" in props:
            augmented.append(t)
            continue
        new_schema = dict(schema)
        new_props = dict(props) if isinstance(props, dict) else {}
        new_props["request_id"] = {
            "type": "string",
            "description": (
                "ID of the evidence request this call serves "
                "(e.g. 'req0001'), copied from the evidence request list"
            ),
        }
        new_schema["properties"] = new_props
        augmented.append(replace(t, argument_schema=new_schema))
    return augmented


def _record_request_attempt(
    ledger: dict[str, list[dict]],
    rid: str,
    name: str,
    args: dict[str, Any],
    result: ToolResult,
) -> None:
    """Append one attempt record to the per-request attempt ledger.

    The ledger feeds two consumers: the retry prompt (queries already
    tried, so the model changes strategy instead of re-rolling
    near-duplicates) and future mechanical query-dedup. ``query`` is the
    most query-like argument (query/url), falling back to a compact
    argument dump for tools like calculator.
    """
    query = args.get("query") or args.get("url") or ""
    if not query and args:
        try:
            query = json.dumps(args, sort_keys=True)
        except (TypeError, ValueError):
            query = str(args)
    structured = result.metadata.get("results") if result.metadata else None
    n_results = len(structured) if isinstance(structured, list) else (1 if result.output else 0)
    ledger.setdefault(rid, []).append(
        {
            "tool": name,
            "query": str(query)[:80],
            "success": bool(result.success),
            "results": n_results,
            # Synthetic duplicate rejections are recorded too: they are
            # evidence the model already tried this angle and was blocked,
            # which the retry prompt surfaces as exhaustion signal (4b).
            "deduped": bool(result.metadata.get("deduped")) if result.metadata else False,
        }
    )


def _process_execution_results(
    results: list,
    valid_calls: list[ToolCall],
    writer: Callable[[dict[str, Any]], None],
    citations: list[Citation],
    seen_urls: dict[str, str],
    facts: list[Fact],
    evidence_requests: list,
    tool_results_log: list[dict],
    call_counts: dict[str, int],
    tool_costs: dict[str, float],
    new_budget: float,
    total_tool_cost: float,
    request_attempts: dict[str, list[dict]] | None = None,
) -> tuple[list[str], float, float]:
    """Process execution results into citations, summaries, and cost tracking.

    Mutates ``citations``, ``seen_urls``, ``facts``, ``tool_results_log``,
    ``call_counts``, and (when provided) ``request_attempts`` in place.
    Returns ``(tool_summary_parts, new_budget, total_tool_cost)``.

    Fact auto-promotion (unknown → unverified) is STRICT: a successful
    call only promotes facts of the evidence request it was attributed
    to via ``call.request_id``. Unattributed calls promote nothing — the
    post-loop ``_cleanup_empty_claims`` safety net then reverts any
    claimless promotion, so coverage reflects what actually ran, not
    "some call somewhere used this tool" (the old loose matching).
    """
    tool_summary_parts: list[str] = []
    request_by_id = {r.get("id"): r for r in evidence_requests if r.get("id")}
    for result, call in zip(results, valid_calls):
        name = call.name
        args = call.arguments
        rid = call.request_id
        if request_attempts is not None and rid:
            _record_request_attempt(request_attempts, rid, name, args, result)
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
                    # Attribution: which evidence request this call served.
                    "request_id": rid,
                },
            }
        )

        # recall_source returns content from existing citations — don't
        # create new citations or charge budget. The synthetic flag also
        # prevents cost charging below, but we short-circuit here to skip
        # the structured/unstructured citation creation branches entirely.
        # Feedback-cap exempt: recall IS the deliberate re-read path (see
        # _TOOL_RESULT_FEEDBACK_LIMIT rationale).
        if name == _RECALL_SOURCE_TOOL_NAME:
            tool_summary_parts.append(result.output)
            tool_results_log.append(
                {
                    "tool": result.tool_name,
                    "args": args,
                    "output": result.output[:_SNIPPET_MAX_LENGTH] if result.output else "",
                    "duration_ms": result.duration_ms,
                    "success": result.success,
                    "metadata": result.metadata,
                    "request_id": rid,
                }
            )
            continue

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
                    # Enforce the pipeline cap at the storage boundary —
                    # tool-side metadata limits mirror it by name but are
                    # not trusted to match (they drifted historically).
                    content=(sr.get("content") or sr.get("snippet") or "")[
                        :_CITATION_CONTENT_LIMIT
                    ]
                    or None,
                )
                status = "SUCCESS" if result.success else "FAILED"
                recurring = "" if is_new else " (recurring source)"
                # Fetch tools (url_content, RESTTool) provide a "content"
                # field with the full retrieved body. Discovery tools
                # (web_search) only provide "snippet". Feed the content to
                # the model when available — the whole point of a fetch tool
                # is to get the body — but bounded by the feedback cap: wide
                # parallel fan-outs of large payloads must not blow the
                # model's context window (see _TOOL_RESULT_FEEDBACK_LIMIT).
                # The full body remains available via recall_source.
                body = _cap_feedback_body(sr.get("content") or sr.get("snippet", ""), cit_id)
                label = "Content" if sr.get("content") else "Snippet"
                tool_summary_parts.append(
                    f"[{cit_id}] Tool: {name}\nStatus: {status}{recurring}\n"
                    f"Title: {sr.get('title', '')}\n"
                    f"URL: {sr.get('url', '')}\n"
                    f"{label}: {body}"
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
            synth_title = _synthesize_citation_title(name, args)
            citations.append(
                Citation(
                    id=cit_id,
                    source=result.tool_name,
                    title=synth_title,
                    excerpt=result.output[:_SNIPPET_MAX_LENGTH] if result.output else "",
                    content=result.output[:_CITATION_CONTENT_LIMIT] if result.output else "",
                )
            )
            status = "SUCCESS" if result.success else "FAILED"
            tool_summary_parts.append(
                f"[{cit_id}] Tool: {name}\nStatus: {status}\nResult:\n"
                # Feedback cap applies here too: Path B stores the full output
                # in the citation, so the model can recall the rest.
                f"{_cap_feedback_body(result.output, cit_id)}"
            )

        tool_results_log.append(
            {
                "tool": result.tool_name,
                "args": args,
                "output": result.output[:_SNIPPET_MAX_LENGTH] if result.output else "",
                "duration_ms": result.duration_ms,
                "success": result.success,
                "metadata": result.metadata,
                "request_id": rid,
            }
        )

        # STRICT attribution: only the request this call served gets its
        # facts promoted. Unattributed / unknown-ID calls promote nothing;
        # the model's own discovered_facts entries (with claims+citations)
        # remain the primary path for real resolution.
        if result.success and result.output:
            planned = request_by_id.get(rid) if rid else None
            if planned is not None:
                target_ids = planned.get("target_fact_ids", [])
                for fact in facts:
                    if fact["id"] in target_ids and fact["status"] == "unknown":
                        fact["status"] = "unverified"
            elif rid:
                logger.warning(
                    "Tool call %s referenced unknown request %s — facts not promoted",
                    call.id,
                    rid,
                )

        # call_counts is now incremented in _validate_and_filter_calls
        # at validation time, so it is already up-to-date here.
        # Synthetic results (deduped url_content calls) don't actually
        # execute, so they don't consume budget. _execute_with_url_dedup
        # already decremented call_counts for them; we skip the cost
        # charge here to match.
        if not result.metadata.get("synthetic"):
            per_call_cost = tool_costs.get(name, 1.0)
            new_budget -= per_call_cost
            total_tool_cost += per_call_cost

    return tool_summary_parts, new_budget, total_tool_cost


def _is_fact_id_reference(text: str) -> bool:
    """True when text consists solely of fact IDs and connectors.

    The research prompt displays the facts list pipe-delimited
    ("f003 | Trade | effect of tariffs on prices"), and models sometimes
    mimic that display format in fact_needed ("f003|f004", "f003 and f004").
    Such a value is a reference to other facts, not a description of what
    needs to be known, and would be meaningless if facts were renumbered.
    Requires at least one fact-ID token so ordinary prose never matches.
    """
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", text.lower()) if t]
    if not tokens:
        return False
    has_fact_id = any(re.fullmatch(r"f\d+", t) for t in tokens)
    return has_fact_id and all(re.fullmatch(r"f\d+", t) or t in ("and", "or") for t in tokens)


def _is_duplicate_fact(
    subject,
    fact_needed,
    facts: list[Fact],
    appended_this_response: list[tuple[str, str]],
) -> bool:
    """Phase 4b fact-append dedup (calibrated on DB forensics).

    Hard-reject tier: normalized ``(subject, fact_needed)`` identity matches
    any existing fact or any entry appended earlier in this same response.
    Historical incidents all share exact identity after normalization —
    per-entity shell twins from run 008200e8 (Clefable x2 byte-identical),
    per-Pokemon clones from c31d, wholesale re-decompositions from 9439 —
    while every inspected fuzzy near-miss (>=0.75, e.g. systolic-vs-
    diastolic twins) is legitimate decomposition. Two-tier reject was
    therefore reduced to tier-1-only; nothing in history sits between the
    legit pairs (~0.83 max) and true dupes.

    Advisory tier: IDF-weighted similarity >= threshold for equal-or-empty
    subjects logs a warning without blocking — accumulates signal for a
    future cutoff without risking false merges.
    """
    ident = _fact_identity(subject, fact_needed)
    if not ident:
        return False
    subj_norm = ident[0]
    for fact in facts:
        if _fact_identity(fact.get("subject"), fact.get("fact_needed")) == ident:
            logger.warning(
                "RESEARCH: duplicate fact rejected (%s | %s) — identity matches existing %s",
                subj_norm or "<no-subject>",
                ident[1][:60],
                fact.get("id"),
            )
            return True
    for s_prev, n_prev in appended_this_response:
        if (s_prev, n_prev) == ident:
            logger.warning(
                "RESEARCH: duplicate fact rejected (%s | %.60s) — second "
                "identical entry in one response",
                subj_norm or "<no-subject>",
                ident[1],
            )
            return True
    # Advisory similarity scan over existing + just-appended entries with
    # equal-or-empty subjects (token-normalized comparison).
    tokens = _tokenize_query(ident[1])
    comparables: list[tuple[str, set[str]]] = [
        (
            _normalize_fact_text(f.get("subject")),
            _tokenize_query(_normalize_fact_text(f.get("fact_needed"))),
        )
        for f in facts
    ]
    comparables += [(s_prev, set(n_prev.split())) for s_prev, n_prev in appended_this_response]
    best = 0.0
    for s_other, t_other in comparables:
        if not tokens or not t_other:
            continue
        if not (subj_norm == s_other or not subj_norm or not s_other):
            continue
        sc, _ = _query_similarity(tokens, [t_other])
        best = max(best, sc)
    if best >= _ADVISORY_FACT_SIMILARITY_THRESHOLD:
        logger.warning(
            "RESEARCH: possible duplicate fact append (%.2f vs %.2f advisory "
            "threshold): %s | %.80s",
            best,
            _ADVISORY_FACT_SIMILARITY_THRESHOLD,
            subj_norm or "<no-subject>",
            ident[1],
        )
    return False


def _apply_discovered_facts(parsed: dict, facts: list[Fact]) -> None:
    """Apply discovered_facts from a model response to the facts list.

    For facts with a matching fact_id and status "unknown" or "unverified",
    updates the claim, relation, value, and status to "unverified" — but
    only if the model provided a non-empty claim. An empty claim causes the
    entire update to be skipped so the fact stays in its prior state.

    Process-metadata claims (e.g. "Insufficient data found") are NOT filtered
    here — they pass through as legitimate claims and are caught later by the
    research_review node, which marks them "unknown" and reverts the fact.
    Letting them reach the reviewer is more robust than regex patterns: the
    reviewer understands any phrasing without pattern maintenance.

    When the model emits multiple cited entries for the same fact_id in one
    response (observed 2026-08-24 telescope run: ~18 entries across 6 fact
    IDs, each a distinct-subject detail adjacent to the fact's question),
    only the FIRST cited entry updates the fact. Each subsequent cited entry
    is split off into a new fact keyed by its own subject — the merge logic
    mirrors the prompt rule "a claim must answer its fact's question;
    related details go in new facts," so a true-but-irrelevant detail no
    longer clobbers a relevant claim (last-write-wins) or vanishes.

    For new facts (fact_id is null/missing with fact_needed), appends them.

    Also applies to "unverified" facts so the model can improve a claim that
    was initially set by plan-matching (raw tool output) with a proper
    extracted claim from discovered_facts.
    """
    discovered = parsed.get("discovered_facts", [])
    if not isinstance(discovered, list):
        return
    # fact IDs already updated by a cited entry earlier in this response —
    # later entries for these IDs become new facts instead of overwrites.
    updated_this_response: set[str] = set()
    # (subject, normalized fact_needed) of every new fact appended in this
    # response — the within-response half of the dedup ledger.
    appended_this_response: list[tuple[str, str]] = []
    for disc in discovered:
        if not isinstance(disc, dict):
            continue
        fact_id = disc.get("fact_id")
        claim = (disc.get("claim") or "").strip()
        disc_cites = sorted(c for c in disc.get("citation_ids", []) if isinstance(c, str))
        if fact_id and fact_id not in updated_this_response:
            matched = False
            for fact in facts:
                if fact["id"] == fact_id and fact["status"] in ("unknown", "unverified"):
                    matched = True
                    if not claim:
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
                    fact["claim"] = claim
                    if disc.get("relation"):
                        fact["relation"] = disc["relation"]
                    if disc.get("value"):
                        fact["value"] = disc["value"]
                    # Merge citation_ids from the model's response into the
                    # fact's existing set. The model references source IDs
                    # (e.g., "cit001") that were labeled in the tool feedback.
                    if disc_cites:
                        existing = set(fact.get("citation_ids", []))
                        existing.update(disc_cites)
                        fact["citation_ids"] = sorted(existing)
                    fact["status"] = "unverified"
                    if disc_cites:
                        # Mark this fact claimed-by-a-cited-entry so any
                        # later entries for the same ID in this response
                        # are diverted to new facts rather than overwriting.
                        updated_this_response.add(fact_id)
                    break
            if not matched:
                logger.warning(
                    "RESEARCH: discovered_fact fact_id=%r does not match any "
                    "existing fact — claim will be dropped",
                    fact_id,
                )
        elif fact_id and fact_id in updated_this_response and claim and disc_cites:
            # Overflow entry: a cited detail for a fact already updated in
            # this response. Split into a new fact so the detail is kept
            # without clobbering the first claim.
            logger.warning(
                "RESEARCH: multiple cited entries for %s in one response — "
                "splitting overflow entry into a new fact: %s",
                fact_id,
                claim[:80],
            )
            subject = (disc.get("subject") or "").strip()
            new_id = next_id("f", facts)
            if _is_duplicate_fact(subject, claim, facts, appended_this_response):
                continue
            new_fact = Fact(
                id=new_id,
                subject=subject,
                fact_needed=claim,
                claim=claim,
                status="unverified",
            )
            new_fact["citation_ids"] = disc_cites
            facts.append(new_fact)
            appended_this_response.append(_fact_identity(subject, claim))
        else:
            fact_needed = (disc.get("fact_needed") or "").strip()
            # ID-reference rejection: "f003|f004" is the model mimicking the
            # pipe-delimited facts display, not a description of what needs
            # to be known. Treat it as absent so the entry falls through to
            # the citation-gated claim paths below.
            if fact_needed and _is_fact_id_reference(fact_needed):
                logger.warning(
                    "RESEARCH: new-fact fact_needed=%r is only fact-ID "
                    "references — treating as missing",
                    fact_needed,
                )
                fact_needed = ""

            if fact_needed:
                if _is_duplicate_fact(
                    disc.get("subject"), fact_needed, facts, appended_this_response
                ):
                    continue
                new_id = next_id("f", facts)
                new_fact = Fact(
                    id=new_id,
                    subject=disc.get("subject", ""),
                    fact_needed=fact_needed,
                    status="unknown",
                )
                # Record an immediately-resolved claim only when it is
                # backed by at least one citation. The 08-24 planning-freedom
                # eval showed uncited immediate claims become unsourced
                # "unverified" facts that leak into the report. An uncited
                # claim still enters as a bare unknown fact (with its
                # fact_needed) so a later round can extract it properly.
                if claim and disc_cites:
                    new_fact["claim"] = claim
                    new_fact["status"] = "unverified"
                    new_fact["citation_ids"] = disc_cites
                facts.append(new_fact)
                appended_this_response.append(_fact_identity(disc.get("subject"), fact_needed))
            elif claim and disc_cites:
                # Fallback: the model provided a cited claim with null
                # fact_id and no fact_needed. Rather than silently dropping
                # the model's extraction work, create a new fact using the
                # claim text as both fact_needed and claim.
                logger.warning(
                    "RESEARCH: model returned cited claim with null fact_id "
                    "and no fact_needed — creating fact from claim: %s",
                    claim[:80],
                )
                new_id = next_id("f", facts)
                if _is_duplicate_fact(disc.get("subject"), claim, facts, appended_this_response):
                    continue
                new_fact = Fact(
                    id=new_id,
                    subject=disc.get("subject", ""),
                    fact_needed=claim,
                    claim=claim,
                    status="unverified",
                )
                new_fact["citation_ids"] = disc_cites
                facts.append(new_fact)
                appended_this_response.append(_fact_identity(disc.get("subject"), claim))
            elif claim:
                # Uncited claim-only entries are dropped: without a source
                # the claim cannot be verified or cited in the report, and
                # admitting it as "unverified" pollutes the knowledge model
                # (the f012-f014 failure mode from the 08-24 eval).
                logger.warning(
                    "RESEARCH: dropping uncited claim with null fact_id (no citation_ids): %s",
                    claim[:80],
                )


def _cleanup_empty_claims(facts: list[Fact]) -> None:
    """Revert "unverified" facts whose claims are empty to "unknown".

    During research, ``_process_execution_results`` auto-promotes facts from
    "unknown" to "unverified" when a tool runs for them (so the model doesn't
    re-research them in subsequent rounds).  If the model then fails to write
    a claim across all remaining rounds, the fact is left as "unverified"
    with an empty claim — a phantom fact that downstream nodes can't use.

    Process-metadata claims (e.g. "Insufficient data found") are intentionally
    NOT handled here — they are non-empty and pass through as legitimate
    claims.  The research_review node catches them via the "unknown" reviewer
    result and reverts the fact there.  Centralising detection in the reviewer
    avoids the maintenance burden of regex patterns and handles any phrasing.

    In both cases (empty claim here, process-metadata claim in review) the
    fact is reverted to "unknown" so the gap is visible and can trigger a
    retry.
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


# Argument keys excluded from synthesized citation titles (security).
_TITLE_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "api_key",
        "key",
        "token",
        "auth",
        "authorization",
        "secret",
        "password",
    }
)
_TITLE_MAX_ARG_LEN = 50


def _synthesize_citation_title(tool_name: str, args: dict[str, Any]) -> str:
    """Build a human-readable title from tool name and call arguments.

    Generic fallback for tools that don't provide structured metadata
    (calculator, date_time, etc.). Ensures every citation has a label
    for the retry context's "Sources already consulted" section.
    """
    safe_args = {k: v for k, v in args.items() if k.lower() not in _TITLE_SENSITIVE_KEYS}
    if safe_args:
        parts = [f"{k}={str(v)[:_TITLE_MAX_ARG_LEN]}" for k, v in safe_args.items()]
        return f"{tool_name}({', '.join(parts)})"
    return tool_name


def _find_or_merge_citation(
    citations: list[Citation],
    seen_urls: dict[str, str],
    source: str,
    url: str | None = None,
    title: str | None = None,
    snippet: str | None = None,
    content: str | None = None,
    depth: str | None = None,
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

    ``depth`` records whether a page body was fetched ("page") or the
    citation holds only search fragments ("snippet"); defaults to deriving
    from ``content`` presence. ``depth`` is passed explicitly by model-
    declared sources, whose ``content`` is an excerpt, not a fetched body.
    On merge, any arriving ``content`` upgrades a snippet-depth citation to
    "page" — a fetch happened, so recall should serve it — but a snippet
    arrival never downgrades a page-depth citation.

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
                # Content follows longest-wins (see docstring). A fetch
                # upgrades snippet-depth to page — recall_source serves it.
                if content:
                    if len(content) > len(c.get("content", "")):
                        c["content"] = content
                    c["depth"] = "page"
                break
        return cit_id, False

    cit_id = next_id("cit", citations)
    citation: Citation = {
        "id": cit_id,
        "source": source,
        "depth": depth or ("page" if content else "snippet"),
    }
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
            content=src.get("excerpt"),
            # Model-declared content is an excerpt, not a fetched body —
            # never let it upgrade the citation to page depth.
            depth="snippet",
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
        extra_body=DEFAULT_INTELLIGENCE_EXTRA_BODY,
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
    evidence_requests: list,
    tool_results_log: list[dict],
    new_budget: float,
    total_tool_cost: float,
    step_limits: dict[str, int] | None = None,
    step_baseline: dict[str, int] | None = None,
    fetched_urls: dict[str, dict[str, Any]] | None = None,
    request_attempts: dict[str, list[dict]] | None = None,
    issued_queries: list[str] | None = None,
) -> _LoopResult:
    """Run the native tool-calling loop.

    Uses the server's tool-calling API (``tools`` parameter) instead of
    text-based JSON parsing. Tool calls come back in ``response.tool_calls``;
    ``discovered_facts`` and ``sources`` are still parsed from ``content``
    (hybrid approach).

    ``fetched_urls`` tracks URL fetch attempts within this research
    invocation. When provided, url_content calls on URLs already in the
    dict are deduped — the actual HTTP fetch is skipped and a synthetic
    result is returned instead, preventing the model from wasting budget
    on retries of already-fetched (or already-failed) URLs.
    """
    if fetched_urls is None:
        fetched_urls = {}
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
            extra_body=DEFAULT_INTELLIGENCE_EXTRA_BODY,
            tools=_augment_tools_with_request_id(candidate_tools),
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
            step_limits=step_limits,
            step_baseline=step_baseline,
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
            results = await _execute_tools(
                valid_calls,
                fetched_urls,
                executor,
                citations,
                call_counts,
                issued_queries=issued_queries,
            )
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
            evidence_requests,
            tool_results_log,
            call_counts,
            tool_costs,
            new_budget,
            total_tool_cost,
            request_attempts=request_attempts,
        )

        # Record url_content outcomes for future dedup. Runs after
        # _process_execution_results so seen_urls is populated with the
        # citation IDs that successful fetches produced.
        _update_fetched_urls(fetched_urls, results, valid_calls, seen_urls)

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
    evidence_requests: list,
    tool_results_log: list[dict],
    new_budget: float,
    total_tool_cost: float,
    step_limits: dict[str, int] | None = None,
    step_baseline: dict[str, int] | None = None,
    fetched_urls: dict[str, dict[str, Any]] | None = None,
    request_attempts: dict[str, list[dict]] | None = None,
    issued_queries: list[str] | None = None,
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

    ``fetched_urls`` tracks URL fetch attempts within this research
    invocation for url_content dedup (see :func:`_run_native_tool_loop`).
    """
    if fetched_urls is None:
        fetched_urls = {}
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
            extra_body=DEFAULT_INTELLIGENCE_EXTRA_BODY,
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
                    extra_body=DEFAULT_INTELLIGENCE_EXTRA_BODY,
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
            step_limits=step_limits,
            step_baseline=step_baseline,
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

        # Execute tool calls (recall_source + url_content + query dupes)
        try:
            results = await _execute_tools(
                valid_calls,
                fetched_urls,
                executor,
                citations,
                call_counts,
                issued_queries=issued_queries,
            )
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
            evidence_requests,
            tool_results_log,
            call_counts,
            tool_costs,
            new_budget,
            total_tool_cost,
            request_attempts=request_attempts,
        )

        # Record url_content outcomes for future dedup
        _update_fetched_urls(fetched_urls, results, valid_calls, seen_urls)

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
    results, and can request additional rounds. The evidence_requests from
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
    # Snapshot the call counts at entry to compute per-step usage.
    # Per-step limit = "how many calls since this research invocation began",
    # which naturally resets each time research() is entered.
    step_call_baseline = dict(call_counts)
    total_tool_cost = es.get("total_tool_cost_consumed", 0.0)
    tool_costs = es.get("tool_costs", {})

    facts = list(knowledge["facts"])
    citations = cast(list[Citation], list(knowledge["citations"]))

    # Build URL → citation_id index for deduplication.  When multiple
    # searches return the same URL, the snippet is merged into the
    # existing citation rather than creating a duplicate.
    _seen_urls: dict[str, str] = {url: c["id"] for c in citations if (url := c.get("url"))}

    # URL fetch memory: tracks every url_content attempt within this
    # research invocation (and pre-populated with URLs already fetched in
    # prior passes). Used to dedupe url_content calls — the model
    # frequently refetches URLs it already retrieved (or that already
    # failed), wasting budget on HTTP calls that can't improve the
    # outcome. Pre-populating from _seen_urls means cross-pass success
    # dedup works immediately; cross-pass failure dedup is not tracked
    # (failed fetches leave no citation record) but within-invocation
    # failures are caught.
    _fetched_urls: dict[str, dict[str, Any]] = {
        url: {"status": "success", "cit_id": cit_id} for url, cit_id in _seen_urls.items()
    }

    candidate_tools = es.get("candidate_tools", [])
    evidence_requests = es.get("evidence_requests", [])
    # Per-request attempt ledger, carried across planning regenerations
    # (see _carry_over_attempts). Copy inner lists so appends here never
    # mutate the LangGraph-state lists in place.
    request_attempts: dict[str, list[dict]] = {
        rid: list(attempts) for rid, attempts in (es.get("request_attempts") or {}).items()
    }
    # Run-scoped ledger of every web_search query issued this workflow run.
    # Seed for the Phase 4a near-duplicate guardrail — an identical query
    # returns ~the same results regardless of context reset, so this list
    # (unlike the recall dedup) is deliberately not batch- or pass-scoped.
    issued_queries: list[str] = list(es.get("issued_queries") or [])

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
            "evidence_requests_size": len(evidence_requests),
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
            evidence_requests=_format_evidence_requests(evidence_requests),
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
            evidence_requests=_format_evidence_requests(evidence_requests),
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
            prior_citations=_format_prior_citations(citations, facts),
        )
        # Per-request failure summary: which requests got attempts, what
        # queries ran, which facts are still unresolved. This is the
        # feedback loop the reviewer's aggregate assessment can't provide.
        request_outcomes = _format_request_outcomes(evidence_requests, request_attempts, facts)
        if request_outcomes:
            system_prompt += "\n\n" + render_prompt(
                "research.system_request_outcomes",
                request_outcomes=request_outcomes,
            )

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    call_limits = es.get("tool_call_limits", {})
    step_limits = es.get("tool_call_step_limits", {})
    tool_costs = es.get("tool_costs", {})

    # Snapshot which facts already have claims, so the structural progress
    # signal can measure what THIS pass added (Phase 4b).
    claim_snapshot = {f["id"]: bool((f.get("claim") or "").strip()) for f in facts}

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
            evidence_requests=evidence_requests,
            tool_results_log=tool_results_log,
            new_budget=new_budget,
            total_tool_cost=total_tool_cost,
            step_limits=step_limits,
            step_baseline=step_call_baseline,
            fetched_urls=_fetched_urls,
            request_attempts=request_attempts,
            issued_queries=issued_queries,
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
            evidence_requests=evidence_requests,
            tool_results_log=tool_results_log,
            new_budget=new_budget,
            total_tool_cost=total_tool_cost,
            step_limits=step_limits,
            step_baseline=step_call_baseline,
            fetched_urls=_fetched_urls,
            request_attempts=request_attempts,
            issued_queries=issued_queries,
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
                extra_body=DEFAULT_INTELLIGENCE_EXTRA_BODY,
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
    # Check for facts that have BOTH status "unverified" AND a non-empty
    # claim. Tool execution auto-promotes facts from "unknown" to
    # "unverified" just because a tool targeted them (see
    # _process_execution_results), so checking status alone is misleading —
    # facts can be "unverified" with empty claims. The real failure mode
    # this safety net addresses: the model called tools and got results,
    # but never wrote discovered_facts in its text content (empty content
    # or content without the JSON object). When that happens, every
    # auto-promoted fact is claimless, the condition fires, and a dedicated
    # extraction call asks the model to interpret the tool results.
    facts_with_claims = [
        f for f in facts if f["status"] == "unverified" and (f.get("claim") or "").strip()
    ]
    if tool_results_log and not facts_with_claims:
        auto_promoted = len([f for f in facts if f["status"] == "unverified"])
        logger.info(
            "RESEARCH: no claims extracted during loop (%d facts auto-promoted "
            "but claimless), running post-loop extraction",
            auto_promoted,
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

    # --- Structural progress signal (Phase 4b) ---
    # A pass that ends with zero newly claimed facts is exhausted no matter
    # what else it did. Graph routers consume this signal (research_progress)
    # to override retry recommendations — model judgment about whether to
    # keep researching is not trusted when the data says nothing was gained.
    new_facts = _count_newly_claimed_facts(claim_snapshot, facts)
    research_progress = {"new_facts": new_facts, "stalled": new_facts == 0}
    if research_progress["stalled"]:
        logger.info(
            "RESEARCH: pass produced %d new claim(s) — marking progress stalled",
            new_facts,
        )

    # --- Build detail and emit ---
    # NOTE: tool_results are NOT included here. The run_manager accumulates
    # them from tool_result events with full output. Including them in
    # node_end would overwrite with truncated copies.
    detail: dict = {
        "facts_resolved": [f["id"] for f in facts if f["status"] == "unverified"],
        "facts_newly_unknown": [f["id"] for f in facts if f["status"] == "unknown"],
        "evidence_requests_size": len(evidence_requests),
        "executor_available": executor is not None,
        "rounds": round_num + 1 if loop_result.had_valid_calls else round_num,
        "tool_calling_mode": "native" if resolved.native_tool_calling else "emulated",
        "prompt": user_prompt,
        "model": last_model_id,
        # Compact attempt counts (full ledger lives in execution_state).
        "request_attempt_counts": {rid: len(v) for rid, v in request_attempts.items()},
        # Observability for the Phase 4a guardrail.
        "issued_query_count": len(issued_queries),
        # Structural progress signal (Phase 4b).
        "new_facts": new_facts,
        "stalled": research_progress["stalled"],
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
            "request_attempts": request_attempts,
            "issued_queries": issued_queries,
            "research_progress": research_progress,
            "research_count": es.get("research_count", 0) + 1,
        },
    }
