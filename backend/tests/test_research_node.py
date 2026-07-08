"""Tests for the research node (public node + call limits + summary + native mode)."""

import json
from unittest.mock import AsyncMock

import pytest
from _node_test_helpers import _build_state, _inject_services, _make_run_config

from moira.inference.client import ChatResponse
from moira.inference.registry import ResolvedModel
from moira.models.knowledge import Fact, ToolCallPlan
from moira.service_setup import _services
from moira.tools.base import ToolCall, ToolDefinition, ToolResult


class TestResearch:
    @pytest.mark.asyncio
    async def test_happy_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="Entity1 is confirmed",
                    success=True,
                    duration_ms=100,
                    metadata={
                        "results": [
                            {
                                "title": "Entity1 Page",
                                "url": "https://example.com/entity1",
                                "snippet": "Entity1 is confirmed",
                            },
                        ]
                    },
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        model_responses = [
            ChatResponse(
                content=json.dumps(
                    [
                        {"tool": "web_search", "args": {"query": "entity1"}},
                    ]
                )
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [
                            {
                                "fact_id": "f001",
                                "claim": "Entity1 confirmed",
                                "citation_ids": ["cit001"],
                            },
                        ],
                        "sources": [],
                    }
                )
            ),
        ]
        mock_model["client"].chat_completion.side_effect = model_responses

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="entity1", fact_needed="some fact", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "entity1"}, target_fact_ids=["f001"], cost=1.0
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]

        result = await research(state, _make_run_config(config))

        assert len(result["knowledge"]["facts"]) >= 1
        assert result["knowledge"]["facts"][0]["status"] == "unverified"
        assert "confirmed" in result["knowledge"]["facts"][0]["claim"].lower()

        assert len(result["knowledge"]["citations"]) >= 1
        cit = result["knowledge"]["citations"][0]
        assert cit["url"] == "https://example.com/entity1"
        assert cit["title"] == "Entity1 Page"
        assert len(result["knowledge"]["facts"][0].get("citation_ids", [])) >= 1, (
            "Fact should be linked to at least one citation from tool execution"
        )
        assert result["execution_state"]["tool_call_counts"]["web_search"] == 1
        assert result["execution_state"]["total_tool_cost_consumed"] == 1.0

        step_cost = config.budget.cost_weights.research
        expected = config.budget.default_limit - step_cost - 1.0
        assert result["execution_state"]["budget_remaining"] == expected

    @pytest.mark.asyncio
    async def test_budget_exhaustion(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["execution_state"]["budget_remaining"] = 0.0

        result = await research(state, _make_run_config(config))

        assert "error" in result["execution_state"]
        assert "Insufficient budget" in result["execution_state"]["error"]

    @pytest.mark.asyncio
    async def test_model_interpretation_failure_fatal(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search", output="Some result", success=True, duration_ms=50
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            RuntimeError("Model crashed"),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "x"}, target_fact_ids=["f001"], cost=1.0
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]

        with pytest.raises(RuntimeError, match="Model crashed"):
            await research(state, _make_run_config(config))

    @pytest.mark.asyncio
    async def test_no_tool_executor_skips_calls(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"tool_calls": [], "discovered_facts": [], "sources": []})
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "x"}, target_fact_ids=["f001"], cost=1.0
            ),
        ]

        result = await research(state, _make_run_config(config))

        assert result["execution_state"]["total_tool_cost_consumed"] == 0.0

    @pytest.mark.asyncio
    async def test_tool_execution_error_continues(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(side_effect=Exception("Tool error"))
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"tool_calls": [], "discovered_facts": [], "sources": []})
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "x"}, target_fact_ids=["f001"], cost=1.0
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]

        result = await research(state, _make_run_config(config))

        assert result["knowledge"]["facts"][0]["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_empty_claim_reverted_by_cleanup(self, config, mock_writer, mock_model):
        """A fact auto-promoted to 'unverified' by tool execution but never
        given a claim by the model must be reverted to 'unknown' by the
        cleanup pass before the node returns."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="Some search result",
                    success=True,
                    duration_ms=100,
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        # Round 1: model calls a tool.  Round 2: model sees the result but
        # returns an empty claim for f001.
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {"tool_calls": [{"tool": "web_search", "args": {"query": "x"}}]}
                )
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [
                            {"fact_id": "f001", "claim": ""},
                        ],
                        "sources": [],
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "x"}, target_fact_ids=["f001"], cost=1.0
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]

        result = await research(state, _make_run_config(config))

        # Fact was auto-promoted during the loop, then reverted by cleanup
        assert result["knowledge"]["facts"][0]["status"] == "unknown", (
            "Empty-claim fact should be reverted to 'unknown' by cleanup pass"
        )

    @pytest.mark.asyncio
    async def test_citation_linked_when_discovered_facts_come_first(
        self, config, mock_writer, mock_model
    ):
        """Citations must be linked to facts even when the model returns
        discovered_facts in the same round as tool_calls (before tools run).
        _apply_discovered_facts changes status to 'unverified' before tool
        execution, so plan-matching must accept 'unverified' facts too.

        Regression test for citation_ids=[] bug where all report citations
        collapsed to [1] because no facts were linked to any sources."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search", output="Result data", success=True, duration_ms=100
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        # Round 1: model returns tool_calls. Round 2: model sees [cit001]
        # labeled result and returns discovered_facts with citation_ids.
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [{"tool": "web_search", "args": {"query": "test"}}],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [
                            {
                                "fact_id": "f001",
                                "claim": "Entity1 confirmed",
                                "citation_ids": ["cit001"],
                            },
                        ],
                        "sources": [],
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="entity1", fact_needed="some fact", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "entity1"}, target_fact_ids=["f001"], cost=1.0
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]

        result = await research(state, _make_run_config(config))

        fact = result["knowledge"]["facts"][0]
        assert fact["status"] == "unverified"
        assert "cit001" in fact.get("citation_ids", []), (
            "Fact should have citation_ids linked from the model's "
            "discovered_facts response. "
            f"Got citation_ids={fact.get('citation_ids', [])}"
        )

    @pytest.mark.asyncio
    async def test_structured_results_create_per_result_citations(
        self, config, mock_writer, mock_model
    ):
        """When web_search metadata carries structured results, one citation
        is created per search result, each with url/title/excerpt."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="formatted text output",
                    success=True,
                    duration_ms=100,
                    metadata={
                        "results": [
                            {"title": "First", "url": "https://a.com", "snippet": "snippet A"},
                            {"title": "Second", "url": "https://b.com", "snippet": "snippet B"},
                            {"title": "Third", "url": "https://c.com", "snippet": "snippet C"},
                        ]
                    },
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [{"tool": "web_search", "args": {"query": "test"}}],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [
                            {"fact_id": "f001", "claim": "found it", "citation_ids": ["cit002"]},
                        ],
                        "sources": [],
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "x"}, target_fact_ids=["f001"], cost=1.0
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]

        result = await research(state, _make_run_config(config))

        citations = result["knowledge"]["citations"]
        assert len(citations) == 3
        assert citations[0]["id"] == "cit001"
        assert citations[0]["url"] == "https://a.com"
        assert citations[0]["title"] == "First"
        assert citations[0]["excerpt"] == "snippet A"
        assert citations[0]["snippets"] == ["snippet A"]
        assert citations[1]["id"] == "cit002"
        assert citations[1]["url"] == "https://b.com"
        assert citations[2]["id"] == "cit003"
        assert citations[2]["url"] == "https://c.com"

    @pytest.mark.asyncio
    async def test_metadata_less_tool_falls_back_to_single_citation(
        self, config, mock_writer, mock_model
    ):
        """Tools without structured metadata (e.g., calculator) still
        get a single citation with text excerpt."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="calculator",
                    output="42",
                    success=True,
                    duration_ms=100,
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [{"tool": "calculator", "args": {"expression": "6*7"}}],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [
                            {"fact_id": "f001", "claim": "found it", "citation_ids": ["cit001"]},
                        ],
                        "sources": [],
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="calculator",
                args={"expression": "6*7"},
                target_fact_ids=["f001"],
                cost=1.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="calculator", description="Calculator"),
        ]

        result = await research(state, _make_run_config(config))

        citations = result["knowledge"]["citations"]
        assert len(citations) == 1
        assert citations[0]["id"] == "cit001"
        assert "url" not in citations[0] or citations[0].get("url") is None
        assert "42" in citations[0].get("excerpt", "")

    @pytest.mark.asyncio
    async def test_url_content_metadata_creates_citation_with_url_and_title(
        self, config, mock_writer, mock_model
    ):
        """url_content metadata populates citation with url, title, and
        a content body larger than the snippet."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="url_content",
                    output="# Some Page\n\nLots of content here.",
                    success=True,
                    duration_ms=100,
                    metadata={
                        "results": [
                            {
                                "url": "https://example.com/article",
                                "title": "Some Page",
                                "snippet": "# Some Page",
                                "content": "# Some Page\n\nLots of content here." * 50,
                            }
                        ]
                    },
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [
                            {"tool": "url_content", "args": {"url": "https://example.com/article"}}
                        ],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [
                            {"fact_id": "f001", "claim": "found it", "citation_ids": ["cit001"]},
                        ],
                        "sources": [],
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="url_content",
                args={"url": "https://example.com/article"},
                target_fact_ids=["f001"],
                cost=1.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="url_content", description="Fetch a URL"),
        ]

        result = await research(state, _make_run_config(config))

        citations = result["knowledge"]["citations"]
        assert len(citations) == 1
        cit = citations[0]
        assert cit["id"] == "cit001"
        assert cit["url"] == "https://example.com/article"
        assert cit["title"] == "Some Page"
        assert cit["source"] == "url_content"
        assert "Some Page" in cit.get("excerpt", "")
        # Content should be the richer body, not just the snippet
        assert len(cit.get("content", "")) > len(cit.get("excerpt", ""))

    @pytest.mark.asyncio
    async def test_tool_result_display_truncation_note(self, config, mock_writer, mock_model):
        """When tool output exceeds the display limit, the writer event
        should include a truncation note."""
        _inject_services(config, mock_model)

        long_output = "x" * 5000
        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="url_content", output=long_output, success=True, duration_ms=100
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [{"tool": "url_content", "args": {"url": "https://x.com"}}],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [
                            {"fact_id": "f001", "claim": "found it", "citation_ids": ["cit001"]},
                        ],
                        "sources": [],
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="url_content",
                args={"url": "https://x.com"},
                target_fact_ids=["f001"],
                cost=1.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="url_content", description="Fetch a URL"),
        ]

        await research(state, _make_run_config(config))

        # Find the tool_result event in mock_writer
        tool_events = [
            e for e in mock_writer if isinstance(e, dict) and e.get("event") == "tool_result"
        ]
        assert len(tool_events) >= 1
        payload = tool_events[0]["payload"]
        assert "more chars not shown" in payload["output"]
        assert len(payload["output"]) < len(long_output)

    @pytest.mark.asyncio
    async def test_citation_dedup_same_url_across_rounds(self, config, mock_writer, mock_model):
        """When the same URL appears in search results across multiple
        research rounds, snippets are merged into a single citation."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()

        async def _execute_batch(calls):
            return [
                ToolResult(
                    tool_name="web_search",
                    output="text",
                    success=True,
                    duration_ms=100,
                    metadata={
                        "results": [
                            {
                                "title": "Result",
                                "url": "https://shared.com",
                                "snippet": f"snippet from query '{calls[i].arguments['query']}'",
                            },
                        ]
                    },
                )
                for i in range(len(calls))
            ]

        mock_executor.execute_batch = AsyncMock(side_effect=_execute_batch)
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            # Round 1: search
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [{"tool": "web_search", "args": {"query": "alpha"}}],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
            # Round 2: search again (different query, same URL in results)
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [{"tool": "web_search", "args": {"query": "beta"}}],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
            # Round 3: done
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [
                            {"fact_id": "f001", "claim": "found it", "citation_ids": ["cit001"]},
                        ],
                        "sources": [],
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "x"}, target_fact_ids=["f001"], cost=1.0
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]

        result = await research(state, _make_run_config(config))

        citations = result["knowledge"]["citations"]
        assert len(citations) == 1
        assert citations[0]["url"] == "https://shared.com"
        assert citations[0]["snippets"] == [
            "snippet from query 'alpha'",
            "snippet from query 'beta'",
        ]

    @pytest.mark.asyncio
    async def test_citation_dedup_same_url_same_batch(self, config, mock_writer, mock_model):
        """Two web_search calls in the same batch returning the same URL
        should merge into one citation with two snippets."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="text",
                    success=True,
                    duration_ms=100,
                    metadata={
                        "results": [
                            {
                                "title": "Shared",
                                "url": "https://dup.com",
                                "snippet": "snippet from first search",
                            },
                        ]
                    },
                ),
                ToolResult(
                    tool_name="web_search",
                    output="text",
                    success=True,
                    duration_ms=100,
                    metadata={
                        "results": [
                            {
                                "title": "Shared",
                                "url": "https://dup.com",
                                "snippet": "snippet from second search",
                            },
                            {
                                "title": "Unique",
                                "url": "https://unique.com",
                                "snippet": "only here",
                            },
                        ]
                    },
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            # Round 1: two searches
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [
                            {"tool": "web_search", "args": {"query": "a"}},
                            {"tool": "web_search", "args": {"query": "b"}},
                        ],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
            # Round 2: done
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "x"}, target_fact_ids=["f001"], cost=1.0
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]

        result = await research(state, _make_run_config(config))

        citations = result["knowledge"]["citations"]
        # dedup: dup.com merged, unique.com new
        assert len(citations) == 2
        dup = [c for c in citations if c.get("url") == "https://dup.com"][0]
        assert dup["snippets"] == [
            "snippet from first search",
            "snippet from second search",
        ]

    @pytest.mark.asyncio
    async def test_apply_sources_dedup_by_url(self, config, mock_writer, mock_model):
        """Model-emitted sources with a URL that already has a citation
        should merge instead of creating a duplicate."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}

        # First call: creates a new citation
        cit_id_1, is_new_1 = _find_or_merge_citation(
            citations,
            seen_urls,
            source="web_search",
            url="https://example.com",
            title="Example",
            snippet="first snippet",
        )
        assert is_new_1 is True
        assert cit_id_1 == "cit001"
        assert len(citations) == 1
        assert citations[0]["snippets"] == ["first snippet"]

        # Second call: same URL, different snippet → merge
        cit_id_2, is_new_2 = _find_or_merge_citation(
            citations,
            seen_urls,
            source="web_search",
            url="https://example.com",
            title="Example",
            snippet="second snippet",
        )
        assert is_new_2 is False
        assert cit_id_2 == "cit001"
        assert len(citations) == 1
        assert citations[0]["snippets"] == ["first snippet", "second snippet"]

        # Third call: new URL → new citation
        cit_id_3, is_new_3 = _find_or_merge_citation(
            citations,
            seen_urls,
            source="web_search",
            url="https://other.com",
            title="Other",
            snippet="other snippet",
        )
        assert is_new_3 is True
        assert cit_id_3 == "cit002"
        assert len(citations) == 2

    @pytest.mark.asyncio
    async def test_find_or_merge_no_url_always_creates_new(self, config, mock_writer, mock_model):
        """When url is None (e.g., calculator results), each call creates
        a new citation since there's nothing to dedup on."""
        from moira.workflow.nodes.research import _find_or_merge_citation

        citations: list = []
        seen_urls: dict[str, str] = {}

        cit_id_1, is_new_1 = _find_or_merge_citation(
            citations,
            seen_urls,
            source="calculator",
            snippet="42",
        )
        cit_id_2, is_new_2 = _find_or_merge_citation(
            citations,
            seen_urls,
            source="calculator",
            snippet="99",
        )
        assert is_new_1 is True
        assert is_new_2 is True
        assert cit_id_1 == "cit001"
        assert cit_id_2 == "cit002"
        assert len(citations) == 2

    # ---- Overlap-aware snippet merge tests ----

    @pytest.mark.asyncio
    async def test_empty_tool_plan(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"tool_calls": [], "discovered_facts": [], "sources": []})
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = []
        state["execution_state"]["tool_call_plan"] = []

        result = await research(state, _make_run_config(config))

        assert result["execution_state"]["total_tool_cost_consumed"] == 0.0

    @pytest.mark.asyncio
    async def test_per_tool_budget_skip(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="expensive_tool", output="data", success=True, duration_ms=50
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"tool_calls": [], "discovered_facts": [], "sources": []})
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(tool="expensive_tool", args={}, target_fact_ids=["f001"], cost=50.0),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="expensive_tool", description="Expensive"),
        ]
        state["execution_state"]["tool_costs"] = {"expensive_tool": 50.0}
        state["execution_state"]["budget_remaining"] = 5.0

        result = await research(state, _make_run_config(config))

        assert result["execution_state"]["total_tool_cost_consumed"] == 0.0

    @pytest.mark.asyncio
    async def test_post_loop_fact_extraction(self, config, mock_writer, mock_model):
        """When the model produces tool_calls but never includes discovered_facts,
        the post-loop extraction step should extract facts from tool results."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="The limit of sqrt(x)/log2(x) as x->infinity is infinity",
                    success=True,
                    duration_ms=50,
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        # Round 1: model returns tool_calls but no discovered_facts
        # Post-loop: model extracts facts from tool results
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [
                            {"tool": "web_search", "args": {"query": "sqrt vs log2 limit"}},
                        ],
                    }
                ),
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "discovered_facts": [
                            {
                                "fact_id": "f001",
                                "subject": "limit",
                                "claim": "lim(x->inf) sqrt(x)/log2(x) = infinity",
                            },
                        ],
                        "sources": [
                            {"source": "web_search", "excerpt": "sqrt(x)/log2(x) -> infinity"},
                        ],
                    }
                ),
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Compare sqrt(x) and log2(x)")
        state["knowledge"]["user_goal"] = "Compare growth rates"
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="limit",
                fact_needed="limit of sqrt(x)/log2(x)",
                status="unknown",
            ),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search",
                args={"query": "sqrt vs log2"},
                target_fact_ids=["f001"],
                cost=1.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]

        result = await research(state, _make_run_config(config))

        # The post-loop extraction should have resolved the fact
        facts = result["knowledge"]["facts"]
        resolved = [f for f in facts if f["status"] != "unknown" and f.get("claim")]
        assert len(resolved) == 1
        assert resolved[0]["id"] == "f001"
        assert "infinity" in resolved[0]["claim"].lower()
        assert mock_model["client"].chat_completion.call_count == 2


class TestResearchCallLimits:
    @pytest.mark.asyncio
    async def test_call_limit_enforced(self, config, mock_writer, mock_model):
        """When a tool has hit its call limit, further calls should be skipped."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"tool_calls": [], "discovered_facts": [], "sources": []})
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_call_limits"] = {"web_search": 1}
        state["execution_state"]["tool_call_counts"] = {"web_search": 1}
        state["execution_state"]["tool_costs"] = {"web_search": 5.0}

        result = await research(state, _make_run_config(config))

        mock_executor.execute_batch.assert_not_called()
        assert result["execution_state"]["total_tool_cost_consumed"] == 0.0

    @pytest.mark.asyncio
    async def test_call_limit_partial_batch(self, config, mock_writer, mock_model):
        """When some tools hit their limit, only remaining tools should execute."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(tool_name="api_tool", output="result", success=True, duration_ms=50),
            ]
        )
        _services["tool_executor"] = mock_executor

        model_responses = [
            ChatResponse(
                content=json.dumps(
                    [
                        {"tool": "web_search", "args": {"query": "x"}},
                        {"tool": "api_tool", "args": {"q": "y"}},
                    ]
                )
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
        ]
        mock_model["client"].chat_completion.side_effect = model_responses

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search",
                args={"query": "x"},
                target_fact_ids=["f001"],
                cost=5.0,
            ),
            ToolCallPlan(
                tool="api_tool",
                args={"q": "y"},
                target_fact_ids=["f001"],
                cost=2.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
            ToolDefinition(name="api_tool", description="API"),
        ]
        state["execution_state"]["tool_call_limits"] = {"web_search": 2}
        state["execution_state"]["tool_call_counts"] = {"web_search": 2}
        state["execution_state"]["tool_costs"] = {"web_search": 5.0, "api_tool": 2.0}

        result = await research(state, _make_run_config(config))

        executed_calls = mock_executor.execute_batch.call_args[0][0]
        assert len(executed_calls) == 1
        assert executed_calls[0].name == "api_tool"
        assert result["execution_state"]["tool_call_counts"]["api_tool"] == 1

    @pytest.mark.asyncio
    async def test_tool_cost_deduction_custom_cost(self, config, mock_writer, mock_model):
        """Tool cost deduction should use the cost from tool_costs, not default 1.0."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(tool_name="web_search", output="data", success=True, duration_ms=100),
            ]
        )
        _services["tool_executor"] = mock_executor

        model_responses = [
            ChatResponse(
                content=json.dumps(
                    [
                        {"tool": "web_search", "args": {"query": "x"}},
                    ]
                )
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [
                            {"fact_id": "f001", "claim": "Found it"},
                        ],
                        "sources": [],
                    }
                )
            ),
        ]
        mock_model["client"].chat_completion.side_effect = model_responses

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search",
                args={"query": "x"},
                target_fact_ids=["f001"],
                cost=5.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_costs"] = {"web_search": 5.0}

        result = await research(state, _make_run_config(config))

        assert result["execution_state"]["total_tool_cost_consumed"] == 5.0
        step_cost = config.budget.cost_weights.research
        expected = config.budget.default_limit - step_cost - 5.0
        assert result["execution_state"]["budget_remaining"] == expected

    @pytest.mark.asyncio
    async def test_zero_call_limit_means_unlimited(self, config, mock_writer, mock_model):
        """A call_limit of 0 (or absent) means unlimited calls allowed."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(tool_name="web_search", output="data", success=True, duration_ms=100),
            ]
        )
        _services["tool_executor"] = mock_executor

        model_responses = [
            ChatResponse(
                content=json.dumps(
                    [
                        {"tool": "web_search", "args": {"query": "x"}},
                    ]
                )
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [
                            {"fact_id": "f001", "claim": "Found"},
                        ],
                        "sources": [],
                    }
                )
            ),
        ]
        mock_model["client"].chat_completion.side_effect = model_responses

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search",
                args={"query": "x"},
                target_fact_ids=["f001"],
                cost=1.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_call_limits"] = {}
        state["execution_state"]["tool_call_counts"] = {"web_search": 5}
        state["execution_state"]["tool_costs"] = {"web_search": 1.0}

        result = await research(state, _make_run_config(config))

        assert result["execution_state"]["tool_call_counts"]["web_search"] == 6
        assert result["execution_state"]["total_tool_cost_consumed"] == 1.0


class TestResearchSummaryRound:
    @pytest.mark.asyncio
    async def test_summary_round_on_max_rounds_exhausted(self, config, mock_writer, mock_model):
        """When all rounds are used, a final summary round should happen
        and its response should be the one shown in the step detail."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="Result data",
                    success=True,
                    duration_ms=50,
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        # Round 1: tool calls, Round 2: tool calls, Round 3: tool calls
        # Then summary round (extra model call)
        tool_call_response = json.dumps(
            {
                "tool_calls": [{"tool": "web_search", "args": {"query": "x"}}],
                "discovered_facts": [],
                "sources": [],
            }
        )
        summary_response = json.dumps(
            {
                "tool_calls": [],
                "discovered_facts": [
                    {"fact_id": "f001", "subject": "x", "claim": "Found it"},
                ],
                "sources": [{"source": "web_search", "excerpt": "Result data"}],
            }
        )

        # 3 rounds of tool calls + 1 summary round = 4 model calls
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(content=tool_call_response),
            ChatResponse(content=tool_call_response),
            ChatResponse(content=tool_call_response),
            ChatResponse(content=summary_response),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search",
                args={"query": "x"},
                target_fact_ids=["f001"],
                cost=1.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_costs"] = {"web_search": 1.0}

        await research(state, _make_run_config(config))

        # The response in detail should be the summary, not a tool call list
        node_end_events = [e for e in mock_writer if e.get("event") == "node_end"]
        assert len(node_end_events) == 1
        detail = node_end_events[0]["payload"]["detail"]
        assert "Found it" in detail["response"]

        # 3 tool rounds + 1 summary = 4 model calls
        assert mock_model["client"].chat_completion.call_count == 4

    @pytest.mark.asyncio
    async def test_no_summary_when_model_signals_done(self, config, mock_writer, mock_model):
        """When the model signals completion (empty tool_calls), no summary
        round should happen."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="Data",
                    success=True,
                    duration_ms=50,
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [{"tool": "web_search", "args": {"query": "x"}}],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [
                            {"fact_id": "f001", "subject": "x", "claim": "Done"},
                        ],
                        "sources": [],
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search",
                args={"query": "x"},
                target_fact_ids=["f001"],
                cost=1.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_costs"] = {"web_search": 1.0}

        await research(state, _make_run_config(config))

        # Only 2 model calls: tool round + done signal
        assert mock_model["client"].chat_completion.call_count == 2

    @pytest.mark.asyncio
    async def test_node_end_detail_has_no_tool_results(self, config, mock_writer, mock_model):
        """The node_end detail should NOT include tool_results (they come from
        tool_result events in the run_manager)."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="Data",
                    success=True,
                    duration_ms=50,
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [{"tool": "web_search", "args": {"query": "x"}}],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [
                            {"fact_id": "f001", "subject": "x", "claim": "Found"},
                        ],
                        "sources": [],
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search",
                args={"query": "x"},
                target_fact_ids=["f001"],
                cost=1.0,
            ),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_costs"] = {"web_search": 1.0}

        await research(state, _make_run_config(config))

        node_end_events = [e for e in mock_writer if e.get("event") == "node_end"]
        detail = node_end_events[0]["payload"]["detail"]
        assert "tool_results" not in detail

    @pytest.mark.asyncio
    async def test_skips_calls_missing_required_params(self, config, mock_writer, mock_model):
        """Tool calls missing required parameters should be skipped before
        execution to avoid deterministic failures."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "tool_calls": [],
                    "discovered_facts": [],
                    "sources": [],
                }
            ),
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find info"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(
                name="web_search",
                description="Search",
                argument_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            ),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search",
                args={},
                target_fact_ids=["f001"],
                cost=1.0,
            ),
        ]
        state["execution_state"]["tool_costs"] = {"web_search": 1.0}

        # Model returns a call with no query param
        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [{"tool": "web_search", "args": {}}],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
            ChatResponse(
                content=json.dumps(
                    {
                        "tool_calls": [],
                        "discovered_facts": [],
                        "sources": [],
                    }
                )
            ),
        ]

        await research(state, _make_run_config(config))

        # web_search should NOT have been executed (missing required param)
        mock_executor.execute_batch.assert_not_called()


class TestResearchNativeMode:
    """Tests for the research node when native_tool_calling is enabled."""

    @pytest.mark.asyncio
    async def test_native_happy_path(self, config, mock_writer, mock_model):
        """Model emits tool_calls, results fed back, then model emits
        discovered_facts and signals completion."""
        _inject_services(config, mock_model)

        # Override resolved model with native_tool_calling enabled
        native_resolved = ResolvedModel(
            model_id="test-model",
            client=mock_model["client"],
            native_tool_calling=True,
            provider_type="completions",
        )
        mock_model["registry"].resolve = AsyncMock(return_value=native_resolved)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="Found it",
                    success=True,
                    duration_ms=100,
                    metadata={
                        "results": [
                            {
                                "title": "Source",
                                "url": "https://example.com",
                                "snippet": "The answer is 42",
                            }
                        ]
                    },
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            # Round 1: model calls a tool
            ChatResponse(
                content="",
                tool_calls=[
                    ToolCall(id="call_1", name="web_search", arguments={"query": "test"}),
                ],
                finish_reason="tool_calls",
            ),
            # Round 2: model is done, emits facts
            ChatResponse(
                content=json.dumps(
                    {
                        "discovered_facts": [
                            {
                                "fact_id": "f001",
                                "claim": "The answer is 42",
                                "citation_ids": ["cit001"],
                            }
                        ],
                        "sources": [],
                    }
                ),
                tool_calls=[],
                finish_reason="stop",
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "What is the answer?")
        state["knowledge"]["user_goal"] = "Find the answer"
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="answer", fact_needed="what is it", status="unknown"),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]
        state["execution_state"]["tool_call_plan"] = [
            ToolCallPlan(
                tool="web_search", args={"query": "test"}, target_fact_ids=["f001"], cost=1.0
            ),
        ]

        result = await research(state, _make_run_config(config))

        assert result["knowledge"]["facts"][0]["status"] == "unverified"
        assert "42" in result["knowledge"]["facts"][0].get("claim", "")
        assert len(result["knowledge"]["citations"]) >= 1
        assert result["execution_state"]["tool_call_counts"]["web_search"] == 1

    @pytest.mark.asyncio
    async def test_native_completion_immediately(self, config, mock_writer, mock_model):
        """When the model returns no tool_calls on the first round, the
        loop exits immediately."""
        _inject_services(config, mock_model)

        native_resolved = ResolvedModel(
            model_id="test-model",
            client=mock_model["client"],
            native_tool_calling=True,
        )
        mock_model["registry"].resolve = AsyncMock(return_value=native_resolved)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"discovered_facts": [], "sources": []}),
            tool_calls=[],
            finish_reason="stop",
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "test")
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]

        await research(state, _make_run_config(config))

        mock_executor.execute_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_native_filters_disallowed_tools(self, config, mock_writer, mock_model):
        """Tools not in the candidate set should be filtered out."""
        _inject_services(config, mock_model)

        native_resolved = ResolvedModel(
            model_id="test-model",
            client=mock_model["client"],
            native_tool_calling=True,
        )
        mock_model["registry"].resolve = AsyncMock(return_value=native_resolved)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(tool_name="web_search", output="ok", success=True, duration_ms=10),
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content="",
                tool_calls=[
                    ToolCall(id="c1", name="disallowed_tool", arguments={}),
                    ToolCall(id="c2", name="web_search", arguments={"query": "x"}),
                ],
            ),
            ChatResponse(
                content=json.dumps({"discovered_facts": [], "sources": []}),
                tool_calls=[],
            ),
            # Post-loop fact extraction (if triggered)
            ChatResponse(
                content=json.dumps({"discovered_facts": [], "sources": []}),
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "test")
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]

        await research(state, _make_run_config(config))

        executed = mock_executor.execute_batch.call_args[0][0]
        assert len(executed) == 1
        assert executed[0].name == "web_search"

    @pytest.mark.asyncio
    async def test_native_hybrid_fact_extraction(self, config, mock_writer, mock_model):
        """Facts are parsed from content alongside tool calls (hybrid mode)."""
        _inject_services(config, mock_model)

        native_resolved = ResolvedModel(
            model_id="test-model",
            client=mock_model["client"],
            native_tool_calling=True,
        )
        mock_model["registry"].resolve = AsyncMock(return_value=native_resolved)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="web_search",
                    output="data",
                    success=True,
                    duration_ms=50,
                    metadata={
                        "results": [{"url": "https://ex.com", "snippet": "info", "title": "T"}]
                    },
                ),
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            # Round 1: tool call + early fact discovery
            ChatResponse(
                content=json.dumps(
                    {
                        "discovered_facts": [
                            {
                                "fact_id": "f001",
                                "claim": "partial result",
                                "citation_ids": ["cit001"],
                            }
                        ],
                        "sources": [],
                    }
                ),
                tool_calls=[
                    ToolCall(id="c1", name="web_search", arguments={"query": "more"}),
                ],
            ),
            # Round 2: done
            ChatResponse(
                content=json.dumps(
                    {
                        "discovered_facts": [
                            {
                                "fact_id": "f001",
                                "claim": "final result",
                                "citation_ids": ["cit001"],
                            }
                        ],
                        "sources": [],
                    }
                ),
                tool_calls=[],
            ),
        ]

        from moira.workflow.nodes.research import research

        state = _build_state(config, "test")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]

        result = await research(state, _make_run_config(config))

        # The fact should be updated (last write wins)
        assert result["knowledge"]["facts"][0]["status"] == "unverified"
        assert result["knowledge"]["facts"][0]["claim"] == "final result"

    @pytest.mark.asyncio
    async def test_native_passes_tools_to_chat_completion(self, config, mock_writer, mock_model):
        """The chat_completion call should receive the candidate tools."""
        _inject_services(config, mock_model)

        native_resolved = ResolvedModel(
            model_id="test-model",
            client=mock_model["client"],
            native_tool_calling=True,
        )
        mock_model["registry"].resolve = AsyncMock(return_value=native_resolved)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content="",
            tool_calls=[],
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "test")
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
        ]

        await research(state, _make_run_config(config))

        call_kwargs = mock_model["client"].chat_completion.call_args[1]
        assert "tools" in call_kwargs
        assert len(call_kwargs["tools"]) == 1


class TestResearchRetryContext:
    """Tests for retry context — when review_count > 0 and last review
    routed 'retry', the system prompt should include established facts,
    prior conclusions, and prior citations so the model can avoid
    re-discovering what is already known."""

    @pytest.mark.asyncio
    async def test_retry_context_in_system_prompt(self, config, mock_writer, mock_model):
        """On review retry, the system prompt should contain the established
        facts, prior conclusions, and prior citations."""
        _inject_services(config, mock_model)

        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(return_value=[])
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps({"tool_calls": [], "discovered_facts": [], "sources": []}),
        )

        from moira.workflow.nodes.research import research

        state = _build_state(config, "Test question")
        state["knowledge"]["user_goal"] = "Find retailers"
        state["knowledge"]["facts"] = [
            Fact(
                id="f001",
                subject="Manufacturer A",
                fact_needed="who makes it",
                claim="Company A makes it",
                status="verified",
                citation_ids=["cit001"],
            ),
            Fact(
                id="f003",
                subject="Retailers",
                fact_needed="which retailers sell it",
                status="unverified",
            ),
        ]
        state["knowledge"]["conclusions"] = [
            {
                "id": "c001",
                "conclusion": "Company A manufactures the product",
                "supporting_fact_ids": ["f001"],
                "status": "verified",
                "reasoning": "based on f001",
            }
        ]
        state["knowledge"]["citations"] = [
            {
                "id": "cit001",
                "source": "web_search",
                "title": "Company A Site",
                "url": "https://company-a.com",
                "excerpt": "Company A makes products",
            }
        ]
        state["knowledge"]["review_history"] = [
            {
                "route": "retry",
                "coverage_assessment": "Missing retailer information",
                "missing_areas": ["Specific retailers that sell the product"],
            }
        ]
        state["execution_state"]["review_count"] = 1
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search"),
        ]

        await research(state, _make_run_config(config))

        # Check the system prompt sent to the model
        call_args = mock_model["client"].chat_completion.call_args
        messages = call_args.kwargs.get("messages") or call_args.args[0]
        system_prompt = messages[0]["content"]

        # Established facts context
        assert "f001" in system_prompt
        assert "Company A makes it" in system_prompt
        # Prior conclusions context
        assert "c001" in system_prompt
        assert "Company A manufactures" in system_prompt
        # Prior citations context
        assert "cit001" in system_prompt
        assert "https://company-a.com" in system_prompt
        # Review feedback
        assert "Missing retailer information" in system_prompt
        # Unverified fact should appear in user prompt
        user_prompt = messages[1]["content"]
        assert "f003" in user_prompt
