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
