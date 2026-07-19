"""Unit tests for research node internal helper functions and parsers."""

import json

from moira.models.knowledge import Fact
from moira.tools.base import ToolCall


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
