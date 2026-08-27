"""Tests for recall_source interception in the research loop.

These tests verify:
- _build_recall_source_result synthesizes correct output from citations
- _execute_tools partitions recall_source calls before url_content dedup
- _process_execution_results skips citation creation for recall_source
"""

from unittest.mock import AsyncMock

from moira.models.knowledge import Citation, Fact
from moira.tools.base import ToolCall, ToolResult


class TestBuildRecallSourceResult:
    """Tests for _build_recall_source_result — the synthetic result builder."""

    @staticmethod
    def _make_call(citation_id: str, call_id: str = "tc1") -> ToolCall:
        return ToolCall(
            id=call_id,
            name="recall_source",
            arguments={"citation_id": citation_id},
        )

    def test_found_with_full_content(self):
        """A matching citation returns title, URL, snippets, and content."""
        from moira.workflow.nodes.research import _build_recall_source_result

        citations: list[Citation] = [
            Citation(
                id="cit004",
                source="url_content",
                url="https://example.com/article",
                title="The Article",
                snippets=["First snippet", "Second snippet"],
                content="Full page body text here.",
            ),
        ]
        call = self._make_call("cit004")

        result = _build_recall_source_result(call, citations)
        assert result.success is True
        assert result.tool_name == "recall_source"
        assert result.metadata.get("synthetic") is True
        assert result.duration_ms == 0

        output = result.output
        assert "cit004" in output
        assert "The Article" in output
        assert "https://example.com/article" in output
        assert "First snippet" in output
        assert "Second snippet" in output
        assert "Full page body text here." in output

    def test_found_with_excerpt_fallback(self):
        """When no snippets list exists, falls back to excerpt."""
        from moira.workflow.nodes.research import _build_recall_source_result

        citations: list[Citation] = [
            Citation(
                id="cit002",
                source="web_search",
                excerpt="Short excerpt from search",
                content="Page content",
            ),
        ]
        call = self._make_call("cit002")

        result = _build_recall_source_result(call, citations)
        assert result.success is True
        assert "Short excerpt from search" in result.output
        assert "Page content" in result.output

    def test_found_with_content_only(self):
        """Citation with content but no snippets or excerpt."""
        from moira.workflow.nodes.research import _build_recall_source_result

        citations: list[Citation] = [
            Citation(
                id="cit010",
                source="url_content",
                content="Only page content, no snippets.",
            ),
        ]
        call = self._make_call("cit010")

        result = _build_recall_source_result(call, citations)
        assert result.success is True
        assert "Only page content, no snippets." in result.output

    def test_not_found_lists_available_ids(self):
        """An unknown citation ID returns an error with available IDs."""
        from moira.workflow.nodes.research import _build_recall_source_result

        citations: list[Citation] = [
            Citation(id="cit001", source="web_search"),
            Citation(id="cit002", source="url_content"),
        ]
        call = self._make_call("cit999")

        result = _build_recall_source_result(call, citations)
        assert result.success is False
        assert result.metadata.get("synthetic") is True
        assert "cit999" in result.output
        assert "cit001" in result.output
        assert "cit002" in result.output

    def test_not_found_empty_citations(self):
        """When no citations exist, returns '(none)' for available."""
        from moira.workflow.nodes.research import _build_recall_source_result

        call = self._make_call("cit001")
        result = _build_recall_source_result(call, [])
        assert result.success is False
        assert "(none)" in result.output


class TestExecuteToolsRecallPartition:
    """Tests for _execute_tools — verifies recall_source calls are
    intercepted and never reach the executor."""

    @staticmethod
    def _make_recall_call(citation_id: str, call_id: str = "rc1") -> ToolCall:
        return ToolCall(
            id=call_id,
            name="recall_source",
            arguments={"citation_id": citation_id},
        )

    @staticmethod
    def _make_url_call(url: str, call_id: str = "uc1") -> ToolCall:
        return ToolCall(id=call_id, name="url_content", arguments={"url": url})

    @staticmethod
    def _make_search_call(query: str, call_id: str = "sc1") -> ToolCall:
        return ToolCall(id=call_id, name="web_search", arguments={"query": query})

    async def test_recall_only_calls_never_execute(self):
        """When all calls are recall_source, the executor is never called."""
        from moira.workflow.nodes.research import _execute_tools

        citations: list[Citation] = [
            Citation(id="cit001", source="web_search", content="Content A"),
        ]
        calls = [self._make_recall_call("cit001", "rc1")]
        executor = AsyncMock()
        executor.execute_batch.return_value = []

        results = await _execute_tools(calls, {}, executor, citations, {"recall_source": 1})

        executor.execute_batch.assert_not_awaited()
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].metadata.get("synthetic") is True
        assert "Content A" in results[0].output

    async def test_mixed_calls_preserve_order(self):
        """recall_source mixed with web_search and url_content — results
        are returned in the original call order."""
        from moira.workflow.nodes.research import _execute_tools

        citations: list[Citation] = [
            Citation(id="cit005", source="url_content", content="Recalled!"),
        ]
        calls = [
            self._make_search_call("test", "c1"),
            self._make_recall_call("cit005", "c2"),
            self._make_url_call("https://example.com", "c3"),
        ]
        executor = AsyncMock()
        executor.execute_batch.return_value = [
            ToolResult(
                tool_name="web_search",
                output="search results",
                success=True,
                metadata={"results": [{"url": "https://s.com"}]},
            ),
            ToolResult(
                tool_name="url_content",
                output="page content",
                success=True,
                metadata={"results": [{"url": "https://example.com"}]},
            ),
        ]

        results = await _execute_tools(calls, {}, executor, citations, {})

        assert len(results) == 3
        # c1: web_search (real execution)
        assert results[0].tool_name == "web_search"
        assert not results[0].metadata.get("synthetic")
        # c2: recall_source (synthetic)
        assert results[1].tool_name == "recall_source"
        assert results[1].metadata.get("synthetic") is True
        assert "Recalled!" in results[1].output
        # c3: url_content (real execution)
        assert results[2].tool_name == "url_content"
        assert not results[2].metadata.get("synthetic")

        # Executor only got the 2 non-recall calls
        executed = executor.execute_batch.call_args[0][0]
        assert len(executed) == 2
        assert executed[0].id == "c1"
        assert executed[1].id == "c3"

    async def test_recall_not_found_still_synthetic(self):
        """A recall_source for a non-existent citation returns a synthetic
        failure result — the model sees available IDs."""
        from moira.workflow.nodes.research import _execute_tools

        citations: list[Citation] = [
            Citation(id="cit001", source="web_search"),
        ]
        call = self._make_recall_call("cit999", "rc1")
        executor = AsyncMock()
        executor.execute_batch.return_value = []

        results = await _execute_tools([call], {}, executor, citations, {"recall_source": 1})

        executor.execute_batch.assert_not_awaited()
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].metadata.get("synthetic") is True
        assert "cit001" in results[0].output


class TestExecuteToolsRecallDedup:
    """Tests for within-batch recall_source dedup.

    Recall content is static within a round, so duplicate recalls of the
    same citation in one batch are intercepted: first call gets the
    stored content, repeats get a short pointer result and don't count
    against per-run/per-step limits. Separate batches (later rounds or
    retry passes with reset context) may re-read the same citation.
    """

    @staticmethod
    def _make_recall_call(citation_id: str, call_id: str = "rc1") -> ToolCall:
        return ToolCall(
            id=call_id,
            name="recall_source",
            arguments={"citation_id": citation_id},
        )

    async def test_same_batch_duplicate_recalled_once(self):
        """Two recalls of cit001 in one batch: first gets content, second
        gets a deduped pointer result."""
        from moira.workflow.nodes.research import _execute_tools

        citations: list[Citation] = [
            Citation(id="cit001", source="url_content", content="Stored body"),
            Citation(id="cit002", source="url_content", content="Other body"),
        ]
        calls = [
            self._make_recall_call("cit001", "rc1"),
            self._make_recall_call("cit001", "rc2"),
        ]
        executor = AsyncMock()

        results = await _execute_tools(calls, {}, executor, citations, {"recall_source": 2})

        assert len(results) == 2
        # First: full content, success
        assert results[0].success is True
        assert "Stored body" in results[0].output
        assert results[0].metadata.get("deduped") is None
        # Second: deduped pointer, failure, no content re-injection
        assert results[1].success is False
        assert "already recalled" in results[1].output
        assert "Stored body" not in results[1].output
        assert results[1].metadata.get("deduped") is True
        assert results[1].metadata.get("synthetic") is True

    async def test_duplicate_recalls_decrement_call_counts(self):
        """Deduped recalls are free against call limits — the count for
        recall_source reflects only the executed (content-bearing) call."""
        from moira.workflow.nodes.research import _execute_tools

        citations: list[Citation] = [
            Citation(id="cit001", source="url_content", content="Stored body"),
        ]
        calls = [
            self._make_recall_call("cit001", "rc1"),
            self._make_recall_call("cit001", "rc2"),
            self._make_recall_call("cit001", "rc3"),
        ]
        executor = AsyncMock()

        # _validate_and_filter_calls would have incremented the count to 3
        call_counts = {"recall_source": 3}
        await _execute_tools(calls, {}, executor, citations, call_counts)

        # Two deduped calls decremented: 3 - 2 = 1
        assert call_counts["recall_source"] == 1

    async def test_distinct_citations_not_deduped(self):
        """Recalls of different citations in one batch all get content."""
        from moira.workflow.nodes.research import _execute_tools

        citations: list[Citation] = [
            Citation(id="cit001", source="url_content", content="Body one"),
            Citation(id="cit002", source="url_content", content="Body two"),
        ]
        calls = [
            self._make_recall_call("cit001", "rc1"),
            self._make_recall_call("cit002", "rc2"),
        ]
        executor = AsyncMock()

        results = await _execute_tools(calls, {}, executor, citations, {"recall_source": 2})

        assert results[0].success is True
        assert "Body one" in results[0].output
        assert results[1].success is True
        assert "Body two" in results[1].output

    async def test_cross_batch_reread_allowed(self):
        """A second _execute_tools batch (later round) may recall the same
        citation again — dedup scope is the batch, not the pass."""
        from moira.workflow.nodes.research import _execute_tools

        citations: list[Citation] = [
            Citation(id="cit001", source="url_content", content="Stored body"),
        ]
        executor = AsyncMock()
        call_counts = {"recall_source": 1}

        # Round 1
        r1 = await _execute_tools(
            [self._make_recall_call("cit001", "rc1")], {}, executor, citations, call_counts
        )
        assert r1[0].success is True
        assert "Stored body" in r1[0].output

        # Round 2 — fresh batch, recall allowed again
        call_counts["recall_source"] = 2
        r2 = await _execute_tools(
            [self._make_recall_call("cit001", "rc2")], {}, executor, citations, call_counts
        )
        assert r2[0].success is True
        assert "Stored body" in r2[0].output
        assert r2[0].metadata.get("deduped") is None


class TestProcessExecutionResultsRecallSource:
    """Tests that _process_execution_results handles recall_source correctly:
    no new citations, no budget charge, but summary + log are produced."""

    def test_no_new_citation_created(self):
        """recall_source results must not create new Citation objects."""
        from moira.workflow.nodes.research import _process_execution_results

        call = ToolCall(
            id="tc1",
            name="recall_source",
            arguments={"citation_id": "cit001"},
        )
        result = ToolResult(
            tool_name="recall_source",
            output="Source: cit001\nPage content:\nExisting content",
            success=True,
            metadata={"synthetic": True},
        )
        citations: list[Citation] = [
            Citation(id="cit001", source="url_content"),
        ]
        seen_urls: dict[str, str] = {}
        facts: list[Fact] = []
        tool_plan: list = []
        tool_results_log: list[dict] = []
        call_counts: dict[str, int] = {}
        tool_costs = {"recall_source": 0.0}

        summaries, budget_after, total_cost = _process_execution_results(
            [result],
            [call],
            lambda _event: None,
            citations,
            seen_urls,
            facts,
            tool_plan,
            tool_results_log,
            call_counts,
            tool_costs,
            100.0,
            0.0,
        )

        # No new citation created — still just the original cit001
        assert len(citations) == 1
        assert citations[0]["id"] == "cit001"
        # Summary produced (model sees the recalled content)
        assert len(summaries) == 1
        assert "Existing content" in summaries[0]
        # No budget charged
        assert budget_after == 100.0
        assert total_cost == 0.0
        # Logged
        assert len(tool_results_log) == 1
        assert tool_results_log[0]["tool"] == "recall_source"

    def test_no_budget_charge_even_with_cost(self):
        """Even if tool_costs has a non-zero value, recall_source is free
        because of the synthetic flag + the early continue."""
        from moira.workflow.nodes.research import _process_execution_results

        call = ToolCall(
            id="tc1",
            name="recall_source",
            arguments={"citation_id": "cit001"},
        )
        result = ToolResult(
            tool_name="recall_source",
            output="content",
            success=True,
            metadata={"synthetic": True},
        )
        citations: list[Citation] = [
            Citation(id="cit001", source="url_content"),
        ]
        # Deliberately set a cost — should NOT be charged
        tool_costs = {"recall_source": 5.0}

        _, budget_after, total_cost = _process_execution_results(
            [result],
            [call],
            lambda _event: None,
            citations,
            {},
            [],
            [],
            [],
            {},
            tool_costs,
            100.0,
            0.0,
        )

        assert budget_after == 100.0
        assert total_cost == 0.0


class TestQueryDupeHelpers:
    """Unit tests for the Phase 4a query similarity helpers."""

    def test_tokenize_strips_stopwords_and_short_runs(self):
        from moira.workflow.nodes.research import _tokenize_query

        tokens = _tokenize_query("The Quick-Brown foxes, and a B12 study")
        # 'the'/'and' are stopwords, 'a' is length-1, 'b12' survives as one run
        assert tokens == {"quick", "brown", "foxes", "b12", "study"}

    def test_normalize_is_order_and_punctuation_insensitive(self):
        from moira.workflow.nodes.research import _normalize_query

        assert _normalize_query("Flamin' Hot Cheetos launch date year Frito-Lay") == (
            _normalize_query("Cheetos hot Flamin' launch Frito Lay date year")
        )

    def test_similarity_identical_sets_scores_one(self):
        from moira.workflow.nodes.research import _query_similarity, _tokenize_query

        score, idx = _query_similarity(
            _tokenize_query("trade policy manufacturing jobs"),
            [_tokenize_query("jobs manufacturing policy trade")],
        )
        assert score == 1.0
        assert idx == 0

    def test_similarity_disjoint_sets_scores_zero(self):
        from moira.workflow.nodes.research import _query_similarity, _tokenize_query

        score, idx = _query_similarity(
            _tokenize_query("jazz trumpeters influence"),
            [_tokenize_query("telescope mount cost drivers")],
        )
        assert score == 0.0
        assert idx == -1

    def test_similarity_no_priors(self):
        from moira.workflow.nodes.research import _query_similarity, _tokenize_query

        assert _query_similarity(_tokenize_query("anything"), []) == (0.0, -1)


class TestWebSearchDupeInterception:
    """Phase 4a guardrail: near-duplicate web_search calls get synthetic
    rejections instead of executing.

    Threshold semantics calibrated against the replayed query history
    (planning-freedom.md Phase 4): word-shuffle dupes with a rare added
    token land ≥0.65 and are rejected; genuine entity/angle changes sit
    well below and pass.
    """

    # Measured 0.748 weighted overlap — same study, '+ intervention', reordered
    BASE_QUERY = "randomized controlled trial acute water drinking blood pressure changes"
    SHUFFLE_DUPE = "acute water drinking intervention randomized controlled trial blood pressure"

    @staticmethod
    def _make_search_call(query: str, call_id: str = "ws1") -> ToolCall:
        return ToolCall(id=call_id, name="web_search", arguments={"query": query})

    @staticmethod
    def _search_executor(results_for: int = 1) -> AsyncMock:
        executor = AsyncMock()
        executor.execute_batch.return_value = [
            ToolResult(tool_name="web_search", output="results here", success=True)
            for _ in range(results_for)
        ]
        return executor

    async def test_word_shuffle_dupe_rejected(self):
        from moira.workflow.nodes.research import _execute_tools

        calls = [
            self._make_search_call(self.BASE_QUERY, "ws1"),
            self._make_search_call(self.SHUFFLE_DUPE, "ws2"),
        ]
        results = await _execute_tools(
            calls, {}, self._search_executor(1), [], {"web_search": 2}, issued_queries=[]
        )

        assert results[0].success is True
        dupe = results[1]
        assert dupe.success is False
        assert dupe.metadata.get("deduped") is True
        assert dupe.metadata.get("synthetic") is True
        assert "duplicate" in dupe.output.lower()
        # The rejected result quotes the prior query so the model can see it
        assert self.BASE_QUERY[:80] in dupe.output

    async def test_exact_normalized_dupes_both_caught(self):
        """Pure reorder in-batch AND exact dupe of the run's first-ever
        query both reject (punctuation/order normalized away)."""
        from moira.workflow.nodes.research import _execute_tools

        q_a = "Flamin' Hot Cheetos launch date year Frito-Lay"
        q_b = "cheetos launch date frito lay year"
        calls = [
            self._make_search_call(q_a, "ws1"),
            self._make_search_call(q_b, "ws2"),
        ]
        results = await _execute_tools(
            calls,
            {},
            self._search_executor(1),
            [],
            {"web_search": 2},
            issued_queries=["flamin hot cheetos launch date year frito lay"],
        )
        assert results[0].success is False  # reorder of seeded prior
        assert results[1].success is False

    async def test_entity_angle_change_passes(self):
        """Swapping the entity and shifting angle scores ~0.21 — far below
        threshold — and executes normally."""
        from moira.workflow.nodes.research import _execute_tools

        priors = [
            "best partner for Kingambit doubles VGC regulation set team",
            "Kingambit teammate suggestions doubles VGC 2024",
            "good partners alongside Kingambit in VGC doubles",
            "competitive doubles team built around Kingambit support",
        ]
        calls = [
            self._make_search_call(
                "best partner for Excadrill sand hole move team building", "ws1"
            )
        ]
        results = await _execute_tools(
            calls,
            {},
            self._search_executor(1),
            [],
            {"web_search": 1},
            issued_queries=list(priors),
        )
        assert results[0].success is True
        assert results[0].metadata.get("deduped") is None

    async def test_in_batch_second_call_scored_against_first_accept(self):
        """Dupes within one fan-out batch are caught sequentially — call 3
        duplicates call 1's query after call 1 was accepted."""
        from moira.workflow.nodes.research import _execute_tools

        calls = [
            self._make_search_call(self.BASE_QUERY, "ws1"),
            self._make_search_call("evaporation cooling drinking water metabolism", "ws2"),
            self._make_search_call(self.BASE_QUERY + " trial design", "ws3"),
        ]
        results = await _execute_tools(
            calls,
            {},
            self._search_executor(2),
            [],
            {"web_search": 3},
            issued_queries=[],
        )
        assert [r.success for r in results] == [True, True, False]

    async def test_rejected_query_not_appended_to_ledger(self):
        """Rejections must not poison future scoring — only accepted
        queries join the run-scoped ledger."""
        from moira.workflow.nodes.research import _execute_tools

        ledger: list[str] = []
        calls = [
            self._make_search_call(self.BASE_QUERY, "ws1"),
            self._make_search_call(self.SHUFFLE_DUPE, "ws2"),
            self._make_search_call("jazz trumpet mutable influence bebop era", "ws3"),
        ]
        await _execute_tools(
            calls,
            {},
            self._search_executor(2),
            [],
            {"web_search": 3},
            issued_queries=ledger,
        )
        assert len(ledger) == 2
        assert self.SHUFFLE_DUPE not in ledger
        assert ledger[0] == self.BASE_QUERY

    async def test_cross_pass_dupe_via_seeded_ledger(self):
        """A retry pass whose queries duplicate a prior pass (seeded via
        execution_state.issued_queries) gets intercepted without executing."""
        from moira.workflow.nodes.research import _execute_tools

        executor = self._search_executor(0)
        ledger = [self.BASE_QUERY]
        calls = [self._make_search_call(f"{self.BASE_QUERY} outcomes", "ws1")]
        results = await _execute_tools(
            calls, {}, executor, [], {"web_search": 1}, issued_queries=ledger
        )
        assert results[0].success is False
        assert results[0].metadata.get("deduped") is True
        executor.execute_batch.assert_not_awaited()

    async def test_rejected_calls_decrement_call_counts(self):
        """Rejected dupes are free against per-run/per-step limits,
        mirroring the url_content/recall dedup decrements."""
        from moira.workflow.nodes.research import _execute_tools

        call_counts = {"web_search": 3}
        calls = [
            self._make_search_call(self.BASE_QUERY, "ws1"),
            self._make_search_call(self.SHUFFLE_DUPE, "ws2"),
            self._make_search_call(self.BASE_QUERY + " trial design", "ws3"),
        ]
        await _execute_tools(
            calls, {}, self._search_executor(1), [], call_counts, issued_queries=[]
        )
        # Only ws1 executed: 3 - 2 rejects = 1
        assert call_counts["web_search"] == 1
