"""Tests for the retrieval-isolation harness (retrieval-quality-plan.md
Phase 1).

Two layers:

- Pure-function tests over canned artifact dicts: attribution, entry
  assembly, gold scoring, LLM-score parsing, metrics aggregation.
- One injected-services smoke test: the real retrieval subgraph
  (decomposition → tool_identification → planning → research → END)
  runs against mocked model/executor services — the same pattern as
  tests/test_integration.py — verifying that the recording wrapper
  captures calls and the artifact assembler reads real state shapes.
"""

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moira.config import MoiraConfig
from moira.inference.client import ChatResponse, InferenceClient
from moira.inference.registry import ModelRegistry, ResolvedModel
from moira.service_setup import _services
from moira.tools.base import ToolDefinition, ToolResult
from moira_eval.judge import JudgeConfig
from moira_eval.metrics import harness_per_fact_recall, harness_recall_summary
from moira_eval.retrieval_harness import (
    LLMRecallScorer,
    RecordingExecutor,
    assemble_fact_entries,
    build_repeat_artifact,
    build_retrieval_subgraph,
    fact_queries,
    gold_score_fact,
    load_gold,
    run_repeat,
    score_repeat,
)

DECOMPOSITION_JSON = json.dumps(
    {
        "user_goal": "Find the capital of France",
        "topic": "Geography",
        "entities": ["France"],
        "concepts": ["capital cities"],
        "unknown_facts": [{"subject": "France", "fact_needed": "Current capital city of France"}],
    }
)

PLANNING_JSON = json.dumps(
    {
        "evidence_requests": [
            {
                "target_fact_ids": ["f001"],
                "evidence_needed": "Current capital city of France",
                "candidate_tools": ["web_search"],
                "fallback": True,
            }
        ],
    }
)

RESEARCH_JSON = json.dumps(
    {
        "tool_calls": [],
        "discovered_facts": [{"fact_id": "f001", "claim": "Paris is the capital of France"}],
        "sources": [],
    }
)

# Research must emit the tool call itself (text mode) with the
# request_id it serves — attribution (request_attempts, fact promotion)
# is strict: calls without a request_id serve nothing.
RESEARCH_TOOL_CALL_JSON = json.dumps(
    {
        "tool_calls": [
            {
                "tool": "web_search",
                "args": {"query": "capital of France"},
                "request_id": "req0001",
            }
        ],
        "discovered_facts": [],
        "sources": [],
    }
)

_STREAM_WRITER_MODULES = [
    "moira.workflow.nodes.decomposition",
    "moira.workflow.nodes.tool_identification",
    "moira.workflow.nodes.planning",
    "moira.workflow.nodes.research",
    "moira.workflow.nodes.synthesis",
    "moira.workflow.nodes.research_review",
    "moira.workflow.nodes.evaluation",
    "moira.workflow.nodes.report_generation",
    "moira.workflow.nodes._helpers_deps",
]


def _fact(fid, subject="S", needed="N", status="unknown"):
    return {
        "id": fid,
        "subject": subject,
        "fact_needed": needed,
        "status": status,
        "citation_ids": [],
    }


def _score(present=False, found_at_k=None, with_pages=False, queries=0, **extra):
    return {
        "present": present,
        "found_at_k": found_at_k,
        "with_pages": with_pages,
        "queries": queries,
        "quote": "",
        "never_queried": queries == 0,
        **extra,
    }


def _canned_repeat(
    fact_scores,
    web_search_calls=0,
    url_content_calls=0,
    url_content_failures=0,
    original_ids=None,
):
    return {
        "facts": [_fact(fid) for fid in fact_scores],
        "fact_scores": fact_scores,
        # None = artifact predates the original-fact snapshot; metrics then
        # treat every fact as original.
        "original_fact_ids": original_ids,
        "counts": {
            "web_search_calls": web_search_calls,
            "url_content_calls": url_content_calls,
            "url_content_failures": url_content_failures,
            "total_tool_calls": web_search_calls + url_content_calls,
        },
    }


# ---------------------------------------------------------------------------
# Subgraph: compile + injected-services smoke test
# ---------------------------------------------------------------------------


class TestSubgraph:
    def test_compiles(self):
        graph = build_retrieval_subgraph()
        assert graph is not None
        # The four retrieval nodes are present; downstream nodes are not.
        node_names = set(graph.get_graph().nodes.keys())
        for expected in ("decomposition", "planning", "research"):
            assert expected in node_names
        for absent in ("synthesis", "research_review", "evaluation", "report_generation"):
            assert absent not in node_names

    async def test_smoke_run_records_and_assembles(self):
        """Full retrieval subgraph run with mocked services: the recording
        executor captures the web_search call and the artifact assembler
        reads the real final-state shapes."""
        config = MoiraConfig()

        client = AsyncMock(spec=InferenceClient)
        client.chat_completion.side_effect = [
            ChatResponse(content=DECOMPOSITION_JSON),
            ChatResponse(content=PLANNING_JSON),
            ChatResponse(content=RESEARCH_TOOL_CALL_JSON),
            ChatResponse(content=RESEARCH_JSON),
        ]
        resolved = ResolvedModel(model_id="test-model", client=client)
        registry = MagicMock(spec=ModelRegistry)
        registry.resolve = AsyncMock(return_value=resolved)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(
            return_value=[ToolDefinition(name="web_search", description="Search the web")]
        )

        mock_catalog = MagicMock()
        mock_catalog.get_all.return_value = [
            ToolDefinition(name="web_search", description="Search the web", invocation_cost=5.0)
        ]

        tool_result = ToolResult(
            tool_name="web_search",
            output="Paris is the capital of France",
            success=True,
            duration_ms=100,
            metadata={
                "results": [
                    {
                        "title": "France Wiki",
                        "url": "https://en.wikipedia.org/wiki/France",
                        "snippet": "Paris is the capital of France",
                    }
                ]
            },
        )
        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[tool_result])

        events = []

        def write(event):
            events.append(event)

        with ExitStack() as stack:
            for mod in _STREAM_WRITER_MODULES:
                stack.enter_context(patch(f"{mod}.get_stream_writer", return_value=write))
            try:
                _services.clear()
                _services["config"] = config
                _services["model_registry"] = registry
                _services["tool_discovery"] = mock_discovery
                _services["tool_catalog"] = mock_catalog
                _services["tool_executor"] = mock_executor

                subgraph = build_retrieval_subgraph()
                artifact = await run_repeat(
                    subgraph,
                    "What is the capital of France?",
                    budget_limit=float(config.budget.default_limit),
                    tool_dicts=({"web_search": 5.0}, {}, {}),
                    index=0,
                    config=config,
                )
            finally:
                _services.clear()

        # The call stream was captured with its ranked results.
        assert artifact["counts"]["web_search_calls"] == 1
        assert len(artifact["tool_calls"]) == 1
        call = artifact["tool_calls"][0]
        assert call["tool"] == "web_search"
        assert call["args"] == {"query": "capital of France"}
        assert call["results"][0]["url"] == "https://en.wikipedia.org/wiki/France"

        # State-derived fields assembled from the real shapes.
        assert len(artifact["facts"]) == 1
        assert artifact["facts"][0]["id"] == "f001"
        assert artifact["issued_queries"] == ["capital of France"]
        assert len(artifact["requests"]) >= 1

        # Attribution: the single query traces back to fact f001.
        queries = fact_queries(artifact)
        assert queries.get("f001") == ["capital of France"]

        # Gold scoring end-to-end: the snippet contains the marker.
        entries = assemble_fact_entries(artifact, "f001")
        assert len(entries[0]) == 1
        assert entries[0][0]["rank"] == 1
        assert entries[1] == []  # no url_content fetch happened
        gold_result = gold_score_fact(
            "Current capital city of France",
            entries[0],
            [{"fact_keywords": ["capital"], "markers": ["Paris is the capital"]}],
        )
        assert gold_result["present"] is True
        assert gold_result["found_at_k"] == 1


# ---------------------------------------------------------------------------
# RecordingExecutor
# ---------------------------------------------------------------------------


class TestRecordingExecutor:
    async def test_batch_records_calls_and_results(self):
        from moira.tools.base import ToolCall

        inner = AsyncMock()
        inner.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="ok",
                    success=True,
                    duration_ms=5,
                    metadata={
                        "cache_hit": False,
                        "results": [{"title": "t", "url": "u", "snippet": "s"}],
                    },
                )
            ]
        )
        recorder = RecordingExecutor(inner)
        calls = [ToolCall(id="c1", name="web_search", arguments={"query": "q"})]
        results = await recorder.execute_batch(calls)
        assert results[0].success is True
        assert len(recorder.calls) == 1
        recorded = recorder.calls[0]
        assert recorded["tool"] == "web_search"
        assert recorded["args"] == {"query": "q"}
        assert recorded["cache_hit"] is False
        assert recorded["results"] == [{"title": "t", "url": "u", "snippet": "s"}]
        assert recorded["output_chars"] == 2
        assert recorded["error"] is None

    async def test_records_failure_with_error_string(self):
        """Failed fetches keep the error text — output is empty on failure,
        so the error string is the only record of *why* (blocked host,
        timeout, oversized response)."""
        from moira.tools.base import ToolCall

        inner = AsyncMock()
        inner.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="url_content",
                    output="",
                    success=False,
                    duration_ms=900,
                    error="Failed to fetch https://www.bls.gov/x: 403 Forbidden",
                )
            ]
        )
        recorder = RecordingExecutor(inner)
        calls = [ToolCall(id="c1", name="url_content", arguments={"url": "https://www.bls.gov/x"})]
        results = await recorder.execute_batch(calls)
        assert results[0].success is False
        recorded = recorder.calls[0]
        assert recorded["success"] is False
        assert recorded["error"] == "Failed to fetch https://www.bls.gov/x: 403 Forbidden"
        assert recorded["output_chars"] == 0


# ---------------------------------------------------------------------------
# Attribution and entry assembly (canned artifacts)
# ---------------------------------------------------------------------------


def _attempts(request_id, entries):
    return {request_id: entries}


class TestAttribution:
    def test_fact_queries_joins_requests_and_attempts(self):
        artifact = {
            "facts": [_fact("f1"), _fact("f2")],
            "requests": [
                {"id": "r1", "target_fact_ids": ["f1"], "evidence_needed": "e"},
                {"id": "r2", "target_fact_ids": ["f2"], "evidence_needed": "e"},
            ],
            "request_attempts": {
                "r1": [
                    {"tool": "web_search", "query": "q1", "success": True},
                    {"tool": "url_content", "url": "https://x", "success": True},
                ],
                "r2": [
                    {"tool": "web_search", "query": "q2", "success": True},
                    {"tool": "web_search", "query": "q2", "success": False, "deduped": True},
                ],
            },
        }
        queries = fact_queries(artifact)
        assert queries["f1"] == ["q1"]
        # Duplicate query text is counted once.
        assert queries["f2"] == ["q2"]

    def test_assemble_fact_entries_ranks_and_pages(self):
        artifact = {
            "facts": [_fact("f1")],
            "requests": [{"id": "r1", "target_fact_ids": ["f1"], "evidence_needed": "e"}],
            "request_attempts": _attempts(
                "r1", [{"tool": "web_search", "query": "q1", "success": True}]
            ),
            "tool_calls": [
                {
                    "tool": "web_search",
                    "args": {"query": "q1"},
                    "success": True,
                    "results": [
                        {"title": "a", "url": "https://a", "snippet": "sa"},
                        {"title": "b", "url": "https://b", "snippet": "sb"},
                    ],
                },
                {
                    "tool": "url_content",
                    "args": {"url": "https://a"},
                    "success": True,
                    "output_head": "full body of page a",
                    "results": [],
                },
                {
                    "tool": "url_content",
                    "args": {"url": "https://unrelated"},
                    "success": True,
                    "output_head": "unrelated page",
                    "results": [],
                },
            ],
        }
        snippet_entries, page_entries = assemble_fact_entries(artifact, "f1")
        assert [e["rank"] for e in snippet_entries] == [1, 2]
        # Only pages whose URL appeared in the fact's search results.
        assert len(page_entries) == 1
        assert page_entries[0]["url"] == "https://a"

    def test_unattributed_fact_has_no_entries(self):
        artifact = {
            "facts": [_fact("f1")],
            "requests": [],
            "request_attempts": {},
            "tool_calls": [
                {
                    "tool": "web_search",
                    "args": {"query": "q1"},
                    "success": True,
                    "results": [{"title": "a", "url": "https://a", "snippet": "sa"}],
                }
            ],
        }
        snippet_entries, page_entries = assemble_fact_entries(artifact, "f1")
        assert snippet_entries == []
        assert page_entries == []


# ---------------------------------------------------------------------------
# Gold scorer
# ---------------------------------------------------------------------------


class TestGoldScorer:
    def test_found_at_k_follows_rank(self):
        entries = [
            {"query": "q", "rank": 1, "text": "weather in Paris"},
            {"query": "q", "rank": 2, "text": "Paris receives 641 mm of rain annually"},
            {"query": "q", "rank": 3, "text": "more text"},
        ]
        gold = [{"fact_keywords": ["rainfall"], "markers": ["641 mm"]}]
        result = gold_score_fact("Average rainfall in Paris", entries, gold)
        assert result["present"] is True
        assert result["found_at_k"] == 2
        assert "641 mm" in result["quote"]

    def test_absent_when_no_marker(self):
        entries = [{"query": "q", "rank": 1, "text": "nothing relevant"}]
        gold = [{"fact_keywords": ["rainfall"], "markers": ["641 mm"]}]
        result = gold_score_fact("Average rainfall in Paris", entries, gold)
        assert result["present"] is False
        assert result["found_at_k"] is None

    def test_unknown_fact_when_no_keyword_match(self):
        result = gold_score_fact(
            "Population of France", [], [{"fact_keywords": ["rainfall"], "markers": ["641 mm"]}]
        )
        assert result["present"] is None
        assert result["matched_gold"] is False

    def test_load_gold_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_gold("no-such-question", gold_dir=tmp_path)

    def test_load_gold_reads_entries(self, tmp_path: Path):
        (tmp_path / "q1.json").write_text(
            json.dumps({"entries": [{"fact_keywords": ["k"], "markers": ["m"]}]}),
            encoding="utf-8",
        )
        gold = load_gold("q1", gold_dir=tmp_path)
        assert gold == [{"fact_keywords": ["k"], "markers": ["m"]}]

    async def test_score_repeat_gold_with_pages_rescue(self):
        """A fact absent from snippets but present in a fetched page body
        scores with_pages=True."""
        artifact = {
            "facts": [_fact("f1", needed="Average rainfall in Paris")],
            "requests": [{"id": "r1", "target_fact_ids": ["f1"], "evidence_needed": "e"}],
            "request_attempts": _attempts(
                "r1", [{"tool": "web_search", "query": "q1", "success": True}]
            ),
            "tool_calls": [
                {
                    "tool": "web_search",
                    "args": {"query": "q1"},
                    "success": True,
                    "results": [
                        {"title": "a", "url": "https://a", "snippet": "tourism in Paris"},
                    ],
                },
                {
                    "tool": "url_content",
                    "args": {"url": "https://a"},
                    "success": True,
                    "output_head": "Paris receives 641 mm of rain annually",
                    "results": [],
                },
            ],
        }
        gold = [{"fact_keywords": ["rainfall"], "markers": ["641 mm"]}]
        scores = await score_repeat(artifact, scorer=None, gold=gold)
        assert scores["f1"]["present"] is False
        assert scores["f1"]["found_at_k"] is None
        assert scores["f1"]["with_pages"] is True

    async def test_score_repeat_never_queried(self):
        artifact = {
            "facts": [_fact("f1")],
            "requests": [],
            "request_attempts": {},
            "tool_calls": [],
        }
        gold = [{"fact_keywords": ["anything"], "markers": ["m"]}]
        scores = await score_repeat(artifact, scorer=None, gold=gold)
        assert scores["f1"]["present"] is False
        assert scores["f1"]["never_queried"] is True


# ---------------------------------------------------------------------------
# LLM scorer
# ---------------------------------------------------------------------------


class _StubScorer:
    """Fixed-verdict stand-in for LLMRecallScorer in score_repeat tests."""

    def __init__(self, verdict):
        self._verdict = verdict
        self.seen: list[list[dict]] = []

    async def score(self, fact_needed: str, entries: list[dict]) -> dict:
        self.seen.append(entries)
        return self._verdict


class TestLLMScorer:
    async def test_score_repeat_derives_found_at_k_and_with_pages(self):
        # Entries seen by the scorer: 2 snippets (ranks 1, 2) + 1 page
        # entry for the rank-1 result's URL, built from tool_calls below.
        artifact = {
            "facts": [_fact("f1")],
            "requests": [{"id": "r1", "target_fact_ids": ["f1"], "evidence_needed": "e"}],
            "request_attempts": _attempts(
                "r1", [{"tool": "web_search", "query": "q", "success": True}]
            ),
            "tool_calls": [
                {
                    "tool": "web_search",
                    "args": {"query": "q"},
                    "success": True,
                    "results": [
                        {"title": "a", "url": "https://a", "snippet": "miss"},
                        {"title": "b", "url": "https://b", "snippet": "hit"},
                    ],
                },
                {
                    "tool": "url_content",
                    "args": {"url": "https://a"},
                    "success": True,
                    "output_head": "page hit",
                    "results": [],
                },
            ],
        }
        stub = _StubScorer({"present": True, "passages": [1, 2], "quote": "hit"})
        scores = await score_repeat(artifact, scorer=stub, gold=None)
        # Entries passed to the scorer: 2 snippets + 1 page.
        assert len(stub.seen[0]) == 3
        # found_at_k = min snippet rank among passages {1 (rank 2), 2 (page)}.
        assert scores["f1"]["found_at_k"] == 2
        assert scores["f1"]["with_pages"] is True
        assert scores["f1"]["queries"] == 1

    async def test_scorer_parses_judge_json(self):
        scorer = LLMRecallScorer(JudgeConfig(endpoint="http://localhost:1", model="judge-model"))
        scorer._client = AsyncMock(spec=InferenceClient)
        scorer._client.chat_completion = AsyncMock(
            return_value=ChatResponse(
                content='```json\n{"present": true, "passages": [0], "quote": "x"}\n```'
            )
        )
        entries = [{"query": "q", "rank": 1, "text": "t"}]
        result = await scorer.score("needed", entries)
        assert result["present"] is True
        assert result["passages"] == [0]
        assert result["quote"] == "x"

    async def test_scorer_tolerates_bad_json(self):
        scorer = LLMRecallScorer(JudgeConfig(endpoint="http://localhost:1", model="judge-model"))
        scorer._client = AsyncMock(spec=InferenceClient)
        scorer._client.chat_completion = AsyncMock(
            return_value=ChatResponse(content="not json at all")
        )
        result = await scorer.score("needed", [{"query": "q", "rank": 1, "text": "t"}])
        assert result["present"] is False
        assert result["passages"] == []


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestHarnessMetrics:
    def test_per_fact_recall_rows(self):
        repeat = _canned_repeat(
            {
                "f1": _score(present=True, found_at_k=1, queries=2),
                "f2": _score(present=True, found_at_k=4, queries=3),
                "f3": _score(present=False, found_at_k=None, queries=0),
            },
            original_ids=["f1", "f2"],
        )
        rows = harness_per_fact_recall(repeat)
        by_id = {r["id"]: r for r in rows}
        assert by_id["f1"]["recall_at_1"] is True
        assert by_id["f2"]["recall_at_1"] is False
        assert by_id["f2"]["recall_at_3"] is False
        assert by_id["f2"]["recall_at_5"] is True
        assert by_id["f3"]["recall_at_5"] is False
        assert by_id["f3"]["never_queried"] is True
        assert by_id["f1"]["original"] is True
        assert by_id["f3"]["original"] is False

    def test_per_fact_recall_defaults_all_original(self):
        # Artifacts without original_fact_ids: every fact counts as original.
        repeat = _canned_repeat({"f1": _score(present=True, found_at_k=1, queries=1)})
        rows = harness_per_fact_recall(repeat)
        assert rows[0]["original"] is True

    def test_summary_separates_spawn_from_original(self):
        repeat = _canned_repeat(
            {
                "f1": _score(present=True, found_at_k=1, queries=1),
                "f2": _score(present=False, found_at_k=None, queries=0),
                "s1": _score(present=False, found_at_k=None, queries=0),  # spawn
            },
            original_ids=["f1", "f2"],
        )
        summary = harness_recall_summary([repeat])
        # All-facts recall is deflated by the unqueried spawn fact...
        assert summary["recall_at_1"]["mean"] == pytest.approx(1 / 3, abs=1e-3)
        # ...original-only recall is not.
        assert summary["recall_at_1_original"]["mean"] == pytest.approx(0.5, abs=1e-3)
        # Queried-only recall isolates retrieval effectiveness.
        assert summary["recall_at_1_queried"]["mean"] == pytest.approx(1.0, abs=1e-3)
        assert summary["coverage"]["mean"] == pytest.approx(0.5, abs=1e-3)
        assert summary["spawned_fact_count"]["mean"] == pytest.approx(1.0)
        assert summary["unresolved_original_fact_count"]["mean"] == pytest.approx(1.0)

    def test_summary_aggregates_across_repeats(self):
        repeat1 = _canned_repeat(
            {
                "f1": _score(present=True, found_at_k=1, queries=2),
                "f2": _score(present=True, found_at_k=4, queries=3),
                "f3": _score(present=False, found_at_k=None, queries=0),
            },
            web_search_calls=5,
            url_content_calls=1,
            url_content_failures=1,
        )
        repeat2 = _canned_repeat(
            {
                "f1": _score(present=True, found_at_k=2, queries=1),
                "f2": _score(present=False, found_at_k=None, queries=4),
                "f3": _score(present=False, found_at_k=None, queries=2),
            },
            web_search_calls=6,
            url_content_calls=0,
        )
        summary = harness_recall_summary([repeat1, repeat2])
        # Metrics round to 4 decimals; compare with a tolerance that
        # absorbs the rounding.
        # repeat1: f1@1, f2@4, f3 absent → @1:1/3, @3:1/3, @5:2/3
        # repeat2: f1@2 only → @1:0, @3:1/3, @5:1/3
        assert summary["recall_at_1"]["mean"] == pytest.approx((1 / 3 + 0) / 2, abs=1e-3)
        assert summary["recall_at_3"]["mean"] == pytest.approx((1 / 3 + 1 / 3) / 2, abs=1e-3)
        assert summary["recall_at_5"]["mean"] == pytest.approx((2 / 3 + 1 / 3) / 2, abs=1e-3)
        assert summary["recall_at_1"]["sd"] > 0
        # Only resolved facts count toward queries_per_resolved_fact.
        assert summary["queries_per_resolved_fact"]["mean"] == pytest.approx(
            (2.5 + 1.0) / 2, abs=1e-3
        )
        assert summary["unresolved_fact_count"]["mean"] == pytest.approx(1.5)
        assert summary["never_queried_fact_count"]["mean"] == pytest.approx(0.5)
        assert summary["facts_per_run"]["mean"] == pytest.approx(3.0)
        assert summary["web_search_calls"]["mean"] == pytest.approx(5.5)
        assert summary["url_content_calls"]["mean"] == pytest.approx(0.5)
        assert summary["url_content_failures"]["mean"] == pytest.approx(0.5)
        assert summary["url_content_failures"]["sd"] > 0

    def test_summary_single_repeat_has_zero_sd(self):
        repeat = _canned_repeat({"f1": _score(present=True, found_at_k=1, queries=1)})
        summary = harness_recall_summary([repeat])
        assert summary["recall_at_1"]["mean"] == pytest.approx(1.0)
        assert summary["recall_at_1"]["sd"] == 0.0

    def test_summary_empty_repeats(self):
        summary = harness_recall_summary([])
        assert summary["recall_at_1"]["mean"] == 0.0


# ---------------------------------------------------------------------------
# Artifact assembly
# ---------------------------------------------------------------------------


class TestBuildRepeatArtifact:
    def test_counts_budget_and_dedup(self):
        final_state = {
            "knowledge": {
                "facts": [
                    {
                        "id": "f1",
                        "subject": "s",
                        "fact_needed": "n",
                        "status": "unverified",
                        "citation_ids": ["c1"],
                    }
                ],
                "citations": [
                    {
                        "id": "c1",
                        "url": "https://a",
                        "title": "t",
                        "depth": "page",
                        "content": "x" * 10,
                        "snippets": [],
                    }
                ],
            },
            "execution_state": {
                "evidence_requests": [
                    {"id": "r1", "target_fact_ids": ["f1"], "evidence_needed": "e"}
                ],
                "request_attempts": {
                    "r1": [
                        {"tool": "web_search", "query": "q1", "success": True},
                        {"tool": "web_search", "query": "q1", "success": False, "deduped": True},
                    ]
                },
                "issued_queries": ["q1"],
                "budget_limit": 100.0,
                "budget_remaining": 40.0,
            },
        }
        recorded = [
            {
                "tool": "web_search",
                "args": {"query": "q1"},
                "success": True,
                "duration_ms": 1,
                "cache_hit": None,
                "results": [],
                "output_head": "",
                "output_chars": 0,
            },
            {
                "tool": "url_content",
                "args": {"url": "https://a"},
                "success": True,
                "duration_ms": 2,
                "cache_hit": None,
                "results": [],
                "output_head": "x",
                "output_chars": 1,
            },
            {
                "tool": "url_content",
                "args": {"url": "https://blocked"},
                "success": False,
                "duration_ms": 3,
                "cache_hit": None,
                "results": [],
                "output_head": "",
                "output_chars": 0,
                "error": "Failed to fetch https://blocked: 403",
            },
        ]
        artifact = build_repeat_artifact(final_state, recorded, 0)
        assert artifact["counts"] == {
            "web_search_calls": 1,
            "url_content_calls": 2,
            "url_content_failures": 1,
            "total_tool_calls": 3,
        }
        assert artifact["budget_consumed"] == pytest.approx(60.0)
        assert artifact["duplicate_queries_intercepted"] == 1
        assert artifact["citations"][0]["content_chars"] == 10
        assert artifact["facts"][0]["status"] == "unverified"


# ---------------------------------------------------------------------------
# Summary CSV accumulation
# ---------------------------------------------------------------------------


def _summary_payload(question_id="q1", variant="freeform", model="m1", **summary_extra):
    summary = {
        "recall_at_5": {"mean": 0.5, "sd": 0.1},
        "coverage": {"mean": 0.3, "sd": 0.0},
    }
    summary.update(summary_extra)
    return {
        "question_id": question_id,
        "variant": variant,
        "model": model,
        "timestamp": "2026-09-13T18:09:53",
        "repeats": [{"index": 0}, {"index": 1}],
        "summary": summary,
    }


class TestAppendSummaryCsv:
    def test_appends_rows_with_mean_and_sd_columns(self, tmp_path):
        from moira_eval.retrieval_harness import append_summary_csv

        csv_path = tmp_path / "summary.csv"
        append_summary_csv(_summary_payload(), csv_path)
        append_summary_csv(_summary_payload(question_id="q2", model="m2"), csv_path)

        lines = csv_path.read_text().strip().splitlines()
        assert lines[0] == (
            "question_id,variant,model,judge_model,timestamp,repeats,"
            "recall_at_5_mean,recall_at_5_sd,coverage_mean,coverage_sd"
        )
        assert len(lines) == 3
        row = lines[1].split(",")
        assert row[0] == "q1" and row[2] == "m1" and row[5] == "2"
        assert row[6] == "0.5" and row[7] == "0.1"

    def test_header_grows_when_new_summary_fields_appear(self, tmp_path):
        from moira_eval.retrieval_harness import append_summary_csv

        csv_path = tmp_path / "summary.csv"
        append_summary_csv(_summary_payload(), csv_path)
        # A later harness version emits one extra summary field.
        append_summary_csv(
            _summary_payload(question_id="q2", spawn_rate={"mean": 4.7, "sd": 1.2}),
            csv_path,
        )

        lines = csv_path.read_text().strip().splitlines()
        assert "spawn_rate_mean" in lines[0] and "spawn_rate_sd" in lines[0]
        assert len(lines) == 3
        # Old row: blank for the new columns; new row: values present.
        assert lines[1].split(",")[-2:] == ["", ""]
        assert lines[2].split(",")[-2:] == ["4.7", "1.2"]


# ---------------------------------------------------------------------------
# Full-benchmark sweep
# ---------------------------------------------------------------------------


class TestRunHarnessAll:
    async def test_failed_question_is_skipped_not_fatal(self, monkeypatch, capsys):
        import moira.service_setup as service_setup
        from moira_eval import retrieval_harness as rh

        class _Q:
            def __init__(self, text):
                self.text = text

        calls = []

        async def _fake_single(
            qid, text, variant, repeats, budget, scorer_kind, config, milestone=False
        ):
            calls.append((qid, milestone))
            if qid == "q1":
                raise RuntimeError("boom")
            return {"question_id": qid, "summary": {}}

        async def _noop(*args, **kwargs):
            return None

        monkeypatch.setattr(rh, "QUESTIONS", {"q2": _Q("b"), "q1": _Q("a"), "q3": _Q("c")})
        monkeypatch.setattr(rh, "_run_single", _fake_single)
        monkeypatch.setattr(rh, "load_config", lambda: object())
        monkeypatch.setattr(service_setup, "init_services", _noop)
        monkeypatch.setattr(service_setup, "shutdown_services", _noop)

        payloads, failed = await rh.run_harness_all(
            variant="freeform", repeats=1, budget=None, scorer_kind="gold"
        )
        # All three attempted in sorted order; the failure didn't stop the sweep.
        assert calls == [("q1", False), ("q2", False), ("q3", False)]
        assert [p["question_id"] for p in payloads] == ["q2", "q3"]
        assert failed == ["q1"]

    async def test_milestone_flag_reaches_run_single(self, monkeypatch):
        import moira.service_setup as service_setup
        from moira_eval import retrieval_harness as rh

        class _Q:
            def __init__(self, text):
                self.text = text

        seen = []

        async def _fake_single(
            qid, text, variant, repeats, budget, scorer_kind, config, milestone=False
        ):
            seen.append(milestone)
            return {"question_id": qid, "summary": {}}

        async def _noop(*args, **kwargs):
            return None

        monkeypatch.setattr(rh, "QUESTIONS", {"q9": _Q("z")})
        monkeypatch.setattr(rh, "_run_single", _fake_single)
        monkeypatch.setattr(rh, "load_config", lambda: object())
        monkeypatch.setattr(service_setup, "init_services", _noop)
        monkeypatch.setattr(service_setup, "shutdown_services", _noop)

        await rh.run_harness_all(
            variant="freeform", repeats=1, budget=None, scorer_kind="llm", milestone=True
        )
        assert seen == [True]
