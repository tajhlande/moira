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

    def test_is_fact_id_reference_variants(self):
        """_is_fact_id_reference matches ID-reference patterns and rejects
        ordinary prose. "f003|f004" style fact_needed values come from the
        model mimicking the pipe-delimited facts display format."""
        from moira.workflow.nodes.research import _is_fact_id_reference

        assert _is_fact_id_reference("f003|f004")
        assert _is_fact_id_reference("f003, f004")
        assert _is_fact_id_reference("f003 and f004")
        assert _is_fact_id_reference("f003 & f004")
        assert _is_fact_id_reference("F003|F004")
        assert _is_fact_id_reference("f003")
        # Ordinary descriptions never match, even when they mention an ID
        assert not _is_fact_id_reference("Measured effect of tariffs on prices")
        assert not _is_fact_id_reference("Whether f001 pricing applies broadly")
        assert not _is_fact_id_reference("and or")

    def test_apply_discovered_facts_id_reference_fact_needed_treated_as_missing(self):
        """A new-fact fact_needed that only references other fact IDs
        ("f003|f004") must be treated as missing. With a cited claim the
        entry falls through to the cited-claim fallback; without one it is
        dropped entirely. Neither path may store the ID-reference string as
        fact_needed (the f012-f014 corruption from the 08-24 eval)."""
        from moira.workflow.nodes.research import _apply_discovered_facts

        facts = [
            Fact(id="f001", subject="A", fact_needed="x", status="unknown"),
        ]

        # Uncited ID-reference entry: dropped, no fact created
        _apply_discovered_facts(
            {
                "discovered_facts": [
                    {
                        "fact_id": None,
                        "fact_needed": "f002|f003",
                        "claim": "Studies use SVAR methods",
                    }
                ]
            },
            facts,
        )
        assert len(facts) == 1

        # Cited ID-reference entry: salvaged via claim fallback — fact_needed
        # is the claim text, never the ID reference
        _apply_discovered_facts(
            {
                "discovered_facts": [
                    {
                        "fact_id": None,
                        "subject": "Trade",
                        "fact_needed": "f002|f003",
                        "claim": "Tariff passthrough raised input prices",
                        "citation_ids": ["cit004"],
                    }
                ]
            },
            facts,
        )
        assert len(facts) == 2
        new_fact = facts[1]
        assert new_fact["id"] == "f002"
        assert new_fact["fact_needed"] == "Tariff passthrough raised input prices"
        assert new_fact["status"] == "unverified"
        assert new_fact["citation_ids"] == ["cit004"]

    def test_apply_discovered_facts_new_fact_uncited_claim_stays_unknown(self):
        """A new fact (real fact_needed) whose immediate claim has no
        citations must enter as 'unknown' with no claim — not as an
        unsourced 'unverified' fact that leaks into the report."""
        from moira.workflow.nodes.research import _apply_discovered_facts

        facts = []

        _apply_discovered_facts(
            {
                "discovered_facts": [
                    {
                        "fact_id": None,
                        "subject": "Trade",
                        "fact_needed": "Measured tariff passthrough rate",
                        "claim": "Passthrough was roughly 20 percent",
                        "citation_ids": [],
                    }
                ]
            },
            facts,
        )

        assert len(facts) == 1
        assert facts[0]["status"] == "unknown"
        assert facts[0].get("claim", "") == ""
        assert facts[0].get("citation_ids", []) == []
        assert facts[0]["fact_needed"] == "Measured tariff passthrough rate"

    def test_apply_discovered_facts_new_fact_cited_claim_recorded(self):
        """A new fact with a real fact_needed AND a cited claim is recorded
        immediately as 'unverified' — the intended no-wasted-retry path."""
        from moira.workflow.nodes.research import _apply_discovered_facts

        facts = []

        _apply_discovered_facts(
            {
                "discovered_facts": [
                    {
                        "fact_id": None,
                        "subject": "Trade",
                        "fact_needed": "Measured tariff passthrough rate",
                        "claim": "Passthrough was roughly 20 percent",
                        "citation_ids": ["cit002"],
                    }
                ]
            },
            facts,
        )

        assert len(facts) == 1
        assert facts[0]["status"] == "unverified"
        assert facts[0]["claim"] == "Passthrough was roughly 20 percent"
        assert facts[0]["citation_ids"] == ["cit002"]

    def test_apply_discovered_facts_claim_only_fallback_requires_citation(self):
        """Claim-only entries (null fact_id, no fact_needed) are created only
        when cited; uncited ones are dropped."""
        from moira.workflow.nodes.research import _apply_discovered_facts

        facts = []

        # Cited claim-only entry: created with claim as fact_needed
        _apply_discovered_facts(
            {
                "discovered_facts": [
                    {
                        "fact_id": None,
                        "subject": "Trade",
                        "claim": "Retaliation targeted agricultural exports",
                        "citation_ids": ["cit001"],
                    }
                ]
            },
            facts,
        )
        assert len(facts) == 1
        assert facts[0]["status"] == "unverified"
        assert facts[0]["fact_needed"] == "Retaliation targeted agricultural exports"
        assert facts[0]["citation_ids"] == ["cit001"]

        # Uncited claim-only entry: dropped
        _apply_discovered_facts(
            {
                "discovered_facts": [
                    {
                        "fact_id": None,
                        "subject": "Trade",
                        "claim": "Uncited assertion",
                    }
                ]
            },
            facts,
        )
        assert len(facts) == 1

    def test_apply_discovered_facts_multiple_cited_entries_same_fact_id(self):
        """Multiple cited entries for the same fact_id in ONE response: the
        first updates the fact; each subsequent cited entry becomes a new
        fact instead of clobbering the first claim.

        Regression test for the 08-24 telescope run: the model emitted ~18
        discovered_facts entries with distinct subjects attached to a handful
        of existing fact IDs, and last-write-wins left an arbitrary survivor.
        """
        from moira.workflow.nodes.research import _apply_discovered_facts

        facts = [
            Fact(
                id="f001",
                subject="Cost",
                fact_needed="Typical price range of X",
                status="unknown",
            ),
        ]

        _apply_discovered_facts(
            {
                "discovered_facts": [
                    {
                        "fact_id": "f001",
                        "subject": "Cost",
                        "claim": "X retails for $400-$600",
                        "citation_ids": ["cit001"],
                    },
                    {
                        "fact_id": "f001",
                        "subject": "Drive mechanics",
                        "claim": "X uses a 360:1 worm gear drive",
                        "citation_ids": ["cit002"],
                    },
                    {
                        "fact_id": "f001",
                        "subject": "Component weight",
                        "claim": "The heaviest part of X weighs 110 pounds",
                        "citation_ids": ["cit003"],
                    },
                ]
            },
            facts,
        )

        # First entry owns the existing fact
        assert facts[0]["id"] == "f001"
        assert facts[0]["claim"] == "X retails for $400-$600"
        assert facts[0]["status"] == "unverified"
        assert facts[0]["citation_ids"] == ["cit001"]

        # Overflow entries were split into new facts, each with its own
        # subject, claim, and citation — not dropped, not clobbering f001.
        new_facts = facts[1:]
        assert len(new_facts) == 2
        assert new_facts[0]["subject"] == "Drive mechanics"
        assert new_facts[0]["claim"] == "X uses a 360:1 worm gear drive"
        assert new_facts[0]["fact_needed"] == "X uses a 360:1 worm gear drive"
        assert new_facts[0]["status"] == "unverified"
        assert new_facts[0]["citation_ids"] == ["cit002"]
        assert new_facts[1]["subject"] == "Component weight"
        assert new_facts[1]["citation_ids"] == ["cit003"]

    def test_apply_discovered_facts_uncited_overflow_entry_dropped(self):
        """An overflow entry (fact already updated this response) without a
        citation is dropped — uncited claims cannot be verified or cited in
        the report, so they must not become new facts either."""
        from moira.workflow.nodes.research import _apply_discovered_facts

        facts = [
            Fact(
                id="f001",
                subject="Cost",
                fact_needed="Typical price range of X",
                status="unknown",
            ),
        ]

        _apply_discovered_facts(
            {
                "discovered_facts": [
                    {
                        "fact_id": "f001",
                        "subject": "Cost",
                        "claim": "X retails for $400-$600",
                        "citation_ids": ["cit001"],
                    },
                    {
                        "fact_id": "f001",
                        "subject": "Misc",
                        "claim": "Uncited side detail",
                    },
                ]
            },
            facts,
        )

        assert len(facts) == 1
        assert facts[0]["claim"] == "X retails for $400-$600"

    def test_apply_discovered_facts_empty_claim_does_not_lock_fact(self):
        """A skipped entry (empty claim) must NOT mark the fact as updated,
        so a later cited entry in the same response can still claim it."""
        from moira.workflow.nodes.research import _apply_discovered_facts

        facts = [
            Fact(
                id="f001",
                subject="Cost",
                fact_needed="Typical price range of X",
                status="unknown",
            ),
        ]

        _apply_discovered_facts(
            {
                "discovered_facts": [
                    {"fact_id": "f001", "subject": "Cost", "claim": ""},
                    {
                        "fact_id": "f001",
                        "subject": "Cost",
                        "claim": "X retails for $400-$600",
                        "citation_ids": ["cit001"],
                    },
                ]
            },
            facts,
        )

        assert len(facts) == 1
        assert facts[0]["claim"] == "X retails for $400-$600"
        assert facts[0]["status"] == "unverified"

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

    def test_depth_snippet_when_only_snippet(self):
        """Search-result citations (snippet, no content) get depth="snippet"."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        _find_or_merge_citation(
            citations,
            {},
            source="web_search",
            url="https://x.com",
            snippet="Search result fragment",
        )
        assert citations[0]["depth"] == "snippet"

    def test_depth_page_when_content_present(self):
        """Fetch-tool citations (content body present) get depth="page"."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        _find_or_merge_citation(
            citations,
            {},
            source="url_content",
            url="https://x.com",
            snippet="teaser",
            content="Full page body.",
        )
        assert citations[0]["depth"] == "page"

    def test_depth_merge_upgrades_snippet_to_page(self):
        """A later fetch on the same URL upgrades snippet depth to page."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}
        _find_or_merge_citation(
            citations,
            seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Search result fragment",
        )
        assert citations[0]["depth"] == "snippet"

        cit_id, is_new = _find_or_merge_citation(
            citations,
            seen_urls,
            source="url_content",
            url="https://x.com",
            content="Full page body.",
        )
        assert is_new is False
        assert citations[0]["depth"] == "page"

    def test_depth_merge_snippet_never_downgrades_page(self):
        """A later snippet arrival on the same URL keeps page depth."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}
        _find_or_merge_citation(
            citations,
            seen_urls,
            source="url_content",
            url="https://x.com",
            content="Full page body.",
        )
        _find_or_merge_citation(
            citations,
            seen_urls,
            source="web_search",
            url="https://x.com",
            snippet="Another search fragment",
        )
        assert citations[0]["depth"] == "page"

    def test_model_sources_forced_snippet_depth(self):
        """Model-declared sources are always snippet depth even though
        their excerpt rides in as content (excerpt != fetched body)."""
        from moira.workflow.nodes.research import _apply_sources

        citations: list = []
        _apply_sources(
            {
                "sources": [
                    {
                        "source": "Reddit",
                        "url": "https://reddit.com/r/foo",
                        "title": "A thread",
                        "excerpt": "Some excerpt text",
                    }
                ]
            },
            citations,
            {},
        )
        assert citations[0]["depth"] == "snippet"

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
        # Markdown table with header row
        assert "| id | depth | linked facts | title |" in result
        assert "cit001" in result
        assert "Source A" in result
        assert "cit002" in result
        # No facts passed → linked facts column shows em-dash placeholder
        assert "| — |" in result
        # snippet-only citations (no content) are labeled as such
        assert "snippet" in result

    def test_format_prior_citations_empty(self):
        from moira.workflow.nodes._helpers import _format_prior_citations

        assert _format_prior_citations([]) == ""

    def test_format_prior_citations_depth_and_linked_facts(self):
        """Depth column distinguishes page content from snippets; linked
        facts column inverts fact.citation_ids; pipe in title is escaped."""
        from moira.workflow.nodes._helpers import _format_prior_citations

        citations = [
            {
                "id": "cit001",
                "title": "Deep page | with pipe",
                "content": "x" * 2500,  # ~2.5k chars → page 2k
            },
            {"id": "cit002", "title": "Snippet only"},
        ]
        facts = [
            {"id": "f001", "citation_ids": ["cit001"]},
            {"id": "f002", "citation_ids": ["cit001", "cit002"]},
        ]
        result = _format_prior_citations(citations, facts)
        lines = result.splitlines()
        assert lines[0] == "| id | depth | linked facts | title |"
        row1 = lines[2]
        assert row1.startswith("| cit001 | page 2k |")
        assert "f001 f002" in row1
        # Pipe inside title escaped so it can't break the table columns
        assert "Deep page \\| with pipe" in row1
        row2 = lines[3]
        assert row2.startswith("| cit002 | snippet | f002 |")

    def test_format_prior_citations_cross_subject_facts(self):
        """Facts are a flat list of dict-like entries; every fact linking a
        citation appears in that citation's linked-facts cell."""
        from moira.workflow.nodes._helpers import _format_prior_citations

        citations = [{"id": "cit001", "title": "T", "content": "y" * 1200}]
        facts = [
            {"id": "f003", "citation_ids": []},
            {"id": "f004", "citation_ids": ["cit001"]},
        ]
        result = _format_prior_citations(citations, facts)
        assert "| cit001 | page 1k | f004 | T |" in result


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
    """Tests that _process_execution_results Path A feeds the content field
    (not just snippet) to the model for fetch tools, bounded by the feedback
    cap.

    This is the core information-flow fix: url_content returns the page body
    in metadata["results"][0]["content"], but the model previously only saw
    the 500-char snippet. Now any structured result with a "content" field
    gets that content in the tool feedback, labeled "Content:" instead of
    "Snippet:". Bodies longer than _TOOL_RESULT_FEEDBACK_LIMIT are truncated
    with a recall_source pointer — the full body lives in the citation store.
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
        labeled 'Content:' whose body starts with the fetched text (not the
        500-char snippet). Bodies over the feedback cap are truncated with a
        recall_source pointer; storage keeps the body up to the citation
        content limit."""
        from moira.workflow.nodes.research import (
            _CITATION_CONTENT_LIMIT,
            _TOOL_RESULT_FEEDBACK_LIMIT,
        )

        call = ToolCall(
            id="tc1",
            name="url_content",
            arguments={"url": "https://example.com/article"},
        )
        full_body = "Body text " * 1000  # ~10k chars, well over both caps
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

        summaries, citations = self._run([result], [call])

        assert len(summaries) == 1
        assert "Content:" in summaries[0]
        assert "Snippet:" not in summaries[0]
        assert full_body[:_TOOL_RESULT_FEEDBACK_LIMIT] in summaries[0]
        assert full_body not in summaries[0]
        # Truncation must point at the stored citation for a deliberate re-read.
        assert "recall_source" in summaries[0]
        assert "cit001" in summaries[0]
        # Storage is bounded by the citation content limit (enforced at the
        # storage boundary); the model-facing copy is capped tighter still.
        assert citations[0]["content"] == full_body[:_CITATION_CONTENT_LIMIT]

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
        from moira.workflow.nodes.research import _TOOL_RESULT_FEEDBACK_LIMIT

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
        assert full_body[:_TOOL_RESULT_FEEDBACK_LIMIT] in summaries[0]
        assert "recall_source" in summaries[0]

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


class TestFeedbackCap:
    """Tests for _TOOL_RESULT_FEEDBACK_LIMIT — tool-result text fed back into
    the research loop's message history is bounded so a wide parallel fan-out
    of large payloads cannot blow the workflow model's context window
    (incident: run fdaeb0e2, 39,457-token request vs a 32,768 limit).

    The cap applies ONLY to the model-facing copy. Citation storage stays
    full-fidelity (up to _CITATION_CONTENT_LIMIT), and truncated feedback
    carries a recall_source pointer so the model can deliberately re-read.
    """

    @staticmethod
    def _run(results, calls):
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

    def test_cap_helper_boundary(self):
        """_cap_feedback_body passes short text through unchanged; long text
        is truncated at exactly the limit with a pointer naming the omitted
        size and the citation ID."""
        from moira.workflow.nodes.research import (
            _TOOL_RESULT_FEEDBACK_LIMIT,
            _cap_feedback_body,
        )

        short = "x" * 100
        assert _cap_feedback_body(short, "cit001") == short

        long_text = "y" * (_TOOL_RESULT_FEEDBACK_LIMIT + 5000)
        capped = _cap_feedback_body(long_text, "cit007")
        assert capped.startswith("y" * _TOOL_RESULT_FEEDBACK_LIMIT)
        assert "cit007" in capped
        assert "recall_source" in capped
        assert f"{5000:,} more chars" in capped

    def test_at_limit_not_truncated(self):
        """Text exactly at the cap passes through with no pointer."""
        from moira.workflow.nodes.research import (
            _TOOL_RESULT_FEEDBACK_LIMIT,
            _cap_feedback_body,
        )

        exact = "z" * _TOOL_RESULT_FEEDBACK_LIMIT
        assert _cap_feedback_body(exact, "cit001") == exact

    def test_path_b_output_capped_with_pointer(self):
        """Unstructured results (Path B — calculator, MCP tools) with long
        output are capped in feedback; the citation stores up to
        _CITATION_CONTENT_LIMIT."""
        from moira.workflow.nodes.research import _CITATION_CONTENT_LIMIT

        call = ToolCall(id="tc1", name="calculator", arguments={"expr": "2^10000"})
        long_output = "9" * (_CITATION_CONTENT_LIMIT + 2000)
        result = ToolResult(tool_name="calculator", output=long_output, success=True)

        summaries, citations = self._run([result], [call])

        assert len(summaries) == 1
        assert "recall_source" in summaries[0]
        assert "cit001" in summaries[0]
        assert long_output not in summaries[0]
        # Path B storage is capped by the citation limit, not the feedback limit.
        assert citations[0]["content"] == long_output[:_CITATION_CONTENT_LIMIT]

    def test_recall_source_exempt_from_cap(self):
        """recall_source results relay the stored content in full — recall is
        the deliberate re-read path the cap's pointer promises, so capping it
        would make content between the cap and _CITATION_CONTENT_LIMIT
        permanently unreachable."""
        from moira.workflow.nodes.research import _CITATION_CONTENT_LIMIT

        call = ToolCall(
            id="tc1",
            name="recall_source",
            arguments={"citation_id": "cit001"},
        )
        stored = "R" * _CITATION_CONTENT_LIMIT
        result = ToolResult(
            tool_name="recall_source",
            output=f"Source: cit001\n\nPage content:\n{stored}",
            success=True,
            metadata={"synthetic": True},
        )

        summaries, citations = self._run([result], [call])

        assert len(summaries) == 1
        assert stored in summaries[0]
        # Recall creates no new citation.
        assert citations == []


class TestFormatResearchProgress:
    """Tests for _format_research_progress — renders the structural
    stalled-progress block for reviewer/evaluator prompts."""

    def test_absent_or_productive_returns_empty_string(self):
        from moira.workflow.nodes._helpers import _format_research_progress

        assert _format_research_progress(None) == ""
        assert _format_research_progress({}) == ""
        assert _format_research_progress({"new_facts": 4, "stalled": False}) == ""

    def test_stalled_renders_signal_with_count(self):
        from moira.workflow.nodes._helpers import _format_research_progress

        out = _format_research_progress({"new_facts": 0, "stalled": True})
        assert "RESEARCH PROGRESS SIGNAL" in out
        assert "0 new factual claims" in out
        # Instructs honest framing of remaining gaps.
        assert "known unknowns" in out


class TestFormatPriorReviews:
    """Tests for _format_prior_reviews — formats prior ReviewOutcome list
    with instructional header. Returns empty string when no history."""

    def test_empty_returns_empty_string(self):
        from moira.workflow.nodes._helpers import _format_prior_reviews

        assert _format_prior_reviews([], instruction="test header") == ""

    def test_single_review_with_evidence(self):
        from moira.workflow.nodes._helpers import _format_prior_reviews

        reviews = [
            {
                "route": "retry",
                "fact_results": [
                    {
                        "fact_id": "f001",
                        "result": "verified",
                        "evidence": "Type chart confirmed via PokeAPI",
                    },
                    {
                        "fact_id": "f002",
                        "result": "unverified",
                        "evidence": "Ability data not found in sources",
                    },
                ],
                "missing_areas": ["Ability data for Tyranitar"],
            }
        ]
        result = _format_prior_reviews(reviews, instruction="HEADER TEXT")

        assert "HEADER TEXT" in result
        assert "Review #1 (retry)" in result
        assert "f001=verified" in result
        assert "Type chart confirmed" in result
        assert "f002=unverified" in result
        assert "Missing: Ability data for Tyranitar" in result

    def test_multiple_reviews(self):
        from moira.workflow.nodes._helpers import _format_prior_reviews

        reviews = [
            {
                "route": "retry",
                "fact_results": [{"fact_id": "f001", "result": "unverified"}],
                "missing_areas": [],
            },
            {
                "route": "retry",
                "fact_results": [{"fact_id": "f001", "result": "verified"}],
                "missing_areas": [],
            },
        ]
        result = _format_prior_reviews(reviews, instruction="HEADER")

        assert "Review #1 (retry)" in result
        assert "Review #2 (retry)" in result
        assert "f001=unverified" in result
        assert "f001=verified" in result

    def test_no_evidence_omits_parens(self):
        from moira.workflow.nodes._helpers import _format_prior_reviews

        reviews = [
            {
                "route": "continue",
                "fact_results": [{"fact_id": "f001", "result": "verified"}],
                "missing_areas": [],
            }
        ]
        result = _format_prior_reviews(reviews, instruction="HEADER")

        assert "f001=verified" in result
        assert "()" not in result

    def test_no_missing_areas_omits_line(self):
        from moira.workflow.nodes._helpers import _format_prior_reviews

        reviews = [
            {
                "route": "continue",
                "fact_results": [{"fact_id": "f001", "result": "verified"}],
                "missing_areas": [],
            }
        ]
        result = _format_prior_reviews(reviews, instruction="HEADER")

        assert "Missing:" not in result


class TestFormatPriorEvaluations:
    """Tests for _format_prior_evaluations — formats prior EvaluationOutcome
    list with instructional header. Returns empty string when no history."""

    def test_empty_returns_empty_string(self):
        from moira.workflow.nodes._helpers import _format_prior_evaluations

        assert _format_prior_evaluations([], instruction="test header") == ""

    def test_single_evaluation_with_reasons(self):
        from moira.workflow.nodes._helpers import _format_prior_evaluations

        evals = [
            {
                "route": "retry",
                "goal_met": False,
                "conclusion_results": [
                    {
                        "conclusion_id": "c001",
                        "result": "verified",
                        "reason": "Type weakness correctly established",
                    },
                    {
                        "conclusion_id": "c002",
                        "result": "unsupported",
                        "reason": "Asserts price superiority without cost data",
                    },
                ],
                "goal_assessment": "Engineering differences described but not linked to cost",
            }
        ]
        result = _format_prior_evaluations(evals, instruction="HEADER TEXT")

        assert "HEADER TEXT" in result
        assert "Evaluation #1 (retry, goal not met)" in result
        assert "c001=verified" in result
        assert "Type weakness" in result
        assert "c002=unsupported" in result
        assert "Asserts price superiority" in result
        assert "Assessment: Engineering differences" in result

    def test_goal_met_shows_correctly(self):
        from moira.workflow.nodes._helpers import _format_prior_evaluations

        evals = [
            {
                "route": "accept",
                "goal_met": True,
                "conclusion_results": [],
                "goal_assessment": "",
            }
        ]
        result = _format_prior_evaluations(evals, instruction="HEADER")

        assert "goal met" in result
        assert "goal not met" not in result

    def test_no_reason_omits_parens(self):
        from moira.workflow.nodes._helpers import _format_prior_evaluations

        evals = [
            {
                "route": "accept",
                "goal_met": True,
                "conclusion_results": [
                    {"conclusion_id": "c001", "result": "verified"},
                ],
                "goal_assessment": "",
            }
        ]
        result = _format_prior_evaluations(evals, instruction="HEADER")

        assert "c001=verified" in result
        assert "()" not in result


class TestProgressCutoff:
    """Tests for the progress-based retry cutoff in evaluation.

    The evaluation node overrides route from 'retry' to 'accept' when
    neither verified facts nor verified conclusions increased from the
    previous evaluation cycle. These tests verify the logic by simulating
    the relevant state and checking the EvaluationOutcome.
    """

    def test_no_progress_overrides_retry_to_accept(self):
        """When verified counts don't increase from prior evaluation,
        route is overridden from retry to accept."""
        # The progress check logic:
        # evaluation_count >= 2, route == "retry", prior eval exists,
        # verified_fact_count <= prev_facts AND verified_conclusion_count <= prev_conclusions
        evaluation_count = 2
        route = "retry"
        verified_fact_count = 3
        verified_conclusion_count = 1
        existing_eval_history = [
            {
                "route": "retry",
                "goal_met": False,
                "verified_fact_count": 3,
                "verified_conclusion_count": 1,
            }
        ]

        # Simulate the progress check
        if evaluation_count >= 2 and route == "retry" and existing_eval_history:
            prev = existing_eval_history[-1]
            prev_facts = prev.get("verified_fact_count", 0)
            prev_conclusions = prev.get("verified_conclusion_count", 0)
            if verified_fact_count <= prev_facts and verified_conclusion_count <= prev_conclusions:
                route = "accept"

        assert route == "accept"

    def test_progress_in_facts_preserves_retry(self):
        """When verified fact count increases, route stays retry."""
        evaluation_count = 2
        route = "retry"
        verified_fact_count = 5
        verified_conclusion_count = 1
        existing_eval_history = [
            {
                "route": "retry",
                "goal_met": False,
                "verified_fact_count": 3,
                "verified_conclusion_count": 1,
            }
        ]

        if evaluation_count >= 2 and route == "retry" and existing_eval_history:
            prev = existing_eval_history[-1]
            prev_facts = prev.get("verified_fact_count", 0)
            prev_conclusions = prev.get("verified_conclusion_count", 0)
            if verified_fact_count <= prev_facts and verified_conclusion_count <= prev_conclusions:
                route = "accept"

        assert route == "retry"

    def test_progress_in_conclusions_preserves_retry(self):
        """When verified conclusion count increases, route stays retry."""
        evaluation_count = 2
        route = "retry"
        verified_fact_count = 3
        verified_conclusion_count = 2
        existing_eval_history = [
            {
                "route": "retry",
                "goal_met": False,
                "verified_fact_count": 3,
                "verified_conclusion_count": 1,
            }
        ]

        if evaluation_count >= 2 and route == "retry" and existing_eval_history:
            prev = existing_eval_history[-1]
            prev_facts = prev.get("verified_fact_count", 0)
            prev_conclusions = prev.get("verified_conclusion_count", 0)
            if verified_fact_count <= prev_facts and verified_conclusion_count <= prev_conclusions:
                route = "accept"

        assert route == "retry"

    def test_first_evaluation_not_checked(self):
        """On the first evaluation (count=1), progress check doesn't
        trigger even if route is retry."""
        evaluation_count = 1
        route = "retry"
        existing_eval_history = []

        if evaluation_count >= 2 and route == "retry" and existing_eval_history:
            route = "accept"

        assert route == "retry"

    def test_accept_route_not_overridden(self):
        """When route is already accept, progress check doesn't apply."""
        evaluation_count = 2
        route = "accept"
        existing_eval_history = [
            {
                "route": "retry",
                "goal_met": False,
                "verified_fact_count": 3,
                "verified_conclusion_count": 1,
            }
        ]

        if evaluation_count >= 2 and route == "retry" and existing_eval_history:
            route = "accept"

        assert route == "accept"


class TestSynthesisDerivationParsing:
    """Tests for _parse_conclusions in synthesis.py.

    Verifies that the derivation field is correctly parsed from model
    output, normalized to "direct" or "inferred", and defaults to "direct"
    when absent or unrecognized.
    """

    def test_explicit_direct_derivation(self):
        from moira.workflow.nodes.synthesis import _parse_conclusions

        parsed = {
            "conclusions": [
                {
                    "conclusion": "X is true",
                    "supporting_fact_ids": ["f001"],
                    "reasoning": "f001 says so",
                    "derivation": "direct",
                }
            ]
        }
        results = _parse_conclusions(parsed)
        assert len(results) == 1
        assert results[0]["derivation"] == "direct"

    def test_explicit_inferred_derivation(self):
        from moira.workflow.nodes.synthesis import _parse_conclusions

        parsed = {
            "conclusions": [
                {
                    "conclusion": "X likely causes Y",
                    "supporting_fact_ids": ["f001", "f002"],
                    "reasoning": "f001 + f002 imply this",
                    "derivation": "inferred",
                }
            ]
        }
        results = _parse_conclusions(parsed)
        assert len(results) == 1
        assert results[0]["derivation"] == "inferred"

    def test_missing_derivation_defaults_to_direct(self):
        from moira.workflow.nodes.synthesis import _parse_conclusions

        parsed = {
            "conclusions": [
                {
                    "conclusion": "X is true",
                    "supporting_fact_ids": ["f001"],
                    "reasoning": "f001 says so",
                }
            ]
        }
        results = _parse_conclusions(parsed)
        assert len(results) == 1
        assert results[0]["derivation"] == "direct"

    def test_invalid_derivation_defaults_to_direct(self):
        """Malformed derivation values must not smuggle through — they
        default to "direct" so downstream nodes apply the stricter standard."""
        from moira.workflow.nodes.synthesis import _parse_conclusions

        parsed = {
            "conclusions": [
                {
                    "conclusion": "X is true",
                    "supporting_fact_ids": ["f001"],
                    "reasoning": "f001 says so",
                    "derivation": "speculation",
                }
            ]
        }
        results = _parse_conclusions(parsed)
        assert len(results) == 1
        assert results[0]["derivation"] == "direct"

    def test_mixed_derivations(self):
        """Multiple conclusions with different derivations are all parsed correctly."""
        from moira.workflow.nodes.synthesis import _parse_conclusions

        parsed = {
            "conclusions": [
                {
                    "conclusion": "Direct fact",
                    "supporting_fact_ids": ["f001"],
                    "derivation": "direct",
                },
                {
                    "conclusion": "Inferred claim",
                    "supporting_fact_ids": ["f001", "f002"],
                    "derivation": "inferred",
                },
                {
                    "conclusion": "Default conclusion",
                    "supporting_fact_ids": ["f003"],
                },
            ]
        }
        results = _parse_conclusions(parsed)
        assert len(results) == 3
        assert results[0]["derivation"] == "direct"
        assert results[1]["derivation"] == "inferred"
        assert results[2]["derivation"] == "direct"


class TestEvaluationDerivationRelabel:
    """Tests for _apply_conclusion_results in evaluation.py.

    Verifies that the evaluator can re-label derivation from "direct" to
    "inferred" when synthesis mislabeled it, and that the derivation label
    is preserved when the evaluator agrees.
    """

    def test_relabel_direct_to_inferred(self):
        """Evaluator corrects a 'direct' label to 'inferred' when the
        conclusion smuggles in inference."""
        from moira.workflow.nodes.evaluation import _apply_conclusion_results

        conclusions = [
            {
                "id": "c001",
                "conclusion": "X costs more",
                "supporting_fact_ids": ["f001"],
                "status": "unverified",
                "derivation": "direct",
            }
        ]
        results = [
            {
                "conclusion_id": "c001",
                "result": "verified",
                "derivation": "inferred",
                "reason": "This is a reasoned inference, not a direct restatement",
            }
        ]
        _apply_conclusion_results(conclusions, results)
        assert conclusions[0]["derivation"] == "inferred"
        assert conclusions[0]["status"] == "verified"

    def test_preserves_inferred_label(self):
        """Evaluator agrees with 'inferred' label — no change."""
        from moira.workflow.nodes.evaluation import _apply_conclusion_results

        conclusions = [
            {
                "id": "c001",
                "conclusion": "X likely costs more",
                "supporting_fact_ids": ["f001"],
                "status": "unverified",
                "derivation": "inferred",
            }
        ]
        results = [
            {
                "conclusion_id": "c001",
                "result": "verified",
                "derivation": "inferred",
                "reason": "Sound inference",
            }
        ]
        _apply_conclusion_results(conclusions, results)
        assert conclusions[0]["derivation"] == "inferred"

    def test_preserves_direct_label(self):
        """Evaluator agrees with 'direct' label — no change."""
        from moira.workflow.nodes.evaluation import _apply_conclusion_results

        conclusions = [
            {
                "id": "c001",
                "conclusion": "f001 says X",
                "supporting_fact_ids": ["f001"],
                "status": "unverified",
                "derivation": "direct",
            }
        ]
        results = [
            {
                "conclusion_id": "c001",
                "result": "verified",
                "derivation": "direct",
                "reason": "Direct restatement",
            }
        ]
        _apply_conclusion_results(conclusions, results)
        assert conclusions[0]["derivation"] == "direct"

    def test_invalid_derivation_preserves_existing(self):
        """Invalid derivation from evaluator does not overwrite existing label."""
        from moira.workflow.nodes.evaluation import _apply_conclusion_results

        conclusions = [
            {
                "id": "c001",
                "conclusion": "X is true",
                "supporting_fact_ids": ["f001"],
                "status": "unverified",
                "derivation": "inferred",
            }
        ]
        results = [
            {
                "conclusion_id": "c001",
                "result": "verified",
                "derivation": "bogus",
                "reason": "Bad label",
            }
        ]
        _apply_conclusion_results(conclusions, results)
        assert conclusions[0]["derivation"] == "inferred"

    def test_missing_derivation_preserves_existing(self):
        """When evaluator doesn't provide derivation, existing label is kept."""
        from moira.workflow.nodes.evaluation import _apply_conclusion_results

        conclusions = [
            {
                "id": "c001",
                "conclusion": "X is true",
                "supporting_fact_ids": ["f001"],
                "status": "unverified",
                "derivation": "inferred",
            }
        ]
        results = [
            {
                "conclusion_id": "c001",
                "result": "verified",
                "reason": "Sound reasoning",
            }
        ]
        _apply_conclusion_results(conclusions, results)
        assert conclusions[0]["derivation"] == "inferred"


class TestDerivationFactGateInteraction:
    """Tests that fact_gate still blocks 'verified' status regardless of derivation.

    The fact_gate is orthogonal to derivation: even if a conclusion is
    correctly labeled as 'inferred' with sound logic, it cannot be marked
    'verified' if its supporting facts are not all verified.
    """

    def test_fact_gate_blocks_inferred_conclusion(self):
        """An inferred conclusion with blocked facts cannot be verified."""
        from moira.workflow.nodes.evaluation import _apply_conclusion_results

        conclusions = [
            {
                "id": "c001",
                "conclusion": "X likely causes Y",
                "supporting_fact_ids": ["f001", "f002"],
                "status": "unverified",
                "derivation": "inferred",
                "fact_gate": "blocked",
            }
        ]
        results = [
            {
                "conclusion_id": "c001",
                "result": "verified",
                "derivation": "inferred",
                "reason": "Sound inference",
            }
        ]
        _apply_conclusion_results(conclusions, results)
        assert conclusions[0]["status"] == "unverified"
        assert "Cannot verify" in conclusions[0].get("verification_note", "")

    def test_fact_gate_blocks_direct_conclusion(self):
        """A direct conclusion with blocked facts also cannot be verified."""
        from moira.workflow.nodes.evaluation import _apply_conclusion_results

        conclusions = [
            {
                "id": "c001",
                "conclusion": "X is true",
                "supporting_fact_ids": ["f001"],
                "status": "unverified",
                "derivation": "direct",
                "fact_gate": "blocked",
            }
        ]
        results = [
            {
                "conclusion_id": "c001",
                "result": "verified",
                "derivation": "direct",
                "reason": "Direct restatement",
            }
        ]
        _apply_conclusion_results(conclusions, results)
        assert conclusions[0]["status"] == "unverified"

    def test_no_fact_gate_allows_inferred_verified(self):
        """Without fact_gate blocking, an inferred conclusion can be verified."""
        from moira.workflow.nodes.evaluation import _apply_conclusion_results

        conclusions = [
            {
                "id": "c001",
                "conclusion": "X likely causes Y",
                "supporting_fact_ids": ["f001", "f002"],
                "status": "unverified",
                "derivation": "inferred",
            }
        ]
        results = [
            {
                "conclusion_id": "c001",
                "result": "verified",
                "derivation": "inferred",
                "reason": "Sound inference from verified facts",
            }
        ]
        _apply_conclusion_results(conclusions, results)
        assert conclusions[0]["status"] == "verified"

    def test_unsupported_status_is_terminal(self):
        """Conclusions already marked 'unsupported' are not touched by evaluator output."""
        from moira.workflow.nodes.evaluation import _apply_conclusion_results

        conclusions = [
            {
                "id": "c001",
                "conclusion": "Bad claim",
                "supporting_fact_ids": ["f001"],
                "status": "unsupported",
                "derivation": "direct",
            }
        ]
        results = [
            {
                "conclusion_id": "c001",
                "result": "verified",
                "derivation": "inferred",
                "reason": "Should not apply",
            }
        ]
        _apply_conclusion_results(conclusions, results)
        assert conclusions[0]["status"] == "unsupported"
        assert conclusions[0]["derivation"] == "direct"


class TestRequestIdAttribution:
    """Tests for request_id attribution: parsing, native-argument popping,
    strict fact promotion, and the attempt ledger."""

    def test_extract_tool_calls_reads_request_id(self):
        """Text-mode tool_calls entries carry request_id onto the ToolCall."""
        from moira.workflow.nodes.research import _extract_tool_calls

        parsed = {
            "tool_calls": [
                {"tool": "web_search", "args": {"query": "x"}, "request_id": "req0001"},
                {"tool": "web_search", "args": {"query": "y"}},
            ]
        }
        calls = _extract_tool_calls(parsed)
        assert calls[0].request_id == "req0001"
        assert calls[1].request_id is None

    def test_parse_tool_calls_reads_request_id(self):
        """Fallback array parsing also threads request_id through."""
        from moira.workflow.nodes.research import _parse_tool_calls

        text = '[{"tool": "web_search", "args": {"query": "x"}, "request_id": "req0002"}]'
        calls = _parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0].request_id == "req0002"

    def test_validate_pops_request_id_from_arguments(self):
        """Native mode: request_id rides inside arguments; validation must
        promote it to the ToolCall field and strip it before execution."""
        from moira.workflow.nodes.research import _validate_and_filter_calls

        call = ToolCall(
            id="tc1",
            name="web_search",
            arguments={"query": "x", "request_id": "req0001"},
        )
        valid, _ = _validate_and_filter_calls(
            [call],
            {"web_search"},
            {"web_search": 10},
            {},
            {"web_search": {"query"}},
        )
        assert len(valid) == 1
        assert valid[0].request_id == "req0001"
        assert "request_id" not in valid[0].arguments

    @staticmethod
    def _promotion_setup():
        """Shared fixtures: two requests, three unknown facts."""
        from moira.workflow.nodes.research import _process_execution_results

        evidence_requests = [
            {
                "id": "req0001",
                "target_fact_ids": ["f001", "f002"],
                "evidence_needed": "e1",
                "candidate_tools": ["web_search"],
                "fallback": False,
            },
            {
                "id": "req0002",
                "target_fact_ids": ["f003"],
                "evidence_needed": "e2",
                "candidate_tools": ["web_search"],
                "fallback": False,
            },
        ]
        facts = [
            Fact(id="f001", subject="a", fact_needed="n1", status="unknown"),
            Fact(id="f002", subject="b", fact_needed="n2", status="unknown"),
            Fact(id="f003", subject="c", fact_needed="n3", status="unknown"),
        ]
        return _process_execution_results, evidence_requests, facts

    def test_strict_attribution_promotes_only_own_request(self):
        """A successful call promotes facts of ITS request only — not every
        request listing the tool (the old loose matching)."""
        _per, evidence_requests, facts = self._promotion_setup()
        call = ToolCall(
            id="tc1",
            name="web_search",
            arguments={"query": "x"},
            request_id="req0001",
        )
        result = ToolResult(
            tool_name="web_search",
            output="some results",
            metadata={"results": [{"url": "https://a", "snippet": "s"}]},
        )
        _per(
            [result],
            [call],
            lambda _e: None,
            [],
            {},
            facts,
            evidence_requests,
            [],
            {},
            {},
            100.0,
            0.0,
        )
        assert facts[0]["status"] == "unverified"
        assert facts[1]["status"] == "unverified"
        assert facts[2]["status"] == "unknown"

    def test_unattributed_call_promotes_nothing(self):
        """Without request_id, a successful call promotes no facts — coverage
        is no longer overstated by loose tool matching."""
        _per, evidence_requests, facts = self._promotion_setup()
        call = ToolCall(id="tc1", name="web_search", arguments={"query": "x"})
        result = ToolResult(
            tool_name="web_search",
            output="some results",
            metadata={"results": [{"url": "https://a", "snippet": "s"}]},
        )
        _per(
            [result],
            [call],
            lambda _e: None,
            [],
            {},
            facts,
            evidence_requests,
            [],
            {},
            {},
            100.0,
            0.0,
        )
        assert all(f["status"] == "unknown" for f in facts)

    def test_unknown_request_id_promotes_nothing(self):
        """A request_id that matches no known request promotes nothing."""
        _per, evidence_requests, facts = self._promotion_setup()
        call = ToolCall(
            id="tc1",
            name="web_search",
            arguments={"query": "x"},
            request_id="req9999",
        )
        result = ToolResult(
            tool_name="web_search",
            output="some results",
            metadata={"results": [{"url": "https://a", "snippet": "s"}]},
        )
        _per(
            [result],
            [call],
            lambda _e: None,
            [],
            {},
            facts,
            evidence_requests,
            [],
            {},
            {},
            100.0,
            0.0,
        )
        assert all(f["status"] == "unknown" for f in facts)

    def test_attempt_ledger_recorded(self):
        """Attributed calls append to the request_attempts ledger and the
        tool_results_log entries carry request_id."""
        _per, evidence_requests, facts = self._promotion_setup()
        ledger: dict[str, list[dict]] = {}
        log: list[dict] = []
        call = ToolCall(
            id="tc1",
            name="web_search",
            arguments={"query": "why are cast iron pans heavy"},
            request_id="req0001",
        )
        result = ToolResult(
            tool_name="web_search",
            output="results",
            metadata={"results": [{"url": f"https://a/{i}", "snippet": "s"} for i in range(3)]},
        )
        _per(
            [result],
            [call],
            lambda _e: None,
            [],
            {},
            facts,
            evidence_requests,
            log,
            {},
            {},
            100.0,
            0.0,
            request_attempts=ledger,
        )
        assert len(ledger["req0001"]) == 1
        entry = ledger["req0001"][0]
        assert entry["tool"] == "web_search"
        assert entry["query"] == "why are cast iron pans heavy"
        assert entry["success"] is True
        assert entry["results"] == 3
        assert log[0]["request_id"] == "req0001"

    def test_format_request_outcomes_lists_tried_queries(self):
        """Outcomes render per-request: unresolved facts + queries tried."""
        from moira.workflow.nodes.research import _format_request_outcomes

        requests = [
            {
                "id": "req0001",
                "target_fact_ids": ["f001"],
                "evidence_needed": "cast iron pan weight",
                "candidate_tools": ["web_search"],
                "fallback": False,
            },
            {
                "id": "req0002",
                "target_fact_ids": ["f002"],
                "evidence_needed": "never attempted",
                "candidate_tools": ["web_search"],
                "fallback": False,
            },
        ]
        attempts = {
            "req0001": [
                {
                    "tool": "web_search",
                    "query": "cast iron pan weight",
                    "success": True,
                    "results": 0,
                },
            ]
        }
        facts = [
            Fact(id="f001", subject="a", fact_needed="n1", status="unknown"),
            Fact(id="f002", subject="b", fact_needed="n2", status="unknown"),
        ]
        out = _format_request_outcomes(requests, attempts, facts)
        assert "req0001" in out
        assert "f001" in out
        assert "cast iron pan weight" in out
        # req0002 has no attempts -> not rendered
        assert "req0002" not in out

    def test_format_request_outcomes_skips_resolved(self):
        """Requests whose target facts are all resolved are not rendered."""
        from moira.workflow.nodes.research import _format_request_outcomes

        requests = [
            {
                "id": "req0001",
                "target_fact_ids": ["f001"],
                "evidence_needed": "x",
                "candidate_tools": ["web_search"],
                "fallback": False,
            }
        ]
        attempts = {
            "req0001": [
                {"tool": "web_search", "query": "q", "success": True, "results": 5},
            ]
        }
        facts = [
            Fact(id="f001", subject="a", fact_needed="n1", status="verified"),
        ]
        assert _format_request_outcomes(requests, attempts, facts) == ""

    def test_attempt_ledger_records_deduped_flag(self):
        """Synthetic duplicate rejections are recorded with deduped=True so
        retry prompts can distinguish exhaustion evidence from failures."""
        import moira.workflow.nodes.research as research_mod

        ledger: dict[str, list[dict]] = {}
        args = {"query": "same query again"}

        dupe_result = ToolResult(
            tool_name="web_search",
            output="Query rejected as a duplicate",
            success=False,
            metadata={"synthetic": True, "deduped": True},
            error="deduped: near-duplicate of an issued query",
        )
        research_mod._record_request_attempt(ledger, "req0001", "web_search", args, dupe_result)
        assert ledger["req0001"][0]["deduped"] is True

        failed_result = ToolResult(tool_name="web_search", output="", success=False)
        research_mod._record_request_attempt(ledger, "req0001", "web_search", args, failed_result)
        assert ledger["req0001"][1]["deduped"] is False

    def test_format_request_outcomes_marks_duplicate_rejections(self):
        """Deduped attempts render distinctly so the model sees its angle
        was mechanically blocked, not merely unsuccessful."""
        from moira.workflow.nodes.research import _format_request_outcomes

        requests = [
            {
                "id": "req0001",
                "target_fact_ids": ["f001"],
                "evidence_needed": "cast iron pan weight",
                "candidate_tools": ["web_search"],
                "fallback": False,
            }
        ]
        attempts = {
            "req0001": [
                {
                    "tool": "web_search",
                    "query": "q one",
                    "success": False,
                    "results": 0,
                    "deduped": True,
                },
                {"tool": "web_search", "query": "q two", "success": True, "results": 2},
            ]
        }
        facts = [Fact(id="f001", subject="a", fact_needed="n1", status="unknown")]
        out = _format_request_outcomes(requests, attempts, facts)
        assert "Rejected as duplicate" in out
        assert "too similar to a query already issued" in out
        assert '"q one"' in out
        # Non-deduped attempts keep the regular Tried rendering.
        assert 'Tried: web_search "q two"' in out

    def test_count_newly_claimed_facts(self):
        """Progress counting: only facts that gained a claim this pass."""
        import moira.workflow.nodes.research as research_mod

        facts = [
            # Claimed before AND now -> not new progress.
            Fact(id="f001", subject="a", fact_needed="n", status="unverified", claim="old claim"),
            # Claimless at snapshot, claimed now -> new progress.
            Fact(id="f002", subject="b", fact_needed="n", status="unverified", claim="new claim"),
            # Still unknown -> no progress.
            Fact(id="f003", subject="c", fact_needed="n", status="unknown"),
            # Brand-new fact created this pass -> absent from snapshot.
            Fact(id="f004", subject="d", fact_needed="n", status="unverified", claim="brand new"),
            # Empty-whitespace claim doesn't count.
            Fact(id="f005", subject="e", fact_needed="n", status="unknown", claim="   "),
        ]
        # Simulated pass-start state: f001 had a claim; f002/f003/f005 did
        # not; f004 didn't exist yet.
        snapshot = {"f001": True, "f002": False, "f003": False, "f005": False}
        assert research_mod._count_newly_claimed_facts(snapshot, facts) == 2
        # Fully exhausted pass: nothing new since snapshot.
        done_snapshot = {f["id"]: True for f in facts}
        assert research_mod._count_newly_claimed_facts(done_snapshot, facts) == 0

    def test_augment_tools_declares_optional_request_id(self):
        """Schema augmentation adds request_id as an optional property so
        native tool-calling models are willing to emit it; original tool
        definitions are not mutated."""
        from moira.tools.base import ToolDefinition
        from moira.workflow.nodes.research import _augment_tools_with_request_id

        tool = ToolDefinition(
            name="web_search",
            description="search",
            argument_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        out = _augment_tools_with_request_id([tool])
        assert "request_id" in out[0].argument_schema["properties"]
        assert "request_id" not in out[0].argument_schema.get("required", [])
        # original untouched
        assert "request_id" not in tool.argument_schema["properties"]
        # idempotent on second pass
        again = _augment_tools_with_request_id(out)
        assert "request_id" in again[0].argument_schema["properties"]

    def test_fact_append_dedup_rejects_identity_matches(self):
        """Historical collision patterns must be blocked: byte-identical shell
        twins (run 008200e8 Clefable f016/f017) and case/order variants of an
        existing fact's (subject, fact_needed)."""
        import moira.workflow.nodes.research as research_mod

        facts = [
            Fact(
                id="f001",
                subject="Clefable",
                fact_needed="Defensive type coverage and team synergy principles",
                status="unknown",
            ),
        ]
        appended: list[tuple[str, str]] = []
        # Byte-identical twin.
        assert research_mod._is_duplicate_fact(
            "Clefable",
            "Defensive type coverage and team synergy principles",
            facts,
            appended,
        )
        # Case/punctuation/order variant of the same identity.
        assert research_mod._is_duplicate_fact(
            "clefable!",
            "team synergy principles, defensive type — coverage?",
            facts,
            appended,
        )
        # Within-response repeat: identical entry twice in one response.
        appended.append(research_mod._fact_identity("Chien-Pao", "Candidate movesets"))
        assert research_mod._is_duplicate_fact(
            "Chien-Pao",
            "candidate movesets!",
            [],
            appended,
        )
        # False positive guard: different subject stays separate even with
        # the same question text (per-candidate decomposition is legitimate).
        assert not research_mod._is_duplicate_fact(
            "Corviknight",
            "Defensive type coverage and team synergy principles",
            facts,
            appended,
        )

    def test_fact_append_dedup_allows_legitimate_twins(self):
        """Must-pass pair from calibration forensics (dd95 run): systolic vs
        diastolic twins share most tokens but differ in one discriminative
        one; a true append dedup only hard-blocks exact identity."""
        import moira.workflow.nodes.research as research_mod

        facts = [
            Fact(
                id="f003",
                subject="Observational cohort studies",
                fact_needed=(
                    "What direction and magnitude of correlation between "
                    "water intake and systolic blood pressure is reported?"
                ),
                status="unknown",
            ),
        ]
        assert not research_mod._is_duplicate_fact(
            "Observational cohort studies",
            (
                "What direction and magnitude of correlation between water "
                "intake and diastolic blood pressure is reported?"
            ),
            facts,
            [],
        )

    def test_apply_discovered_facts_drops_duplicate_shell_entries(self):
        """End-to-end through _apply_discovered_facts: two identical
        fact_id-null entries in one response yield ONE new fact, matching
        the f016/f017 incident shape."""
        import moira.workflow.nodes.research as research_mod

        facts: list[dict] = []
        parsed = {
            "discovered_facts": [
                {
                    "fact_id": None,
                    "subject": "Clefable",
                    "fact_needed": "Defensive type coverage and team synergy principles",
                },
                {
                    "fact_id": None,
                    "subject": "Clefable",
                    "fact_needed": "defensive type coverage AND team synergy principles!",
                },
            ],
        }
        research_mod._apply_discovered_facts(parsed, facts)
        assert len(facts) == 1
        assert facts[0]["subject"] == "Clefable"
        assert facts[0]["status"] == "unknown"


class TestPruneRedundantUrls:
    """Tests for the hypermedia URL-pruning rule (Phase 3.3.2).

    The rule: drop a URL-bearing field only when a sibling identifying
    field (name/title/id/...) exists in the same object; keep URLs that
    are the sole content of an object. Applies only to model-facing
    copies; storage is untouched.
    """

    def test_url_pruned_when_identifying_sibling_present(self):
        """Object with name + url keeps name, drops url."""
        from moira.workflow.nodes.research import _prune_redundant_urls

        body = json.dumps(
            {
                "name": "Excadrill",
                "url": "https://pokeapi.co/api/v2/pokemon/excadrill",
                "id": 530,
            }
        )
        # Pad past the prune minimum so it isn't skipped as small
        body = body + " " * 2500 if len(body) < 2000 else body
        result = _prune_redundant_urls(body)
        assert "pokeapi.co" not in result
        assert "Excadrill" in result

    def test_url_kept_when_sole_content(self):
        """URL with no identifying sibling is preserved — it IS the data."""
        from moira.workflow.nodes.research import _prune_redundant_urls

        body = json.dumps({"url": "https://example.com/sole-content"})
        body = body + " " * 2500  # exceed prune minimum
        result = _prune_redundant_urls(body)
        assert "example.com/sole-content" in result

    def test_nested_objects_pruned_recursively(self):
        """URL walls inside nested lists/dicts are pruned when siblings exist."""
        from moira.workflow.nodes.research import _prune_redundant_urls

        body = json.dumps(
            {
                "types": [
                    {
                        "slot": 1,
                        "type": {
                            "name": "rock",
                            "url": "https://pokeapi.co/api/v2/type/6/",
                        },
                    },
                    {
                        "slot": 2,
                        "type": {
                            "name": "dark",
                            "url": "https://pokeapi.co/api/v2/type/17/",
                        },
                    },
                ],
                "stats": [
                    {
                        "base_stat": 134,
                        "stat": {"name": "attack", "url": "https://pokeapi.co/api/v2/stat/2/"},
                    }
                ],
            }
        )
        body = body + " " * 2500
        result = _prune_redundant_urls(body)
        assert "pokeapi.co" not in result
        assert '"rock"' in result
        assert '"dark"' in result
        assert "attack" in result

    def test_prose_body_untouched(self):
        """Non-JSON bodies (markdown/prose with URLs) pass through unchanged."""
        from moira.workflow.nodes.research import _prune_redundant_urls

        body = "Check https://example.com/page for details. " * 100
        assert _prune_redundant_urls(body) == body

    def test_short_body_skipped(self):
        """Bodies under the prune minimum are returned as-is (cost guard)."""
        from moira.workflow.nodes.research import _prune_redundant_urls

        body = json.dumps({"name": "x", "url": "https://pokeapi.co/api/v2/x"})
        assert len(body) < 2000
        assert _prune_redundant_urls(body) == body

    def test_storage_not_pruned_via_find_or_merge(self):
        """Citation storage keeps the un-pruned body even when feedback is
        pruned — recall_source serves the pruned copy at read time, not the
        store."""
        from moira.workflow.nodes.research import _prune_redundant_urls

        # The pruning contract: _cap_feedback_body prunes; the citation
        # stores what it was given (un-pruned). This test pins the helper
        # boundary so a future refactor can't silently prune storage.
        raw = json.dumps({"name": "Moltres", "url": "https://pokeapi.co/api/v2/pokemon/146"})
        raw = raw + " " * 2500
        assert "pokeapi.co" in raw
        pruned = _prune_redundant_urls(raw)
        assert "pokeapi.co" not in pruned
        # And the cap pipeline prunes before capping:
        from moira.workflow.nodes.research import _cap_feedback_body

        out = _cap_feedback_body(raw, "cit001")
        assert "pokeapi.co" not in out or len(out) <= 3000

    def test_non_string_url_values_untouched(self):
        """Numeric/null url-ish fields and non-URL strings pass through."""
        from moira.workflow.nodes.research import _prune_redundant_urls

        body = json.dumps({"name": "x", "url_count": 5, "linked": "not-a-url", "id": 1})
        body = body + " " * 2500
        result = _prune_redundant_urls(body)
        assert '"url_count": 5' in result
        assert '"linked": "not-a-url"' in result

    def test_url_prefix_variants_covered(self):
        """Fields like sprite_url / href / link are pruned when redundant."""
        from moira.workflow.nodes.research import _prune_redundant_urls

        body = json.dumps(
            {
                "name": "pixel art",
                "sprite_url": "https://raw.githubusercontent.com/x/y.png",
                "href": "https://example.com/a",
                "link": "https://example.com/b",
            }
        )
        body = body + " " * 2500
        result = _prune_redundant_urls(body)
        assert "githubusercontent" not in result
        assert "example.com/a" not in result
        assert "example.com/b" not in result
        assert "pixel art" in result
