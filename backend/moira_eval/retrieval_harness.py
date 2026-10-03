"""Retrieval-isolation harness (Phase 1 of retrieval-quality-plan.md).

Runs the retrieval half of the workflow in isolation — decomposition →
tool_identification → planning → research → END — with the REAL services
(model registry, tool catalog, SearXNG-backed web_search with its
exact-query cache) and no synthesis / review / evaluation / report.
Because it runs in-process, no API server is needed; because the search
cache is deterministic per query string, repeat-run variance measures the
variable we care about: query quality.

What it produces per repeat:

- An artifact dict: facts, citations, evidence requests, the per-request
  attempt ledger, every issued query, and every executed tool call with
  its ranked results (web_search) or fetched body (url_content).
- Per-fact recall scores: for each decomposition fact, whether the needed
  information appeared in the top-k results of the queries attributed to
  that fact's evidence requests. Two scorers:

  * ``llm``   — a judge call per fact, using the purpose-scoped judge
    model: ``MOIRA_EVAL_JUDGE_MODEL_ITERATION`` by default or
    ``MOIRA_EVAL_JUDGE_MODEL_MILESTONE`` with ``--milestone``
    (endpoint/key shared; see ``judge.py``).
  * ``gold``  — deterministic keyword markers from
    ``moira_eval/gold/<question_id>.json``, for judge-free re-scoring.

Output goes to ``moira_eval/results/harness/<question_id>/<variant>/
<timestamp>.json`` — deliberately NOT sha-keyed (see the EVAL_LOG pitfall
about sha-collision overwrites). ``--variant`` is a label only for now;
later phases (query-writer, fan-out, passage mode) plug in as variants.

Usage (from ``backend/``)::

    uv run python -m moira_eval.retrieval_harness \\
        --question water-blood-pressure --repeats 3
"""

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moira.config import MoiraConfig, ResearchSettings, load_config
from moira_eval.judge import JudgeConfig, judge_config_from_env, judge_model_var
from moira_eval.metrics import (
    failure_class,
    harness_per_fact_recall,
    harness_recall_summary,
)
from moira_eval.questions import QUESTIONS, get_question

# Depth at which snippet recall is scored. web_search's max_results
# default is 5, so entries beyond rank 5 do not exist in practice.
K_MAX = 5
_KS = (1, 3, 5)

# Cap on judge-input size per fact: entries (snippets + page excerpts).
_MAX_ENTRIES_PER_FACT = 30
_PAGE_EXCERPT_CHARS = 3000

# Concurrency for LLM fact scoring.
_SCORER_CONCURRENCY = 4


# ---------------------------------------------------------------------------
# Recording executor wrapper
# ---------------------------------------------------------------------------


class RecordingExecutor:
    """Wraps the real ToolExecutor and records every executed call.

    The research node resolves the executor from the service locator at
    call time, so swapping ``_services["tool_executor"]`` for a recording
    wrapper around the real one captures the full call stream — including
    metadata like web_search's ranked ``results`` — without touching the
    research node itself.

    Delegation preserves semantics exactly: ``execute`` passes through
    (the real timeout/retry logic runs), ``execute_batch`` delegates to
    the wrapped batch call and zips results back onto the recorded calls.
    """

    def __init__(self, inner: Any):
        self._inner = inner
        self.calls: list[dict[str, Any]] = []

    async def execute(self, tool_name: str, args: dict, allowed_tools: set[str] | None = None):
        result = await self._inner.execute(tool_name, args, allowed_tools=allowed_tools)
        self._record(tool_name, args, result)
        return result

    async def execute_batch(
        self,
        calls: list[Any],
        allowed_tools: set[str] | None = None,
    ):
        results = await self._inner.execute_batch(calls, allowed_tools=allowed_tools)
        for call, result in zip(calls, results):
            self._record(call.name, call.arguments, result)
        return results

    def _record(self, tool_name: str, args: dict, result: Any) -> None:
        metadata = getattr(result, "metadata", None) or {}
        # url_content failures carry the fetch error (403, timeout, ...) in
        # ToolResult.error — output is empty on failure, so output_head alone
        # would hide *why* a call failed. Bounded to keep artifacts lean.
        error = getattr(result, "error", None)
        self.calls.append(
            {
                "tool": tool_name,
                "args": dict(args),
                "success": bool(getattr(result, "success", False)),
                "duration_ms": getattr(result, "duration_ms", 0),
                "cache_hit": metadata.get("cache_hit"),
                # web_search: ranked {title, url, snippet} dicts — the
                # recall metric's ground truth for rank scoring.
                "results": metadata.get("results") or [],
                # Output head is for forensics only; scoring uses
                # results / page bodies below.
                "output_head": (getattr(result, "output", "") or "")[:500],
                "output_chars": len(getattr(result, "output", "") or ""),
                "error": str(error)[:300] if error else None,
            }
        )


# ---------------------------------------------------------------------------
# Subgraph
# ---------------------------------------------------------------------------


def build_retrieval_subgraph():
    """decomposition → tool_identification → planning → research → END.

    Reuses the real node functions so query generation, dedup guardrails,
    and attribution logic are exactly what production runs exercise.
    Compiled without a checkpointer: harness runs are ephemeral and must
    not write rows to the live database's checkpoint tables.
    """
    from langgraph.graph import END, START, StateGraph

    from moira.models.knowledge import ResearchState
    from moira.workflow.nodes.decomposition import decomposition
    from moira.workflow.nodes.planning import planning
    from moira.workflow.nodes.research import research
    from moira.workflow.nodes.tool_identification import tool_identification

    graph = StateGraph(ResearchState)
    graph.add_node("decomposition", decomposition)
    graph.add_node("tool_identification", tool_identification)
    graph.add_node("planning", planning)
    graph.add_node("research", research)
    graph.add_edge(START, "decomposition")
    graph.add_edge("decomposition", "tool_identification")
    graph.add_edge("tool_identification", "planning")
    graph.add_edge("planning", "research")
    graph.add_edge("research", END)
    return graph.compile()


# ---------------------------------------------------------------------------
# Run-state construction (mirrors api/streaming.py initial state)
# ---------------------------------------------------------------------------


async def _settings_or(config: MoiraConfig, key: str, fallback: Any) -> Any:
    """Read a typed setting with config fallback, like streaming.py does."""
    try:
        from moira.service_setup import service_provider

        settings_svc = service_provider("settings_service")
        value = await settings_svc.get_typed(key)
        return value if value is not None else fallback
    except Exception:
        return fallback


def _tool_dicts() -> tuple[dict[str, float], dict[str, int], dict[str, int]]:
    """(tool_costs, tool_call_limits, tool_call_step_limits) for enabled
    tools — mirrors api/streaming.py's ``_build_tool_cost_and_limits``."""
    from moira.service_setup import service_provider

    catalog = service_provider("tool_catalog")
    tools = catalog.get_all()
    costs = {t.name: t.invocation_cost for t in tools if t.enabled}
    limits = {
        t.name: t.call_limit_per_run
        for t in tools
        if t.enabled and t.call_limit_per_run and t.call_limit_per_run > 0
    }
    step_limits = {
        t.name: t.call_limit_per_step
        for t in tools
        if t.enabled and t.call_limit_per_step and t.call_limit_per_step > 0
    }
    return costs, limits, step_limits


def build_initial_state(
    question: str,
    budget_limit: float,
    tool_dicts: tuple[dict[str, float], dict[str, int], dict[str, int]],
    config: MoiraConfig,
) -> dict:
    """Initial ResearchState shaped like a production run's."""
    tool_costs, tool_call_limits, tool_call_step_limits = tool_dicts
    cw = config.budget.cost_weights
    step_costs = {
        "decomposition": cw.decomposition,
        "tool_identification": cw.tool_identification,
        "planning": cw.planning,
        "research": cw.research,
        "synthesis": cw.synthesis,
        "research_review": cw.research_review,
        "evaluation": cw.evaluation,
        "report_generation": cw.report_generation,
    }
    return {
        "knowledge": {
            "question": question,
            "user_goal": "",
            "topic": "",
            "entities": [],
            "concepts": [],
            "facts": [],
            "conclusions": [],
            "citations": [],
            "review_history": [],
            "evaluation_history": [],
        },
        "execution_state": {
            "candidate_tools": [],
            "evidence_requests": [],
            "budget_remaining": float(budget_limit),
            "budget_limit": float(budget_limit),
            "step_costs": step_costs,
            "retry_limits": {"max_review": 3, "max_evaluation": 2},
            "tool_costs": tool_costs,
            "tool_call_limits": tool_call_limits,
            "tool_call_step_limits": tool_call_step_limits,
            "tool_call_counts": {},
            "total_tool_cost_consumed": 0.0,
            "error": "",
            "research_retry_count": 0,
            "research_count": 0,
            "review_count": 0,
            "evaluation_count": 0,
        },
    }


# ---------------------------------------------------------------------------
# Repeat execution
# ---------------------------------------------------------------------------


async def run_repeat(
    subgraph,
    question_text: str,
    budget_limit: float,
    tool_dicts: tuple[dict[str, float], dict[str, int], dict[str, int]],
    index: int,
    config: MoiraConfig,
) -> dict:
    """One isolated retrieval pass. Returns a repeat artifact dict."""
    from moira.service_setup import _services, service_provider

    state = build_initial_state(question_text, budget_limit, tool_dicts, config)
    graph_config = {"configurable": {"moira_config": config}}

    recorder = RecordingExecutor(service_provider("tool_executor"))
    real_executor = recorder._inner
    _services["tool_executor"] = recorder
    # Single pass in "values" stream mode: each chunk is the full state after
    # a step. The chunk following decomposition gives the original fact ids —
    # needed to separate planning-targeted facts from facts research later
    # spawns via its overflow-split path (spawned facts are never queried and
    # would otherwise deflate per-fact recall). The last chunk is final state.
    original_fact_ids: set[str] = set()
    final_state: dict = {}
    try:
        seen_decomposition = False
        async for values in subgraph.astream(state, config=graph_config, stream_mode="values"):
            if seen_decomposition and not original_fact_ids:
                original_fact_ids = {
                    f.get("id", "") for f in (values.get("knowledge", {}).get("facts") or [])
                }
            if values.get("knowledge", {}).get("facts"):
                seen_decomposition = True
            final_state = values
    finally:
        _services["tool_executor"] = real_executor

    artifact = build_repeat_artifact(final_state, recorder.calls, index, original_fact_ids)
    fetch_note = ""
    if artifact["counts"]["url_content_failures"]:
        fetch_note = f", {artifact['counts']['url_content_failures']} fetches blocked"
    print(
        f"  repeat {index + 1}: {artifact['counts']['web_search_calls']} searches, "
        f"{artifact['counts']['url_content_calls']} fetches{fetch_note}, "
        f"{len(artifact['facts'])} facts "
        f"({len(artifact['original_fact_ids'] or [])} original)",
        file=sys.stderr,
    )
    return artifact


def build_repeat_artifact(
    final_state: dict,
    recorded_calls: list[dict],
    index: int,
    original_fact_ids: set[str] | None = None,
) -> dict:
    """Assemble the serializable repeat artifact from final graph state
    plus the recorded call stream.

    original_fact_ids are the decomposition-produced fact ids (captured at
    runtime); facts outside that set were spawned by research's
    overflow-split path and are excluded from headline recall metrics.
    """
    knowledge = final_state.get("knowledge", {})
    es = final_state.get("execution_state", {})

    facts = [
        {
            "id": f.get("id", ""),
            "subject": f.get("subject", ""),
            "fact_needed": f.get("fact_needed", ""),
            "status": f.get("status", ""),
            "citation_ids": f.get("citation_ids", []),
            # "" on artifacts that predate the origin field.
            "origin": f.get("origin", ""),
        }
        for f in knowledge.get("facts", [])
    ]
    citations = [
        {
            "id": c.get("id", ""),
            "url": c.get("url", ""),
            "title": c.get("title", ""),
            "depth": c.get("depth", ""),
            "content_chars": len(c.get("content", "") or ""),
            "snippets_count": len(c.get("snippets", []) or []),
        }
        for c in knowledge.get("citations", [])
    ]
    requests = [
        {
            "id": r.get("id", ""),
            "target_fact_ids": r.get("target_fact_ids", []),
            "evidence_needed": r.get("evidence_needed", ""),
        }
        for r in es.get("evidence_requests", [])
    ]

    web_search_calls = sum(1 for c in recorded_calls if c["tool"] == "web_search")
    url_content_calls = sum(1 for c in recorded_calls if c["tool"] == "url_content")
    # Blocked/failed fetches (403s on bot-protected hosts, timeouts, ...) —
    # tracked separately because they consume call budget and block the
    # page-rescue path while producing no content. The per-class dict is
    # parsed from the stable prefixes in ToolResult.error (see
    # url_content's failure classification); synthetic blocked-host
    # interceptions never reach the executor, so both figures count real
    # fetch failures only.
    url_content_failures = sum(
        1 for c in recorded_calls if c["tool"] == "url_content" and not c.get("success")
    )
    url_content_failure_classes: dict[str, int] = {}
    for c in recorded_calls:
        if c["tool"] == "url_content" and not c.get("success"):
            cls = failure_class(c.get("error"))
            url_content_failure_classes[cls] = url_content_failure_classes.get(cls, 0) + 1

    artifact = {
        "index": index,
        "facts": facts,
        "original_fact_ids": sorted(original_fact_ids) if original_fact_ids is not None else None,
        "citations": citations,
        "requests": requests,
        "request_attempts": es.get("request_attempts", {}),
        "issued_queries": es.get("issued_queries", []),
        "tool_calls": recorded_calls,
        "counts": {
            "web_search_calls": web_search_calls,
            "url_content_calls": url_content_calls,
            "url_content_failures": url_content_failures,
            "url_content_failure_classes": url_content_failure_classes,
            "total_tool_calls": len(recorded_calls),
        },
        # Loop outcome of the final research pass (rounds / cap hit /
        # stall signal). None on artifacts from graphs that never
        # surfaced it — the summary treats those as missing.
        "research_loop": es.get("research_loop"),
        "budget_consumed": round(es.get("budget_limit", 0.0) - es.get("budget_remaining", 0.0), 2),
        "budget_limit": es.get("budget_limit", 0.0),
        "duplicate_queries_intercepted": sum(
            1
            for attempts in es.get("request_attempts", {}).values()
            for a in attempts
            if a.get("deduped")
        ),
    }
    # web_search calls whose query serves no evidence request — the
    # agent searched off-plan. Counted at call level (failed searches
    # included): a call is attributed only when its exact query text
    # appears in some request's attempt ledger.
    artifact["counts"]["unattributed_web_search_calls"] = sum(
        1
        for c in recorded_calls
        if c["tool"] == "web_search"
        and c.get("args", {}).get("query", "") not in attributed_queries(artifact)
    )
    return artifact


# ---------------------------------------------------------------------------
# Per-fact retrieval attribution
# ---------------------------------------------------------------------------


def fact_queries(artifact: dict) -> dict[str, list[str]]:
    """fact_id → distinct web_search queries attributed to it.

    Attribution follows the evidence-request chain: requests targeting the
    fact → their recorded attempts (tool == web_search). Attempts recorded
    as deduped still count — a rejected duplicate returns the same results
    as its original, so recall semantics are unchanged.
    """
    fact_ids = {f["id"] for f in artifact.get("facts", [])}
    by_request: dict[str, list[str]] = {}
    for req in artifact.get("requests", []):
        rid = req["id"]
        attempts = artifact.get("request_attempts", {}).get(rid, [])
        queries = [
            a.get("query", "")
            for a in attempts
            if a.get("tool") == "web_search" and a.get("query")
        ]
        by_request[rid] = queries

    mapping: dict[str, list[str]] = {}
    for req in artifact.get("requests", []):
        for fid in req.get("target_fact_ids", []):
            if fid in fact_ids:
                for q in by_request.get(req["id"], []):
                    if q not in mapping.setdefault(fid, []):
                        mapping[fid].append(q)
    return mapping


def _search_results_by_query(artifact: dict) -> dict[str, list[dict]]:
    """query → ranked results, from the recorded call stream (first
    execution of each query wins; later repeats are cache hits)."""
    ranked: dict[str, list[dict]] = {}
    for call in artifact.get("tool_calls", []):
        if call["tool"] != "web_search" or not call.get("success"):
            continue
        query = call.get("args", {}).get("query", "")
        if query and query not in ranked:
            ranked[query] = call.get("results", [])
    return ranked


def _page_texts(artifact: dict) -> dict[str, str]:
    """url → fetched body excerpt, from recorded url_content calls."""
    pages: dict[str, str] = {}
    for call in artifact.get("tool_calls", []):
        if call["tool"] != "url_content" or not call.get("success"):
            continue
        url = call.get("args", {}).get("url", "")
        if url:
            # Reconstruct enough body for scoring from the output head;
            # full bodies are the Phase-2 store's job, not the harness's.
            pages[url] = call.get("output_head", "")
    return pages


def attributed_queries(artifact: dict) -> set[str]:
    """All query texts attributed to ANY fact via the request chain.

    The complement (successful or failed web_search calls whose query is
    not in this set) is the unattributed population: searches the agent
    issued without serving a planned evidence request. Coverage_any
    scores their material against every fact.
    """
    attributed: set[str] = set()
    for queries in fact_queries(artifact).values():
        attributed.update(queries)
    return attributed


def assemble_fact_entries(artifact: dict, fact_id: str) -> tuple[list[dict], list[dict]]:
    """(snippet_entries, page_entries) for scoring one fact.

    Snippet entries carry ``rank`` (1-based position within their query's
    results) so ``found_at_k`` can be derived from judge output. Page
    entries are url_content bodies for URLs that appeared in the fact's
    search results — acquisition evidence, unranked.

    Candidates are the fact's attributed queries PLUS every unattributed
    query executed in the repeat (entries tagged ``attributed``).
    Unattributed entries are appended after attributed ones so the
    per-fact cap can never drop an attributed entry in favor of an
    unattributed one — attributed (back-comparable) metrics stay exact;
    ``present_any`` / ``found_at_k_any`` are derived from the union.
    """
    queries = fact_queries(artifact).get(fact_id, [])
    attributed = attributed_queries(artifact)
    ranked = _search_results_by_query(artifact)
    pages = _page_texts(artifact)

    def _snippet_entries_for(query: str, is_attributed: bool) -> tuple[list[dict], set[str]]:
        entries: list[dict] = []
        urls: set[str] = set()
        for i, r in enumerate(ranked.get(query, [])[:K_MAX]):
            entries.append(
                {
                    "query": query,
                    "rank": i + 1,
                    "url": r.get("url", ""),
                    "text": f"{r.get('title', '')} — {r.get('snippet', '')}".strip(),
                    "attributed": is_attributed,
                }
            )
            if r.get("url"):
                urls.add(r["url"])
        return entries, urls

    snippet_entries: list[dict] = []
    page_urls: set[str] = set()
    for q in queries:
        entries, urls = _snippet_entries_for(q, True)
        snippet_entries.extend(entries)
        page_urls |= urls
    # Unattributed material comes after attributed entries so the per-fact
    # cap can never drop an attributed entry in favor of an unattributed
    # one — attributed (back-comparable) metrics stay exact while
    # present_any / found_at_k_any score the union.
    unattributed_queries = [q for q in ranked if q not in attributed]
    for q in unattributed_queries:
        entries, urls = _snippet_entries_for(q, False)
        snippet_entries.extend(entries)
        page_urls |= urls

    page_entries = [
        {"url": url, "rank": None, "text": pages[url][:_PAGE_EXCERPT_CHARS], "attributed": True}
        for url in sorted(page_urls)
        if url in pages
    ]

    all_entries = snippet_entries + page_entries
    if len(all_entries) > _MAX_ENTRIES_PER_FACT:
        # Keep ranks low (they matter most) — entries are already ordered
        # by query then rank, and page entries sit at the end.
        all_entries = all_entries[:_MAX_ENTRIES_PER_FACT]
        snippet_entries = [e for e in all_entries if e["rank"] is not None]
        page_entries = [e for e in all_entries if e["rank"] is None]
    return snippet_entries, page_entries


# ---------------------------------------------------------------------------
# Scorers
# ---------------------------------------------------------------------------

_SCORER_SYSTEM = """You assess retrieval quality for a research pipeline.
You will be given one piece of needed information (a "fact needed") and a
numbered list of retrieved passages (search-result snippets and fetched
page excerpts). Decide whether the information needed is actually present
in any passage — not merely topically related, but sufficient to establish
the needed fact. Respond with JSON only:
{"present": true/false, "passages": [<ints, the passage numbers that
contain it>], "quote": "<short verbatim quote from one passage, or empty>"}
"""


class LLMRecallScorer:
    """Per-fact presence scorer using the judge endpoint."""

    def __init__(self, judge_config: JudgeConfig):
        from moira.inference.client import InferenceClient

        self._config = judge_config
        self._client = InferenceClient(
            base_url=judge_config.endpoint,
            api_key=judge_config.api_key,
        )
        self._semaphore = asyncio.Semaphore(_SCORER_CONCURRENCY)

    async def start(self) -> None:
        await self._client.start()

    async def stop(self) -> None:
        await self._client.stop()

    async def score(self, fact_needed: str, entries: list[dict]) -> dict:
        """Score one fact against numbered entries.

        Returns {present, passages, quote}. Rank-derived fields
        (found_at_k) are computed by the caller from ``passages``.
        """
        listing = "\n".join(
            f"[{i}] ({'page' if e['rank'] is None else 'rank ' + str(e['rank'])}) {e['text']}"
            for i, e in enumerate(entries)
        )
        messages = [
            {"role": "system", "content": _SCORER_SYSTEM},
            {
                "role": "user",
                "content": f"Fact needed: {fact_needed}\n\nPassages:\n{listing}",
            },
        ]
        async with self._semaphore:
            response = await self._client.chat_completion(
                model=self._config.model,
                messages=messages,
                temperature=0.0,
            )
        parsed = _parse_scorer_json(response.content or "")
        present = bool(parsed.get("present"))
        passages = parsed.get("passages", [])
        if not isinstance(passages, list):
            passages = []
        quote = str(parsed.get("quote", ""))[:300]
        return {"present": present, "passages": passages, "quote": quote}


def _parse_scorer_json(raw: str) -> dict:
    from moira.workflow.nodes._helpers import _parse_json_object

    parsed = _parse_json_object(raw)
    return parsed if isinstance(parsed, dict) else {}


def load_gold(question_id: str, gold_dir: Path | None = None) -> list[dict]:
    """Load gold-marker entries for a question.

    Format: ``{"entries": [{"fact_keywords": [...], "markers": [...]}]}``.
    A fact matches an entry when any keyword appears in its fact_needed
    text; recall is present when any marker appears in the retrieved text.
    """
    if gold_dir is None:
        gold_dir = Path(__file__).resolve().parent / "gold"
    path = gold_dir / f"{question_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No gold file for '{question_id}' at {path}. Create one or use --scorer llm."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    if not entries:
        raise ValueError(f"Gold file {path} has no entries")
    return entries


def gold_score_fact(fact_needed: str, snippet_entries: list[dict], gold: list[dict]) -> dict:
    """Deterministic marker-based presence scoring."""
    lowered = fact_needed.lower()
    matched = [e for e in gold if any(kw.lower() in lowered for kw in e.get("fact_keywords", []))]
    if not matched:
        # No gold entry for this fact — mark unknown rather than absent.
        return {"present": None, "found_at_k": None, "quote": "", "matched_gold": False}

    markers = [m for e in matched for m in e.get("markers", [])]
    texts_by_rank: dict[int, str] = {}
    for entry in snippet_entries:
        rank = entry.get("rank")
        if rank is not None:
            texts_by_rank.setdefault(rank, "")
            texts_by_rank[rank] += f"\n{entry['text']}"

    found_at_k = None
    for k in range(1, K_MAX + 1):
        combined = "".join(texts_by_rank.get(r, "") for r in range(1, k + 1))
        if any(m.lower() in combined.lower() for m in markers):
            found_at_k = k
            break
    present = found_at_k is not None
    quote = ""
    if present:
        combined = "".join(texts_by_rank.values())
        for m in markers:
            idx = combined.lower().find(m.lower())
            if idx >= 0:
                quote = combined[max(0, idx - 40) : idx + len(m) + 40]
                break
    return {
        "present": present,
        "found_at_k": found_at_k,
        "quote": quote,
        "matched_gold": True,
    }


# ---------------------------------------------------------------------------
# Scoring driver
# ---------------------------------------------------------------------------


async def score_repeat(
    artifact: dict,
    scorer: LLMRecallScorer | None,
    gold: list[dict] | None = None,
) -> dict:
    """Attach fact_scores to a repeat artifact. Returns {fact_id: score}.

    LLM path: one judge call per fact over its entries; found_at_k is the
    minimum rank among the passages the judge identified.
    Gold path: marker matching, fully deterministic.
    """
    queries_by_fact = fact_queries(artifact)

    if scorer is not None:

        async def _score_fact(fact: dict) -> tuple[str, dict]:
            fid = fact["id"]
            snippet_entries, page_entries = assemble_fact_entries(artifact, fid)
            entries = snippet_entries + page_entries
            if not entries:
                return fid, _no_query_score()
            raw = await scorer.score(fact["fact_needed"], entries)
            picks = [entries[i] for i in raw["passages"] if 0 <= i < len(entries)]
            attr_ranks = [
                e["rank"] for e in picks if e.get("attributed") and e["rank"] is not None
            ]
            any_ranks = [e["rank"] for e in picks if e["rank"] is not None]
            present_any = bool(raw["present"])
            # Attributed presence keeps the pre-union semantics: the judge
            # must have identified at least one attributed passage. An empty
            # pick list with present=true falls back to present_any — the
            # judge saw the material, it just didn't cite a passage number.
            present_attr = present_any and (not picks or any(e.get("attributed") for e in picks))
            return fid, {
                "present": present_attr,
                "found_at_k": min(attr_ranks) if attr_ranks else None,
                "present_any": present_any,
                "found_at_k_any": min(any_ranks) if any_ranks else None,
                "with_pages": raw["present"] and any(e["rank"] is None for e in picks),
                "queries": len(queries_by_fact.get(fid, [])),
                "quote": raw["quote"],
                "never_queried": False,
            }

        return dict(await asyncio.gather(*[_score_fact(f) for f in artifact["facts"]]))

    if gold is None:
        raise ValueError("Gold scorer requires a gold entry list")

    fact_scores: dict[str, dict] = {}
    for fact in artifact["facts"]:
        fid = fact["id"]
        snippet_entries, page_entries = assemble_fact_entries(artifact, fid)
        if not snippet_entries and not page_entries:
            # Nothing retrieved for this fact — nothing to score. Gold
            # scoring would report "unknown" (no keyword match against
            # empty evidence); absence-of-retrieval is the fact we want.
            fact_scores[fid] = _no_query_score()
            continue
        # Union scoring (present_any / found_at_k_any) over all entries;
        # attributed scoring restricted to tagged entries — deterministic,
        # so two marker passes are cheap.
        result_any = gold_score_fact(fact["fact_needed"], snippet_entries, gold)
        attr_snippets = [e for e in snippet_entries if e.get("attributed")]
        result_attr = gold_score_fact(fact["fact_needed"], attr_snippets, gold)
        # Page-level presence only counts when a gold entry matched the
        # fact at all; unmatched facts stay unknown, not absent.
        with_pages = bool(result_any["present"])
        if not with_pages and result_any["matched_gold"]:
            pages_text = "\n".join(p["text"] for p in page_entries)
            markers = _markers_for_fact(fact["fact_needed"], gold)
            with_pages = any(m.lower() in pages_text.lower() for m in markers)
        fact_scores[fid] = {
            "present": result_attr["present"] if result_attr["matched_gold"] else False,
            "found_at_k": result_attr["found_at_k"],
            "present_any": result_any["present"],
            "found_at_k_any": result_any["found_at_k"],
            "with_pages": with_pages,
            "queries": len(queries_by_fact.get(fid, [])),
            "quote": result_any["quote"],
            "never_queried": not queries_by_fact.get(fid),
            "matched_gold": result_any["matched_gold"],
        }
    return fact_scores


def _no_query_score() -> dict:
    """Score for a fact whose requests produced no attributed queries."""
    return {
        "present": False,
        "found_at_k": None,
        "present_any": False,
        "found_at_k_any": None,
        "with_pages": False,
        "queries": 0,
        "quote": "",
        "never_queried": True,
    }


def _markers_for_fact(fact_needed: str, gold: list[dict]) -> list[str]:
    """Markers from gold entries whose keywords match this fact."""
    lowered = fact_needed.lower()
    return [
        m
        for e in gold
        if any(kw.lower() in lowered for kw in e.get("fact_keywords", []))
        for m in e.get("markers", [])
    ]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def append_summary_csv(payload: dict, csv_path: Path) -> Path:
    """Append one row per run to the accumulating summary CSV.

    Columns: question_id, variant, model, timestamp, repeats, then
    <summary_field>_mean / <summary_field>_sd for every field in the
    summary (definitions in moira_eval.metrics.harness_recall_summary).
    The file is rewritten wholesale on each append so the header can grow
    when new summary fields appear — old rows keep their values and gain
    blanks for new columns.
    """
    row = {
        "question_id": payload["question_id"],
        "variant": payload["variant"],
        "model": payload.get("model", ""),
        "judge_model": payload.get("judge_model", ""),
        "timestamp": payload["timestamp"],
        "repeats": len(payload.get("repeats", [])),
    }
    for key, vals in payload.get("summary", {}).items():
        row[f"{key}_mean"] = vals.get("mean", "")
        row[f"{key}_sd"] = vals.get("sd", "")

    base_fields = list(row)
    rows: list[dict] = []
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
            if reader.fieldnames:
                base_fields = reader.fieldnames + [
                    f for f in base_fields if f not in reader.fieldnames
                ]
    rows.append({k: row.get(k, "") for k in base_fields})

    tmp = csv_path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=base_fields, restval="")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(csv_path)
    return csv_path


def save_harness_result(payload: dict, question_id: str, variant: str) -> Path:
    results_dir = Path(__file__).resolve().parent / "results" / "harness"
    out_dir = results_dir / question_id / variant
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = payload["timestamp"].replace(":", "").replace("-", "")[:15]
    path = out_dir / f"{ts}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    csv_path = append_summary_csv(payload, results_dir / "summary.csv")
    print(f"summary row appended: {csv_path}", file=sys.stderr)
    return path


def print_report(payload: dict) -> None:
    summary = payload["summary"]
    print(
        f"\n=== {payload['question_id']} (variant={payload['variant']}, "
        f"repeats={len(payload['repeats'])}) ==="
    )
    for repeat in payload["repeats"]:
        counts = repeat["counts"]
        fetch_note = ""
        if counts.get("url_content_failures"):
            fetch_note = f", {counts['url_content_failures']} blocked"
        print(
            f"\n-- repeat {repeat['index'] + 1} "
            f"({counts['web_search_calls']} searches, "
            f"{counts['url_content_calls']} fetches{fetch_note}) --"
        )
        rows = harness_per_fact_recall(repeat)
        if not rows:
            print("  (no facts)")
            continue
        for r in rows:
            found = r["found_at_k"] if r["found_at_k"] is not None else "-"
            flags = []
            if not r["original"]:
                flags.append("spawn")
            if r["never_queried"]:
                flags.append("never-queried")
            if r["with_pages"]:
                flags.append("page-rescue")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            print(
                f"  {r['id']}  q={r['queries']}  found@k={found}  "
                f"recall@1={int(r['recall_at_1'])} @3={int(r['recall_at_3'])} "
                f"@5={int(r['recall_at_5'])}{suffix}"
            )
            if r["quote"]:
                print(f"      quote: {r['quote'][:120]}")
    print("\n-- summary --")
    for k in _KS:
        vals = summary[f"recall_at_{k}"]
        ovals = summary[f"recall_at_{k}_original"]
        qvals = summary[f"recall_at_{k}_queried"]
        print(
            f"recall@{k}: {vals['mean']:.2f} ± {vals['sd']:.2f}  "
            f"(original: {ovals['mean']:.2f} ± {ovals['sd']:.2f}; "
            f"queried: {qvals['mean']:.2f} ± {qvals['sd']:.2f})"
        )
    wp = summary["recall_with_pages"]
    print(f"recall w/ pages: {wp['mean']:.2f} ± {wp['sd']:.2f}")
    qprf = summary["queries_per_resolved_fact"]
    print(f"queries/resolved fact: {qprf['mean']:.2f} ± {qprf['sd']:.2f}")
    cov = summary["coverage"]
    print(f"planning coverage (original facts queried): {cov['mean']:.2f} ± {cov['sd']:.2f}")
    print(
        f"unresolved facts/run: {summary['unresolved_fact_count']['mean']:.1f} "
        f"(original: {summary['unresolved_original_fact_count']['mean']:.1f} "
        f"of {summary['facts_per_run']['mean'] - summary['spawned_fact_count']['mean']:.1f}; "
        f"spawned: {summary['spawned_fact_count']['mean']:.1f})"
    )
    print(f"never-queried facts/run: {summary['never_queried_fact_count']['mean']:.1f}")
    print(
        f"web_search/url_content per run: "
        f"{summary['web_search_calls']['mean']:.1f}/"
        f"{summary['url_content_calls']['mean']:.1f}"
    )
    ucf = summary.get("url_content_failures")
    if ucf:
        print(
            f"url_content blocked/run: {ucf['mean']:.1f} "
            f"(recall w/ pages is structurally capped by this)"
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _apply_variant(config: MoiraConfig, variant: str) -> MoiraConfig:
    """Map a harness variant label onto research-loop config (Phase 3).

    ``query-writer`` flips the delegated query-writer hook on; the
    default ``freeform`` label (and any label without a config mapping
    yet — e.g. future fan-out variants) leaves research settings
    untouched. Returns a copy so the caller's config stays immutable.
    """
    if variant == "query-writer":
        return config.model_copy(update={"research": ResearchSettings(query_writer_enabled=True)})
    return config


async def _run_single(
    question_id: str,
    question_text: str,
    variant: str,
    repeats: int,
    budget: int | None,
    scorer_kind: str,
    config: MoiraConfig,
    milestone: bool = False,
) -> dict:
    """One question, repeats → scored payload → saved artifact.

    Assumes services are already initialized (see run_harness /
    run_harness_all) — the sweep shares one init across questions.
    """
    from moira.service_setup import service_provider

    config = _apply_variant(config, variant)

    budget_limit = float(
        budget
        if budget is not None
        else await _settings_or(config, "budget.default_limit", config.budget.default_limit)
    )
    tool_dicts = _tool_dicts()

    # Record the resolved intelligence model — the harness's results
    # are only interpretable next to the model that wrote the queries.
    registry = service_provider("model_registry")
    resolved = await registry.resolve("intelligence", conversation_id="")
    model_id = getattr(resolved, "model_id", "")

    subgraph = build_retrieval_subgraph()

    repeat_artifacts = []
    for i in range(repeats):
        print(f"Running repeat {i + 1}/{repeats}...", file=sys.stderr)
        repeat_artifacts.append(
            await run_repeat(subgraph, question_text, budget_limit, tool_dicts, i, config)
        )

    # Scoring happens after all repeats so a scorer failure never
    # wastes completed retrieval passes.
    scorer: LLMRecallScorer | None = None
    gold: list[dict] | None = None
    judge_model: str | None = None
    if scorer_kind == "llm":
        # Iteration (default) vs milestone scoring differ only in the
        # judge model — see judge_config_from_env's purpose slots.
        purpose = "milestone" if milestone else "iteration"
        judge_config = judge_config_from_env(purpose)
        if judge_config is None:
            raise SystemExit(
                f"LLM scorer requires MOIRA_EVAL_JUDGE_ENDPOINT and "
                f"{judge_model_var(purpose)} (or use --scorer gold)."
            )
        scorer = LLMRecallScorer(judge_config)
        # Judge identity is part of the measurement — recall verdicts are
        # only comparable across runs scored by the same judge model.
        judge_model = judge_config.model
        await scorer.start()
    else:
        gold = load_gold(question_id)

    try:
        scored = []
        for artifact in repeat_artifacts:
            fact_scores = await score_repeat(artifact, scorer, gold)
            scored.append({**artifact, "fact_scores": fact_scores})
    finally:
        if scorer is not None:
            await scorer.stop()

    summary = harness_recall_summary(scored, ks=_KS)

    payload = {
        "question_id": question_id,
        "question_text": question_text,
        "variant": variant,
        "scorer": scorer_kind,
        "judge_model": judge_model or "",
        "model": model_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repeats": scored,
        "summary": summary,
    }
    path = save_harness_result(payload, question_id, variant)
    print(f"saved: {path}", file=sys.stderr)
    return payload


async def run_harness(
    question_id: str,
    question_text: str,
    variant: str,
    repeats: int,
    budget: int | None,
    scorer_kind: str,
    milestone: bool = False,
) -> dict:
    """Initialize real services, run one question, score, summarize, save."""
    from moira.service_setup import init_services, shutdown_services

    config = load_config()
    await init_services(config)
    try:
        return await _run_single(
            question_id,
            question_text,
            variant,
            repeats,
            budget,
            scorer_kind,
            config,
            milestone=milestone,
        )
    finally:
        await shutdown_services()


async def run_harness_all(
    variant: str,
    repeats: int,
    budget: int | None,
    scorer_kind: str,
    milestone: bool = False,
) -> tuple[list[dict], list[str]]:
    """Sweep every benchmark question under one services init.

    A question that fails (model error, scorer failure, ...) is recorded
    and skipped so a single bad question doesn't waste the rest of the
    sweep. Returns (payloads, failed_question_ids).
    """
    from moira.service_setup import init_services, shutdown_services

    config = load_config()
    await init_services(config)
    payloads: list[dict] = []
    failed: list[str] = []
    try:
        for qid in sorted(QUESTIONS):
            print(f"\n=== question {qid} ===", file=sys.stderr)
            try:
                payloads.append(
                    await _run_single(
                        qid,
                        QUESTIONS[qid].text,
                        variant,
                        repeats,
                        budget,
                        scorer_kind,
                        config,
                        milestone=milestone,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - sweep must continue
                print(f"question {qid} FAILED: {exc!r}", file=sys.stderr)
                failed.append(qid)
    finally:
        await shutdown_services()
    return payloads, failed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Retrieval-isolation harness: per-fact recall@k for the "
        "retrieval half of the workflow.",
    )
    parser.add_argument("--question", default=None, help="Predefined question ID.")
    parser.add_argument("--text", default=None, help="Adhoc question text.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Sweep every benchmark question (one artifact + CSV row each; "
        "a failing question is skipped, not fatal).",
    )
    parser.add_argument(
        "--variant",
        default="freeform",
        help="Label for the query-generation variant (default: freeform).",
    )
    parser.add_argument("--repeats", type=int, default=3, help="Repeat runs (default: 3).")
    parser.add_argument(
        "--scorer",
        choices=["llm", "gold"],
        default="llm",
        help="Recall scorer (default: llm; gold needs moira_eval/gold/<qid>.json).",
    )
    parser.add_argument(
        "--milestone",
        action="store_true",
        help="Score with the milestone-baseline judge "
        "(MOIRA_EVAL_JUDGE_MODEL_MILESTONE) instead of the iteration judge "
        "(MOIRA_EVAL_JUDGE_MODEL_ITERATION).",
    )
    parser.add_argument("--budget", type=int, default=None, help="Budget override.")
    args = parser.parse_args()

    if args.all:
        if args.question or args.text:
            parser.error("--all cannot be combined with --question or --text.")
            return
        payloads, failed = asyncio.run(
            run_harness_all(
                variant=args.variant,
                repeats=args.repeats,
                budget=args.budget,
                scorer_kind=args.scorer,
                milestone=args.milestone,
            )
        )
        for payload in payloads:
            print_report(payload)
        print(
            f"\nsweep complete: {len(payloads)} questions ok"
            + (f", {len(failed)} failed ({', '.join(failed)})" if failed else "")
        )
        sys.exit(1 if failed else 0)

    if args.question:
        q = get_question(args.question)
        if q is None:
            available = ", ".join(sorted(QUESTIONS.keys()))
            print(f"Unknown question '{args.question}'. Available: {available}", file=sys.stderr)
            sys.exit(1)
        question_id, question_text = q.id, q.text
    elif args.text:
        question_id, question_text = "adhoc", args.text
    else:
        parser.error("Provide --question, --text, or --all.")
        return

    payload = asyncio.run(
        run_harness(
            question_id=question_id,
            question_text=question_text,
            variant=args.variant,
            repeats=args.repeats,
            budget=args.budget,
            scorer_kind=args.scorer,
            milestone=args.milestone,
        )
    )
    print_report(payload)


if __name__ == "__main__":
    main()
