"""Unit tests for research node internal helper functions and parsers."""

import json
from unittest.mock import AsyncMock

from moira.models.knowledge import Citation, Fact
from moira.tools.base import ToolCall, ToolResult


class TestResearchHelpers:
    def test_apply_discovered_facts_preserves_existing_claim(self):
        """An empty claim in a later _apply_discovered_facts call must not
        overwrite a non-empty claim set by an earlier call.

        Regression test: when research is retried after review, the model
        sometimes emits discovered_facts with empty claims for facts it
        already covered. The earlier claim should be preserved.
        """
        from moira.workflow.nodes.research import _apply_discovered_facts

        facts = [
            Fact(
                id="f001",
                subject="test",
                fact_needed="something",
                claim="Original claim from round 1",
                status="unverified",
            ),
        ]

        # Simulate a later round where the model emits an empty claim
        _apply_discovered_facts(
            {"discovered_facts": [{"fact_id": "f001", "claim": ""}]},
            facts,
        )

        assert facts[0]["claim"] == "Original claim from round 1", (
            f"Empty claim should not overwrite existing claim. Got: '{facts[0]['claim']}'"
        )

    def test_apply_discovered_facts_empty_claim_skips_unknown_fact(self):
        """When the model returns an empty claim for an 'unknown' fact, the
        entire update must be skipped — status stays 'unknown', no fields set.

        Without this guard, status would flip to 'unverified' with no claim,
        creating a phantom fact invisible to subsequent research rounds.
        """
        from moira.workflow.nodes.research import _apply_discovered_facts

        facts = [
            Fact(
                id="f001",
                subject="test",
                fact_needed="price of X",
                status="unknown",
            ),
        ]

        _apply_discovered_facts(
            {
                "discovered_facts": [
                    {
                        "fact_id": "f001",
                        "claim": "",
                        "relation": "costs",
                        "value": "$10",
                        "citation_ids": ["cit001"],
                    }
                ]
            },
            facts,
        )

        assert facts[0]["status"] == "unknown"
        assert facts[0].get("claim", "") == ""
        assert "relation" not in facts[0] or facts[0].get("relation") is None
        assert facts[0].get("citation_ids", []) == []

    def test_apply_discovered_facts_whitespace_claim_skips_unknown_fact(self):
        """Whitespace-only claims should be treated the same as empty."""
        from moira.workflow.nodes.research import _apply_discovered_facts

        facts = [
            Fact(
                id="f001",
                subject="test",
                fact_needed="weight of X",
                status="unknown",
            ),
        ]

        _apply_discovered_facts(
            {"discovered_facts": [{"fact_id": "f001", "claim": "   \n\t  "}]},
            facts,
        )

        assert facts[0]["status"] == "unknown"

    def test_cleanup_empty_claims_reverts_unverified_to_unknown(self):
        """_cleanup_empty_claims reverts 'unverified' facts with empty claims
        back to 'unknown' so the gap is visible to research_review."""
        from moira.workflow.nodes.research import _cleanup_empty_claims

        facts = [
            # Auto-promoted by tool execution, model never wrote a claim
            Fact(id="f001", subject="A", fact_needed="x", status="unverified"),
            # Properly researched
            Fact(
                id="f002",
                subject="B",
                fact_needed="y",
                claim="B costs $5",
                status="unverified",
            ),
            # Already unknown — not affected
            Fact(id="f003", subject="C", fact_needed="z", status="unknown"),
            # Whitespace-only claim
            Fact(
                id="f004",
                subject="D",
                fact_needed="w",
                claim="   ",
                status="unverified",
            ),
        ]

        _cleanup_empty_claims(facts)

        assert facts[0]["status"] == "unknown"
        assert facts[1]["status"] == "unverified"
        assert facts[2]["status"] == "unknown"
        assert facts[3]["status"] == "unknown"

    def test_apply_discovered_facts_process_metadata_claims_flow_through(self):
        """Phase 3: process-metadata claims (e.g. 'Insufficient data found')
        are no longer filtered at write time. They flow through as legitimate
        claims so the research_review node can catch them via the 'unknown'
        reviewer result. Filtering at write time required maintaining regex
        patterns for every phrasing variant; the reviewer handles any phrasing.

        This test documents the new contract: such a claim updates the fact
        just like any other non-empty claim would.
        """
        from moira.workflow.nodes.research import _apply_discovered_facts

        facts = [
            Fact(
                id="f001",
                subject="test",
                fact_needed="price of X",
                status="unknown",
            ),
        ]

        _apply_discovered_facts(
            {
                "discovered_facts": [
                    {
                        "fact_id": "f001",
                        "claim": "Insufficient data found",
                        "relation": "costs",
                        "value": "$10",
                        "citation_ids": ["cit001"],
                    }
                ]
            },
            facts,
        )

        # The claim is applied; research_review will catch and revert it.
        assert facts[0]["status"] == "unverified"
        assert facts[0]["claim"] == "Insufficient data found"
        assert facts[0].get("citation_ids") == ["cit001"]

    def test_apply_discovered_facts_overwrites_with_any_nonempty_claim(self):
        """Phase 3: a later round's non-empty claim overwrites an earlier
        claim, even if the new claim is process-metadata. There is no special
        filtering — the earlier claim is replaced. This is the counterpart
        to the empty-claim preservation test above: only EMPTY claims skip
        the update."""
        from moira.workflow.nodes.research import _apply_discovered_facts

        facts = [
            Fact(
                id="f001",
                subject="test",
                fact_needed="something",
                claim="Original claim from round 1",
                status="unverified",
            ),
        ]

        _apply_discovered_facts(
            {"discovered_facts": [{"fact_id": "f001", "claim": "No information available"}]},
            facts,
        )

        # Overwritten — no special filtering for process-metadata phrasings.
        assert facts[0]["claim"] == "No information available"

    def test_try_merge_snippets_suffix_prefix(self):
        """A's suffix overlaps B's prefix → merged into one string."""
        from moira.workflow.nodes.research import _try_merge_snippets

        a = "The recipe uses cherries and sugar for maceration"
        b = "cherries and sugar for maceration over one month"
        result = _try_merge_snippets(a, b)
        assert result == "The recipe uses cherries and sugar for maceration over one month"

    def test_try_merge_snippets_reverse_direction(self):
        """B's suffix overlaps A's prefix → merged (reverse direction)."""
        from moira.workflow.nodes.research import _try_merge_snippets

        a = "cherries and sugar for maceration over one month"
        b = "The recipe uses cherries and sugar for maceration"
        result = _try_merge_snippets(a, b)
        assert result == "The recipe uses cherries and sugar for maceration over one month"

    def test_try_merge_snippets_no_overlap(self):
        """No meaningful overlap → None."""
        from moira.workflow.nodes.research import _try_merge_snippets

        result = _try_merge_snippets(
            "Cherries are harvested in June",
            "Bottling happens in December",
        )
        assert result is None

    def test_try_merge_snippets_substring_is_handled(self):
        """When one is a token-level substring of the other, overlap merge
        finds the full overlap and produces the longer string."""
        from moira.workflow.nodes.research import _try_merge_snippets

        a = "Need cherries sugar brandy"
        b = "Need cherries sugar brandy cinnamon cloves"
        result = _try_merge_snippets(a, b)
        assert result == "Need cherries sugar brandy cinnamon cloves"

    def test_try_merge_snippets_case_insensitive(self):
        """Overlap detection should be case-insensitive."""
        from moira.workflow.nodes.research import _try_merge_snippets

        a = "Add the Sugar and stir well"
        b = "and stir well until dissolved"
        result = _try_merge_snippets(a, b)
        assert result is not None
        assert "dissolved" in result

    def test_try_merge_snippets_min_words_threshold(self):
        """Overlap below min_words threshold should not merge."""
        from moira.workflow.nodes.research import _try_merge_snippets

        # Only 2 words overlap, default min is 3
        result = _try_merge_snippets(
            "the end",
            "the beginning",
            min_words=3,
        )
        assert result is None

    def test_dedup_substring_keeps_longer(self):
        """When new snippet is substring of existing, existing is kept."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}

        # First: long snippet
        _find_or_merge_citation(
            citations,
            seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Need cherries sugar brandy cinnamon cloves",
        )
        # Second: shorter version of same content
        cit_id, is_new = _find_or_merge_citation(
            citations,
            seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Need cherries sugar",
        )
        assert is_new is False
        assert len(citations) == 1
        assert len(citations[0]["snippets"]) == 1
        assert citations[0]["snippets"][0] == "Need cherries sugar brandy cinnamon cloves"

    def test_dedup_existing_substring_replaced_by_longer(self):
        """When existing snippet is substring of new, existing is replaced."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}

        # First: short snippet
        _find_or_merge_citation(
            citations,
            seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Need cherries sugar",
        )
        # Second: longer version
        _find_or_merge_citation(
            citations,
            seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Need cherries sugar brandy cinnamon cloves",
        )
        assert len(citations) == 1
        assert len(citations[0]["snippets"]) == 1
        assert citations[0]["snippets"][0] == "Need cherries sugar brandy cinnamon cloves"

    def test_dedup_overlap_merges_two_snippets(self):
        """Suffix-prefix overlap → two snippets merged into one."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}

        _find_or_merge_citation(
            citations,
            seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="The recipe uses cherries and sugar for maceration",
        )
        _find_or_merge_citation(
            citations,
            seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="cherries and sugar for maceration over one month",
        )
        assert len(citations) == 1
        assert len(citations[0]["snippets"]) == 1
        assert citations[0]["snippets"][0] == (
            "The recipe uses cherries and sugar for maceration over one month"
        )

    def test_dedup_no_overlap_keeps_both(self):
        """Genuinely different snippets for same URL → both kept."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}

        _find_or_merge_citation(
            citations,
            seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Cherries are harvested in June",
        )
        _find_or_merge_citation(
            citations,
            seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Bottling happens in December",
        )
        assert len(citations) == 1
        assert len(citations[0]["snippets"]) == 2


class TestParseToolCalls:
    """Unit tests for _parse_tool_calls — verifies text-based parsing
    returns ToolCall objects with generated IDs."""

    def test_json_array(self):
        from moira.workflow.nodes.research import _parse_tool_calls

        text = json.dumps([{"tool": "web_search", "args": {"query": "x"}}])
        calls = _parse_tool_calls(text)
        assert len(calls) == 1
        assert isinstance(calls[0], ToolCall)
        assert calls[0].name == "web_search"
        assert calls[0].arguments == {"query": "x"}
        assert calls[0].id  # non-empty

    def test_line_delimited_json(self):
        from moira.workflow.nodes.research import _parse_tool_calls

        text = '{"tool": "calc", "args": {"expr": "1+1"}}\n{"tool": "search", "args": {"q": "x"}}'
        calls = _parse_tool_calls(text)
        assert len(calls) == 2
        assert calls[0].name == "calc"
        assert calls[1].name == "search"

    def test_markdown_fenced(self):
        from moira.workflow.nodes.research import _parse_tool_calls

        text = '```json\n[{"tool": "calc", "args": {}}]\n```'
        calls = _parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0].name == "calc"

    def test_empty_text(self):
        from moira.workflow.nodes.research import _parse_tool_calls

        assert _parse_tool_calls("") == []
        assert _parse_tool_calls("no json here") == []

    def test_generated_ids_unique(self):
        from moira.workflow.nodes.research import _parse_tool_calls

        text = json.dumps(
            [
                {"tool": "a", "args": {}},
                {"tool": "b", "args": {}},
            ]
        )
        calls = _parse_tool_calls(text)
        ids = {c.id for c in calls}
        assert len(ids) == 2  # unique


class TestExtractToolCalls:
    """Unit tests for _extract_tool_calls — verifies structured JSON
    object parsing returns ToolCall objects with generated IDs."""

    def test_happy_path(self):
        from moira.workflow.nodes.research import _extract_tool_calls

        parsed = {
            "tool_calls": [
                {"tool": "web_search", "args": {"query": "x"}},
                {"tool": "calc", "args": {"expr": "2+2"}},
            ],
        }
        calls = _extract_tool_calls(parsed)
        assert len(calls) == 2
        assert all(isinstance(c, ToolCall) for c in calls)
        assert calls[0].name == "web_search"
        assert calls[1].name == "calc"
        assert all(c.id for c in calls)

    def test_empty_tool_calls(self):
        from moira.workflow.nodes.research import _extract_tool_calls

        assert _extract_tool_calls({"tool_calls": []}) == []
        assert _extract_tool_calls({}) == []

    def test_tool_calls_not_list(self):
        from moira.workflow.nodes.research import _extract_tool_calls

        assert _extract_tool_calls({"tool_calls": "not a list"}) == []

    def test_extract_tool_calls_skips_missing_name(self):
        from moira.workflow.nodes.research import _extract_tool_calls

        parsed = {"tool_calls": [{"args": {"x": 1}}, {"tool": "ok", "args": {}}]}
        calls = _extract_tool_calls(parsed)
        assert len(calls) == 1
        assert calls[0].name == "ok"


class TestValidateAndFilterCalls:
    """Tests for _validate_and_filter_calls — verifies call limit enforcement,
    allowed-name filtering, and required-param validation."""

    @staticmethod
    def _make_call(name: str, args: dict | None = None) -> ToolCall:
        return ToolCall(id="tc_test", name=name, arguments=args or {})

    def test_batch_does_not_overshoot_limit(self):
        """Multiple calls to the same tool in one batch must not all pass
        when the limit is low.  This is the check-vs-increment regression
        test: previously all calls in a batch saw the same stale count
        and every one passed, allowing overshoot."""
        from moira.workflow.nodes.research import _validate_and_filter_calls

        calls = [self._make_call("web_search", {"query": f"q{i}"}) for i in range(5)]
        call_counts: dict[str, int] = {"web_search": 8}
        valid, rejected = _validate_and_filter_calls(
            calls,
            allowed_names={"web_search"},
            call_limits={"web_search": 10},
            call_counts=call_counts,
            required_params={"web_search": {"query"}},
        )
        # Limit is 10, already used 8 → only 2 more allowed
        assert len(valid) == 2
        assert call_counts["web_search"] == 10

    def test_batch_all_pass_when_under_limit(self):
        """Normal case: all calls pass when well under the limit."""
        from moira.workflow.nodes.research import _validate_and_filter_calls

        calls = [self._make_call("web_search", {"query": f"q{i}"}) for i in range(3)]
        call_counts: dict[str, int] = {}
        valid, rejected = _validate_and_filter_calls(
            calls,
            allowed_names={"web_search"},
            call_limits={"web_search": 10},
            call_counts=call_counts,
            required_params={"web_search": {"query"}},
        )
        assert len(valid) == 3
        assert call_counts["web_search"] == 3

    def test_at_limit_blocks_all(self):
        """When already at the limit, all calls to that tool are blocked."""
        from moira.workflow.nodes.research import _validate_and_filter_calls

        calls = [self._make_call("web_search", {"query": "x"})]
        call_counts: dict[str, int] = {"web_search": 10}
        valid, rejected = _validate_and_filter_calls(
            calls,
            allowed_names={"web_search"},
            call_limits={"web_search": 10},
            call_counts=call_counts,
            required_params={},
        )
        assert len(valid) == 0
        assert call_counts["web_search"] == 10  # unchanged

    def test_mixed_tools_independent_limits(self):
        """Calls to different tools are counted independently within a batch."""
        from moira.workflow.nodes.research import _validate_and_filter_calls

        calls = [
            self._make_call("web_search", {"query": "x"}),
            self._make_call("calculator", {"expression": "1+1"}),
            self._make_call("web_search", {"query": "y"}),
        ]
        call_counts: dict[str, int] = {"web_search": 9, "calculator": 0}
        valid, rejected = _validate_and_filter_calls(
            calls,
            allowed_names={"web_search", "calculator"},
            call_limits={"web_search": 10, "calculator": 5},
            call_counts=call_counts,
            required_params={"web_search": {"query"}, "calculator": {"expression"}},
        )
        assert len(valid) == 2
        assert call_counts["web_search"] == 10
        assert call_counts["calculator"] == 1

    # --- Per-step limit tests ---

    def test_step_limit_blocks_within_step(self):
        """Per-step limit caps calls even when per-run limit has room."""
        from moira.workflow.nodes.research import _validate_and_filter_calls

        calls = [self._make_call("web_search", {"query": f"q{i}"}) for i in range(5)]
        call_counts: dict[str, int] = {"web_search": 0}
        valid, rejected = _validate_and_filter_calls(
            calls,
            allowed_names={"web_search"},
            call_limits={"web_search": 20},  # generous per-run
            call_counts=call_counts,
            required_params={"web_search": {"query"}},
            step_limits={"web_search": 3},
            step_baseline={"web_search": 0},
        )
        assert len(valid) == 3
        assert call_counts["web_search"] == 3

    def test_step_limit_resets_across_steps(self):
        """Per-step usage is relative to baseline, so a fresh step gets
        a fresh budget even if prior steps used calls."""
        from moira.workflow.nodes.research import _validate_and_filter_calls

        # Simulate 2nd research invocation: 4 calls already used,
        # baseline at 4 (those were from the first step).
        calls = [self._make_call("web_search", {"query": f"q{i}"}) for i in range(5)]
        call_counts: dict[str, int] = {"web_search": 4}
        valid, rejected = _validate_and_filter_calls(
            calls,
            allowed_names={"web_search"},
            call_limits={"web_search": 20},
            call_counts=call_counts,
            required_params={"web_search": {"query"}},
            step_limits={"web_search": 3},
            step_baseline={"web_search": 4},  # baseline = counts at step entry
        )
        # step_used = 4 - 4 = 0, so 3 calls allowed this step
        assert len(valid) == 3
        assert call_counts["web_search"] == 7

    def test_step_and_run_limit_both_enforced(self):
        """When per-step is generous but per-run is tight, per-run wins."""
        from moira.workflow.nodes.research import _validate_and_filter_calls

        calls = [self._make_call("web_search", {"query": f"q{i}"}) for i in range(5)]
        call_counts: dict[str, int] = {"web_search": 8}
        valid, rejected = _validate_and_filter_calls(
            calls,
            allowed_names={"web_search"},
            call_limits={"web_search": 10},
            call_counts=call_counts,
            required_params={"web_search": {"query"}},
            step_limits={"web_search": 10},
            step_baseline={"web_search": 0},
        )
        # Per-run limit (10) hits first: only 2 more allowed
        assert len(valid) == 2
        assert call_counts["web_search"] == 10

    def test_step_limit_zero_means_unlimited(self):
        """step_limit of 0 (or absent) means no per-step cap."""
        from moira.workflow.nodes.research import _validate_and_filter_calls

        calls = [self._make_call("web_search", {"query": f"q{i}"}) for i in range(10)]
        call_counts: dict[str, int] = {}
        valid, rejected = _validate_and_filter_calls(
            calls,
            allowed_names={"web_search"},
            call_limits={"web_search": 0},  # unlimited
            call_counts=call_counts,
            required_params={"web_search": {"query"}},
            step_limits={"web_search": 0},  # unlimited
            step_baseline={},
        )
        assert len(valid) == 10


class TestFormatUnknownFacts:
    """Tests for _format_unknown_facts — verifies that all non-verified
    statuses are included so retry passes can see unverified facts."""

    def test_includes_unverified_facts(self):
        """'unverified' facts must appear so the research model can
        re-research facts whose claims didn't match fact_needed."""
        from moira.workflow.nodes.research import _format_unknown_facts

        facts = [
            Fact(id="f001", subject="A", fact_needed="x", status="unverified"),
            Fact(id="f002", subject="B", fact_needed="y", status="unknown"),
            Fact(id="f003", subject="C", fact_needed="z", status="contradicted"),
            Fact(
                id="f004",
                subject="D",
                fact_needed="w",
                status="verified",
                claim="done",
            ),
        ]
        result = _format_unknown_facts(facts)
        assert "f001" in result
        assert "f002" in result
        assert "f003" in result
        assert "f004" not in result

    def test_empty_when_all_verified(self):
        from moira.workflow.nodes.research import _format_unknown_facts

        facts = [
            Fact(id="f001", subject="A", fact_needed="x", status="verified", claim="ok"),
        ]
        result = _format_unknown_facts(facts)
        assert result == ""


class TestRetryContextHelpers:
    """Tests for the shared retry-context formatting helpers."""

    def test_format_established_facts_includes_only_verified_with_claims(self):
        from moira.workflow.nodes._helpers import _format_established_facts

        facts = [
            Fact(
                id="f001",
                subject="Price",
                fact_needed="cost",
                claim="Costs $5",
                status="verified",
                citation_ids=["cit001"],
            ),
            Fact(id="f002", subject="Weight", fact_needed="wt", status="unknown"),
            Fact(
                id="f003",
                subject="Color",
                fact_needed="color",
                claim="",
                status="verified",
            ),
        ]
        result = _format_established_facts(facts)
        assert "f001" in result
        assert "Costs $5" in result
        assert "cit001" in result
        assert "f002" not in result
        assert "f003" not in result

    def test_format_established_facts_empty(self):
        from moira.workflow.nodes._helpers import _format_established_facts

        assert _format_established_facts([]) == ""

    def test_format_prior_conclusions(self):
        from moira.workflow.nodes._helpers import _format_prior_conclusions

        conclusions = [
            {
                "id": "c001",
                "conclusion": "X is true",
                "supporting_fact_ids": ["f001", "f002"],
                "status": "verified",
            },
            {
                "id": "c002",
                "conclusion": "Y is false",
                "supporting_fact_ids": [],
                "status": "contradicted",
            },
        ]
        result = _format_prior_conclusions(conclusions)
        assert "c001" in result
        assert "X is true" in result
        assert "f001" in result
        assert "c002" in result

    def test_format_prior_conclusions_empty(self):
        from moira.workflow.nodes._helpers import _format_prior_conclusions

        assert _format_prior_conclusions([]) == ""

    def test_format_prior_citations(self):
        from moira.workflow.nodes._helpers import _format_prior_citations

        citations = [
            {"id": "cit001", "title": "Source A", "url": "https://a.com"},
            {"id": "cit002", "title": "Source B", "url": "https://b.com"},
        ]
        result = _format_prior_citations(citations)
        assert "cit001" in result
        assert "Source A" in result
        assert "https://a.com" in result
        assert "cit002" in result

    def test_format_prior_citations_empty(self):
        from moira.workflow.nodes._helpers import _format_prior_citations

        assert _format_prior_citations([]) == ""


class TestPartitionUrlContentCalls:
    """Tests for _partition_url_content_calls — the URL dedup gate.

    Verifies that url_content calls on already-fetched URLs are split into
    a synthetic list (skipped execution), while new URLs and non-url_content
    calls pass through to real execution.
    """

    @staticmethod
    def _make_call(name: str, url: str = "", call_id: str = "tc1") -> ToolCall:
        args = {"url": url} if url else {}
        return ToolCall(id=call_id, name=name, arguments=args)

    def test_new_url_passes_through(self):
        """A url_content call on a URL not in fetched_urls executes."""
        from moira.workflow.nodes.research import _partition_url_content_calls

        call = self._make_call("url_content", "https://example.com/new")
        fetched_urls: dict = {}
        citations: list[Citation] = []

        to_execute, synthetics = _partition_url_content_calls([call], fetched_urls, citations)
        assert len(to_execute) == 1
        assert len(synthetics) == 0

    def test_previously_succeeded_is_deduped(self):
        """A url_content call on a URL that previously succeeded is deduped
        when the referenced citation still exists."""
        from moira.workflow.nodes.research import _partition_url_content_calls

        url = "https://example.com/cached"
        call = self._make_call("url_content", url)
        fetched_urls = {url: {"status": "success", "cit_id": "cit001", "error": None}}
        citations: list[Citation] = [
            Citation(id="cit001", source="url_content", url=url, title="Cached"),
        ]

        to_execute, synthetics = _partition_url_content_calls([call], fetched_urls, citations)
        assert len(to_execute) == 0
        assert len(synthetics) == 1
        assert synthetics[0][0] is call

    def test_previously_succeeded_missing_citation_refetches(self):
        """If the citation referenced by fetched_urls is gone, the call
        falls through to real execution (defensive refetch)."""
        from moira.workflow.nodes.research import _partition_url_content_calls

        url = "https://example.com/missing"
        call = self._make_call("url_content", url)
        fetched_urls = {url: {"status": "success", "cit_id": "cit999", "error": None}}
        citations: list[Citation] = []  # cit999 not present

        to_execute, synthetics = _partition_url_content_calls([call], fetched_urls, citations)
        assert len(to_execute) == 1
        assert len(synthetics) == 0

    def test_previously_failed_is_deduped(self):
        """A url_content call on a URL that previously failed is deduped —
        failures are treated as permanent (paywalled/JS-rendered sites will
        fail again)."""
        from moira.workflow.nodes.research import _partition_url_content_calls

        url = "https://example.com/failed"
        call = self._make_call("url_content", url)
        fetched_urls = {url: {"status": "failed", "cit_id": None, "error": "HTTP 403"}}
        citations: list[Citation] = []

        to_execute, synthetics = _partition_url_content_calls([call], fetched_urls, citations)
        assert len(to_execute) == 0
        assert len(synthetics) == 1

    def test_non_url_content_calls_always_execute(self):
        """web_search and other tools are never deduped."""
        from moira.workflow.nodes.research import _partition_url_content_calls

        calls = [
            self._make_call("web_search", call_id="ws1"),
            self._make_call("calculator", call_id="calc1"),
        ]
        # web_search doesn't take a url arg
        calls[0] = ToolCall(id="ws1", name="web_search", arguments={"query": "test"})
        calls[1] = ToolCall(id="calc1", name="calculator", arguments={"expression": "1+1"})
        fetched_urls: dict = {}
        citations: list[Citation] = []

        to_execute, synthetics = _partition_url_content_calls(calls, fetched_urls, citations)
        assert len(to_execute) == 2
        assert len(synthetics) == 0

    def test_mixed_batch_preserves_order(self):
        """A mix of deduped and non-deduped calls preserves original order
        in both output lists."""
        from moira.workflow.nodes.research import _partition_url_content_calls

        cached_url = "https://example.com/cached"
        failed_url = "https://example.com/failed"
        new_url = "https://example.com/new"

        calls = [
            self._make_call("url_content", cached_url, "c1"),
            self._make_call("url_content", new_url, "c2"),
            self._make_call("url_content", failed_url, "c3"),
            self._make_call("web_search", call_id="c4"),
        ]
        calls[3] = ToolCall(id="c4", name="web_search", arguments={"query": "test"})
        fetched_urls = {
            cached_url: {"status": "success", "cit_id": "cit001"},
            failed_url: {"status": "failed", "cit_id": None, "error": "403"},
        }
        citations: list[Citation] = [
            Citation(id="cit001", source="url_content", url=cached_url),
        ]

        to_execute, synthetics = _partition_url_content_calls(calls, fetched_urls, citations)
        # c2 (new url) and c4 (web_search) execute
        assert [c.id for c in to_execute] == ["c2", "c4"]
        # c1 (cached) and c3 (failed) are synthesized, in original order
        assert [c.id for c, _ in synthetics] == ["c1", "c3"]


class TestBuildSyntheticUrlResult:
    """Tests for _build_synthetic_url_result — synthetic ToolResult builder."""

    @staticmethod
    def _make_call(url: str) -> ToolCall:
        return ToolCall(id="tc1", name="url_content", arguments={"url": url})

    def test_success_carries_citation_metadata(self):
        """A synthetic success result carries metadata pointing to the
        existing citation so _process_execution_results marks it as a
        recurring source."""
        from moira.workflow.nodes.research import _build_synthetic_url_result

        url = "https://example.com/article"
        call = self._make_call(url)
        info = {"status": "success", "cit_id": "cit005", "error": None}
        citations: list[Citation] = [
            Citation(
                id="cit005",
                source="url_content",
                url=url,
                title="The Article",
                excerpt="Short snippet here",
                content="Full body content",
            ),
        ]

        result = _build_synthetic_url_result(call, info, citations)
        assert result.success is True
        assert result.metadata.get("synthetic") is True
        results_meta = result.metadata.get("results", [])
        assert len(results_meta) == 1
        assert results_meta[0]["url"] == url
        assert results_meta[0]["title"] == "The Article"
        assert "Full body content" in results_meta[0]["content"]
        assert "cit005" in result.output

    def test_failure_carries_error_message(self):
        """A synthetic failure result surfaces the prior error so the model
        knows why the URL is blocked."""
        from moira.workflow.nodes.research import _build_synthetic_url_result

        url = "https://example.com/paywalled"
        call = self._make_call(url)
        info = {"status": "failed", "cit_id": None, "error": "HTTP 403 Forbidden"}

        result = _build_synthetic_url_result(call, info, [])
        assert result.success is False
        assert result.metadata.get("synthetic") is True
        assert result.metadata.get("results") == []
        assert "HTTP 403 Forbidden" in result.output
        assert result.error  # non-empty error string


class TestUpdateFetchedUrls:
    """Tests for _update_fetched_urls — records url_content outcomes after
    _process_execution_results runs."""

    @staticmethod
    def _make_call(url: str) -> ToolCall:
        return ToolCall(id="tc1", name="url_content", arguments={"url": url})

    def test_records_success_with_cit_id(self):
        """A successful real fetch is recorded with the cit_id resolved from
        seen_urls."""
        from moira.workflow.nodes.research import _update_fetched_urls

        url = "https://example.com/new-success"
        call = self._make_call(url)
        result = ToolResult(
            tool_name="url_content",
            output="URL: ...\n\ncontent",
            success=True,
            metadata={"results": [{"url": url}]},
        )
        fetched_urls: dict = {}
        seen_urls = {url: "cit010"}

        _update_fetched_urls(fetched_urls, [result], [call], seen_urls)
        assert url in fetched_urls
        assert fetched_urls[url]["status"] == "success"
        assert fetched_urls[url]["cit_id"] == "cit010"

    def test_records_failure_with_error(self):
        """A failed real fetch is recorded with the error message."""
        from moira.workflow.nodes.research import _update_fetched_urls

        url = "https://example.com/new-fail"
        call = self._make_call(url)
        result = ToolResult(
            tool_name="url_content",
            output="",
            success=False,
            error="timeout",
            metadata={"results": []},
        )
        fetched_urls: dict = {}
        seen_urls: dict = {}

        _update_fetched_urls(fetched_urls, [result], [call], seen_urls)
        assert url in fetched_urls
        assert fetched_urls[url]["status"] == "failed"
        assert fetched_urls[url]["error"] == "timeout"

    def test_skips_synthetic_results(self):
        """Synthetic results (already tracked in fetched_urls) are skipped."""
        from moira.workflow.nodes.research import _update_fetched_urls

        url = "https://example.com/cached"
        call = self._make_call(url)
        result = ToolResult(
            tool_name="url_content",
            output="cached",
            success=True,
            metadata={"results": [{"url": url}], "synthetic": True},
        )
        fetched_urls = {url: {"status": "success", "cit_id": "cit001", "error": None}}

        _update_fetched_urls(fetched_urls, [result], [call], {})
        # Entry unchanged — synthetic didn't overwrite
        assert fetched_urls[url]["cit_id"] == "cit001"

    def test_skips_non_url_content_calls(self):
        """web_search and other tools are ignored by this tracker."""
        from moira.workflow.nodes.research import _update_fetched_urls

        call = ToolCall(id="ws1", name="web_search", arguments={"query": "x"})
        result = ToolResult(
            tool_name="web_search",
            output="results",
            success=True,
            metadata={"results": [{"url": "https://example.com/s"}]},
        )
        fetched_urls: dict = {}

        _update_fetched_urls(fetched_urls, [result], [call], {})
        assert fetched_urls == {}

    def test_does_not_overwrite_existing_entry(self):
        """If a URL is already in fetched_urls (e.g. pre-populated from
        seen_urls), a real execution result doesn't overwrite it."""
        from moira.workflow.nodes.research import _update_fetched_urls

        url = "https://example.com/preexisting"
        call = self._make_call(url)
        result = ToolResult(
            tool_name="url_content",
            output="content",
            success=False,
            error="should not overwrite",
            metadata={"results": []},
        )
        fetched_urls = {url: {"status": "success", "cit_id": "cit001", "error": None}}

        _update_fetched_urls(fetched_urls, [result], [call], {})
        assert fetched_urls[url]["status"] == "success"


class TestExecuteWithUrlDedup:
    """Integration tests for _execute_with_url_dedup — verifies the full
    partition/execute/merge flow."""

    @staticmethod
    def _make_call(name: str, url: str = "", call_id: str = "tc1") -> ToolCall:
        args = {"url": url} if url else {}
        return ToolCall(id=call_id, name=name, arguments=args)

    async def test_deduped_calls_do_not_execute(self):
        """The executor is only called for non-deduped calls."""
        from moira.workflow.nodes.research import _execute_with_url_dedup

        cached_url = "https://example.com/cached"
        new_url = "https://example.com/new"
        calls = [
            self._make_call("url_content", cached_url, "c1"),
            self._make_call("url_content", new_url, "c2"),
        ]
        fetched_urls = {cached_url: {"status": "success", "cit_id": "cit001", "error": None}}
        citations: list[Citation] = [
            Citation(id="cit001", source="url_content", url=cached_url),
        ]
        executor = AsyncMock()
        executor.execute_batch.return_value = [
            ToolResult(
                tool_name="url_content",
                output="new content",
                success=True,
                metadata={"results": [{"url": new_url}]},
            )
        ]
        call_counts = {"url_content": 2}

        results = await _execute_with_url_dedup(
            calls, fetched_urls, executor, citations, call_counts
        )

        # Only the new URL was executed
        executor.execute_batch.assert_awaited_once()
        executed_calls = executor.execute_batch.call_args[0][0]
        assert len(executed_calls) == 1
        assert executed_calls[0].id == "c2"

        # Two results returned, in original call order
        assert len(results) == 2
        assert results[0].metadata.get("synthetic") is True  # c1 deduped
        assert results[1].metadata.get("synthetic") is not True  # c2 real

    async def test_call_counts_decremented_for_synthetics(self):
        """call_counts is decremented for deduped calls so per-run limits
        reflect only actual executions."""
        from moira.workflow.nodes.research import _execute_with_url_dedup

        cached_url = "https://example.com/cached"
        call = self._make_call("url_content", cached_url)
        fetched_urls = {cached_url: {"status": "success", "cit_id": "cit001", "error": None}}
        citations: list[Citation] = [
            Citation(id="cit001", source="url_content", url=cached_url),
        ]
        executor = AsyncMock()
        # No calls to execute — all deduped
        executor.execute_batch.return_value = []
        call_counts = {"url_content": 5}

        await _execute_with_url_dedup([call], fetched_urls, executor, citations, call_counts)

        # Was 5, _validate incremented to 6 (hypothetically), dedup decrements back
        assert call_counts["url_content"] == 4

    async def test_all_new_urls_execute_normally(self):
        """When no URLs are cached, all calls execute normally."""
        from moira.workflow.nodes.research import _execute_with_url_dedup

        calls = [
            self._make_call("url_content", "https://a.com", "c1"),
            self._make_call("url_content", "https://b.com", "c2"),
        ]
        fetched_urls: dict = {}
        citations: list[Citation] = []
        executor = AsyncMock()
        executor.execute_batch.return_value = [
            ToolResult(
                tool_name="url_content",
                output="a",
                success=True,
                metadata={"results": [{"url": "https://a.com"}]},
            ),
            ToolResult(
                tool_name="url_content",
                output="b",
                success=True,
                metadata={"results": [{"url": "https://b.com"}]},
            ),
        ]
        call_counts = {"url_content": 0}

        results = await _execute_with_url_dedup(
            calls, fetched_urls, executor, citations, call_counts
        )

        assert len(results) == 2
        assert all(not r.metadata.get("synthetic") for r in results)
        assert call_counts["url_content"] == 0  # unchanged — no synthetics

    async def test_results_returned_in_call_order(self):
        """Results are returned in the same order as valid_calls, with
        synthetics filling in for deduped calls."""
        from moira.workflow.nodes.research import _execute_with_url_dedup

        cached = "https://cached.com"
        new = "https://new.com"
        failed = "https://failed.com"
        calls = [
            self._make_call("url_content", cached, "c1"),
            self._make_call("url_content", new, "c2"),
            self._make_call("url_content", failed, "c3"),
        ]
        fetched_urls = {
            cached: {"status": "success", "cit_id": "cit001"},
            failed: {"status": "failed", "error": "403"},
        }
        citations: list[Citation] = [
            Citation(id="cit001", source="url_content", url=cached),
        ]
        executor = AsyncMock()
        executor.execute_batch.return_value = [
            ToolResult(
                tool_name="url_content",
                output="new content",
                success=True,
                metadata={"results": [{"url": new}]},
            ),
        ]
        call_counts = {"url_content": 3}

        results = await _execute_with_url_dedup(
            calls, fetched_urls, executor, citations, call_counts
        )

        assert len(results) == 3
        # c1: synthetic success (cached)
        assert results[0].success is True
        assert results[0].metadata.get("synthetic") is True
        # c2: real execution
        assert results[1].metadata.get("synthetic") is not True
        # c3: synthetic failure (previously failed)
        assert results[2].success is False
        assert results[2].metadata.get("synthetic") is True


class TestProcessExecutionResultsSyntheticCost:
    """Tests that _process_execution_results skips budget charging for
    synthetic (deduped) results."""

    def test_synthetic_result_does_not_charge_budget(self):
        """A synthetic url_content result must not deduct tool cost from
        the budget — the HTTP call was skipped."""
        from moira.workflow.nodes.research import _process_execution_results

        url = "https://example.com/cached"
        call = ToolCall(id="tc1", name="url_content", arguments={"url": url})
        synthetic_result = ToolResult(
            tool_name="url_content",
            output="URL already fetched — see [cit001].",
            success=True,
            metadata={
                "results": [
                    {
                        "url": url,
                        "title": "Cached",
                        "snippet": "snippet",
                        "content": "content",
                    }
                ],
                "synthetic": True,
            },
        )
        citations: list[Citation] = [
            Citation(id="cit001", source="url_content", url=url),
        ]
        seen_urls = {url: "cit001"}
        facts: list[Fact] = []
        tool_plan: list = []
        tool_results_log: list[dict] = []
        call_counts = {"url_content": 0}
        tool_costs = {"url_content": 5.0}

        budget_before = 100.0
        summaries, budget_after, total_cost = _process_execution_results(
            [synthetic_result],
            [call],
            lambda _event: None,  # no-op writer
            citations,
            seen_urls,
            facts,
            tool_plan,
            tool_results_log,
            call_counts,
            tool_costs,
            budget_before,
            0.0,
        )
        # Budget unchanged — synthetic didn't cost anything
        assert budget_after == budget_before
        assert total_cost == 0.0
        # Summary still produced (model sees the dedup feedback)
        assert len(summaries) == 1

    def test_real_result_charges_budget(self):
        """A real url_content result charges the tool cost as before."""
        from moira.workflow.nodes.research import _process_execution_results

        url = "https://example.com/real"
        call = ToolCall(id="tc1", name="url_content", arguments={"url": url})
        real_result = ToolResult(
            tool_name="url_content",
            output="URL: ...\n\nreal content",
            success=True,
            metadata={
                "results": [
                    {
                        "url": url,
                        "title": "Real",
                        "snippet": "snippet",
                        "content": "content",
                    }
                ]
            },
        )
        citations: list[Citation] = []
        seen_urls: dict[str, str] = {}
        facts: list[Fact] = []
        tool_plan: list = []
        tool_results_log: list[dict] = []
        call_counts = {"url_content": 0}
        tool_costs = {"url_content": 5.0}

        budget_before = 100.0
        summaries, budget_after, total_cost = _process_execution_results(
            [real_result],
            [call],
            lambda _event: None,
            citations,
            seen_urls,
            facts,
            tool_plan,
            tool_results_log,
            call_counts,
            tool_costs,
            budget_before,
            0.0,
        )
        assert budget_after == budget_before - 5.0
        assert total_cost == 5.0


class TestSynthesizeCitationTitle:
    """Tests for _synthesize_citation_title — generic title fallback for
    tools that don't provide structured metadata (calculator, date_time,
    etc.)."""

    def test_no_args_uses_tool_name(self):
        from moira.workflow.nodes.research import _synthesize_citation_title

        assert _synthesize_citation_title("date_time", {}) == "date_time"

    def test_single_arg(self):
        from moira.workflow.nodes.research import _synthesize_citation_title

        title = _synthesize_citation_title("calculator", {"expression": "2+2"})
        assert "calculator" in title
        assert "expression=2+2" in title

    def test_multiple_args(self):
        from moira.workflow.nodes.research import _synthesize_citation_title

        title = _synthesize_citation_title("convert", {"from": "USD", "to": "EUR"})
        assert "from=USD" in title
        assert "to=EUR" in title

    def test_filters_sensitive_args(self):
        from moira.workflow.nodes.research import _synthesize_citation_title

        title = _synthesize_citation_title(
            "data_fetch",
            {"api_key": "SECRET", "query": "weather"},
        )
        assert "SECRET" not in title
        assert "api_key" not in title
        assert "query=weather" in title

    def test_truncates_long_arg_values(self):
        from moira.workflow.nodes.research import _synthesize_citation_title

        long_val = "x" * 200
        title = _synthesize_citation_title("search", {"q": long_val})
        assert len(title) < 200  # truncated
        assert "x" in title  # still present, just truncated


class TestPathBTitleSynthesis:
    """Tests that _process_execution_results Path B creates citations with
    synthesized titles for tools without structured metadata."""

    def test_calculator_citation_has_title(self):
        """A calculator result (no metadata["results"]) must produce a
        citation with a synthesized title like 'calculator(expression=2+2)'."""
        from moira.workflow.nodes.research import _process_execution_results

        call = ToolCall(id="tc1", name="calculator", arguments={"expression": "2+2"})
        result = ToolResult(
            tool_name="calculator",
            output="4",
            success=True,
        )
        citations: list[Citation] = []
        facts: list[Fact] = []
        tool_plan: list = []
        tool_results_log: list[dict] = []

        summaries, _, _ = _process_execution_results(
            [result],
            [call],
            lambda _event: None,
            citations,
            {},
            facts,
            tool_plan,
            tool_results_log,
            {},
            {"calculator": 0.1},
            100.0,
            0.0,
        )

        assert len(citations) == 1
        assert citations[0]["title"] == "calculator(expression=2+2)"

    def test_no_args_tool_gets_bare_name_title(self):
        """A tool with no arguments gets just the tool name as title."""
        from moira.workflow.nodes.research import _process_execution_results

        call = ToolCall(id="tc1", name="date_time", arguments={})
        result = ToolResult(
            tool_name="date_time",
            output="2024-01-01T00:00:00Z",
            success=True,
        )
        citations: list[Citation] = []

        _process_execution_results(
            [result],
            [call],
            lambda _event: None,
            citations,
            {},
            [],
            [],
            [],
            {},
            {},
            100.0,
            0.0,
        )

        assert len(citations) == 1
        assert citations[0]["title"] == "date_time"


class TestPathAContentFeedback:
    """Tests that _process_execution_results Path A feeds the full content
    field (not just snippet) to the model for fetch tools.

    This is the core information-flow fix: url_content returns the page body
    in metadata["results"][0]["content"], but the model previously only saw
    the 500-char snippet. Now any structured result with a "content" field
    gets that content in the tool feedback, labeled "Content:" instead of
    "Snippet:".
    """

    @staticmethod
    def _run(results, calls):
        """Helper: run _process_execution_results and return summaries."""
        from moira.workflow.nodes.research import _process_execution_results

        citations: list[Citation] = []
        summaries, _, _ = _process_execution_results(
            results,
            calls,
            lambda _event: None,
            citations,
            {},
            [],
            [],
            [],
            {},
            {},
            100.0,
            0.0,
        )
        return summaries, citations

    def test_url_content_shows_content_label(self):
        """url_content result with a content field must produce a summary
        labeled 'Content:' containing the full body text, not a 500-char
        snippet."""
        call = ToolCall(
            id="tc1",
            name="url_content",
            arguments={"url": "https://example.com/article"},
        )
        full_body = "Body text " * 1000  # ~10k chars, well over snippet size
        result = ToolResult(
            tool_name="url_content",
            output=full_body,
            success=True,
            metadata={
                "results": [
                    {
                        "url": "https://example.com/article",
                        "title": "The Article",
                        "snippet": "Short snippet.",
                        "content": full_body,
                    }
                ]
            },
        )

        summaries, _ = self._run([result], [call])

        assert len(summaries) == 1
        assert "Content:" in summaries[0]
        assert "Snippet:" not in summaries[0]
        assert full_body in summaries[0]
        assert "Short snippet." not in summaries[0].split("Content:")[1]

    def test_web_search_shows_snippet_label(self):
        """web_search results have no content field — must still use
        'Snippet:' label with the snippet text (unchanged behavior)."""
        call = ToolCall(
            id="tc1",
            name="web_search",
            arguments={"query": "pokemon types"},
        )
        result = ToolResult(
            tool_name="web_search",
            output="search results",
            success=True,
            metadata={
                "results": [
                    {
                        "title": "Pokemon Types",
                        "url": "https://example.com/pokemon",
                        "snippet": "Fire is super effective against Grass.",
                    }
                ]
            },
        )

        summaries, _ = self._run([result], [call])

        assert len(summaries) == 1
        assert "Snippet:" in summaries[0]
        assert "Content:" not in summaries[0]
        assert "Fire is super effective against Grass." in summaries[0]

    def test_synthetic_dedup_shows_content(self):
        """A synthetic url_content dedup result (already-fetched URL)
        carries content from the stored citation — the model must see that
        content, not just the snippet."""
        full_body = "Cached body " * 800
        call = ToolCall(
            id="tc1",
            name="url_content",
            arguments={"url": "https://example.com/cached"},
        )
        result = ToolResult(
            tool_name="url_content",
            output="URL already fetched in this session — see [cit001].",
            success=True,
            duration_ms=0,
            metadata={
                "results": [
                    {
                        "url": "https://example.com/cached",
                        "title": "Cached Page",
                        "snippet": "Cached snippet.",
                        "content": full_body,
                    }
                ],
                "synthetic": True,
            },
        )

        summaries, _ = self._run([result], [call])

        assert len(summaries) == 1
        assert "Content:" in summaries[0]
        assert full_body in summaries[0]

    def test_rest_tool_shows_content_label(self):
        """RESTTool results provide a content field — must use 'Content:'
        label so the model sees the API response body."""
        api_body = '{"type": "rock", "damage_relations": {"double_damage_to": ["fire"]}}'
        call = ToolCall(
            id="tc1",
            name="pokeapi__type_retrieve",
            arguments={"id": "rock"},
        )
        result = ToolResult(
            tool_name="pokeapi__type_retrieve",
            output=api_body,
            success=True,
            metadata={
                "results": [
                    {
                        "url": "https://pokeapi.co/api/v2/type/rock",
                        "title": "pokeapi type retrieve(id=rock)",
                        "snippet": api_body[:500],
                        "content": api_body,
                    }
                ]
            },
        )

        summaries, _ = self._run([result], [call])

        assert len(summaries) == 1
        assert "Content:" in summaries[0]
        assert "double_damage_to" in summaries[0]

    def test_empty_content_falls_back_to_snippet(self):
        """If content field is present but empty/falsy, fall back to
        snippet with 'Snippet:' label. Prevents showing an empty body
        when a fetch returned no content."""
        call = ToolCall(
            id="tc1",
            name="url_content",
            arguments={"url": "https://example.com/empty"},
        )
        result = ToolResult(
            tool_name="url_content",
            output="",
            success=True,
            metadata={
                "results": [
                    {
                        "url": "https://example.com/empty",
                        "title": "Empty Page",
                        "snippet": "A short excerpt.",
                        "content": "",
                    }
                ]
            },
        )

        summaries, _ = self._run([result], [call])

        assert len(summaries) == 1
        assert "Snippet:" in summaries[0]
        assert "Content:" not in summaries[0]
        assert "A short excerpt." in summaries[0]
