"""Tests for the tool identification node."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from _node_test_helpers import _build_state, _inject_services, _make_run_config

from moira.models.knowledge import Fact
from moira.service_setup import _services
from moira.tools.base import ToolDefinition


class TestToolIdentification:
    @pytest.mark.asyncio
    async def test_happy_path(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(
            return_value=[
                ToolDefinition(name="web_search", description="Search the web"),
                ToolDefinition(name="api_tool", description="API tool"),
            ]
        )
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = [
            ToolDefinition(name="calculator", description="Math", is_default=True),
        ]
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="entity1", fact_needed="some fact", status="unknown"),
        ]

        result = await tool_identification(state, _make_run_config(config))

        candidates = result["execution_state"]["candidate_tools"]
        names = [t.name for t in candidates]
        assert "web_search" in names
        assert "api_tool" in names
        assert "calculator" in names

        step_cost = config.budget.cost_weights.tool_identification
        assert result["execution_state"]["budget_remaining"] == (
            config.budget.default_limit - step_cost
        )

    @pytest.mark.asyncio
    async def test_empty_facts_list(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=[])
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = [
            ToolDefinition(name="calculator", description="Math", is_default=True),
        ]
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = []

        result = await tool_identification(state, _make_run_config(config))

        mock_discovery.discover.assert_not_called()
        names = [t.name for t in result["execution_state"]["candidate_tools"]]
        assert "calculator" in names

    @pytest.mark.asyncio
    async def test_discovery_failure_gets_defaults(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(side_effect=Exception("LanceDB down"))
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = [
            ToolDefinition(name="calculator", description="Math", is_default=True),
        ]
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        result = await tool_identification(state, _make_run_config(config))

        names = [t.name for t in result["execution_state"]["candidate_tools"]]
        assert "calculator" in names

    @pytest.mark.asyncio
    async def test_deduplication_of_discovered_tools(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(
            return_value=[
                ToolDefinition(name="web_search", description="Search"),
                ToolDefinition(name="web_search", description="Search again"),
            ]
        )
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = []
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        result = await tool_identification(state, _make_run_config(config))

        names = [t.name for t in result["execution_state"]["candidate_tools"]]
        assert names.count("web_search") == 1

    @pytest.mark.asyncio
    async def test_skips_verified_facts(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=[])
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = []
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="verified"),
        ]

        await tool_identification(state, _make_run_config(config))
        mock_discovery.discover.assert_not_called()


class TestToolIdentificationPhaseC:
    @pytest.mark.asyncio
    async def test_per_fact_discovery_queries(self, config, mock_writer, mock_model):
        """Each unknown fact should trigger its own discover() call."""
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=[])
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = []
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="pokemon", fact_needed="typing", status="unknown"),
            Fact(id="f002", subject="pokemon", fact_needed="base stats", status="unknown"),
            Fact(id="f003", subject="pokemon", fact_needed="evolution", status="unknown"),
        ]

        await tool_identification(state, _make_run_config(config))

        assert mock_discovery.discover.call_count == 3
        calls = mock_discovery.discover.call_args_list
        assert calls[0][0][0] == "pokemon typing"
        assert calls[1][0][0] == "pokemon base stats"
        assert calls[2][0][0] == "pokemon evolution"

    @pytest.mark.asyncio
    async def test_per_fact_discovery_merges_across_facts(self, config, mock_writer, mock_model):
        """Different facts may discover different tools; all should be merged."""
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(
            side_effect=[
                [ToolDefinition(name="pokeapi", description="Pokemon API")],
                [ToolDefinition(name="web_search", description="Search")],
                [ToolDefinition(name="pokeapi", description="Pokemon API again")],
            ]
        )
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = [
            ToolDefinition(name="calculator", description="Math", is_default=True),
        ]
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="a", fact_needed="x", status="unknown"),
            Fact(id="f002", subject="b", fact_needed="y", status="unknown"),
            Fact(id="f003", subject="c", fact_needed="z", status="unknown"),
        ]

        result = await tool_identification(state, _make_run_config(config))

        names = [t.name for t in result["execution_state"]["candidate_tools"]]
        assert "pokeapi" in names
        assert "web_search" in names
        assert "calculator" in names
        assert names.count("pokeapi") == 1

    @pytest.mark.asyncio
    async def test_tool_costs_populated_from_candidates(self, config, mock_writer, mock_model):
        """tool_costs should be populated from candidate tools' invocation_cost."""
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(
            return_value=[
                ToolDefinition(name="web_search", description="Search", invocation_cost=5.0),
                ToolDefinition(name="api_tool", description="API", invocation_cost=2.0),
            ]
        )
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = [
            ToolDefinition(
                name="calculator",
                description="Math",
                is_default=True,
                invocation_cost=0.1,
            ),
        ]
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        result = await tool_identification(state, _make_run_config(config))

        tc = result["execution_state"]["tool_costs"]
        assert tc["web_search"] == 5.0
        assert tc["api_tool"] == 2.0
        assert tc["calculator"] == 0.1

    @pytest.mark.asyncio
    async def test_tool_call_limits_populated(self, config, mock_writer, mock_model):
        """tool_call_limits should be populated for tools with limit > 0."""
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(
            return_value=[
                ToolDefinition(
                    name="web_search",
                    description="Search",
                    invocation_cost=5.0,
                    call_limit_per_run=10,
                ),
                ToolDefinition(
                    name="api_tool",
                    description="API",
                    invocation_cost=2.0,
                ),
            ]
        )
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = []
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        result = await tool_identification(state, _make_run_config(config))

        limits = result["execution_state"]["tool_call_limits"]
        assert limits["web_search"] == 10
        assert "api_tool" not in limits

    @pytest.mark.asyncio
    async def test_tool_call_step_limits_populated(self, config, mock_writer, mock_model):
        """tool_call_step_limits should be populated for tools with per-step limit > 0."""
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(
            return_value=[
                ToolDefinition(
                    name="web_search",
                    description="Search",
                    invocation_cost=5.0,
                    call_limit_per_run=10,
                    call_limit_per_step=5,
                ),
                ToolDefinition(
                    name="api_tool",
                    description="API",
                    invocation_cost=2.0,
                ),
            ]
        )
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = []
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]

        result = await tool_identification(state, _make_run_config(config))

        step_limits = result["execution_state"]["tool_call_step_limits"]
        assert step_limits["web_search"] == 5
        assert "api_tool" not in step_limits

    @pytest.mark.asyncio
    async def test_tool_costs_merges_with_initial_state(self, config, mock_writer, mock_model):
        """tool_costs from initial_state (from streaming.py catalog) should
        be preserved when tool_identification merges its candidate costs."""
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(
            return_value=[
                ToolDefinition(name="web_search", description="Search", invocation_cost=5.0),
            ]
        )
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = []
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="x", fact_needed="y", status="unknown"),
        ]
        state["execution_state"]["tool_costs"] = {
            "url_content": 3.0,
            "calculator": 0.1,
        }
        state["execution_state"]["tool_call_limits"] = {
            "url_content": 15,
        }

        result = await tool_identification(state, _make_run_config(config))

        tc = result["execution_state"]["tool_costs"]
        assert tc["web_search"] == 5.0
        assert tc["url_content"] == 3.0
        assert tc["calculator"] == 0.1

        limits = result["execution_state"]["tool_call_limits"]
        assert limits["url_content"] == 15

    @pytest.mark.asyncio
    async def test_detail_includes_per_fact_queries(self, config, mock_writer, mock_model):
        """The streamed detail should include per-fact query info with top_results."""
        _inject_services(config, mock_model)

        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(
            return_value=[
                ToolDefinition(name="pokeapi", description="API"),
                ToolDefinition(name="pokemon_db", description="DB"),
            ]
        )
        _services["tool_discovery"] = mock_discovery

        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = [
            ToolDefinition(name="calculator", description="Math", is_default=True),
        ]
        _services["tool_catalog"] = mock_catalog

        from moira.workflow.nodes.tool_identification import tool_identification

        state = _build_state(config, "Test question")
        state["knowledge"]["facts"] = [
            Fact(id="f001", subject="Tyranitar", fact_needed="typing", status="unknown"),
        ]

        await tool_identification(state, _make_run_config(config))

        node_end = [e for e in mock_writer if e.get("event") == "node_end"][0]
        detail = node_end["payload"]["detail"]
        assert len(detail["queries"]) == 1
        assert detail["queries"][0]["fact_id"] == "f001"
        assert detail["queries"][0]["query"] == "Tyranitar typing"
        assert "pokeapi" in detail["queries"][0]["top_results"]
        assert "calculator" in detail["default_tools_included"]
