"""Tests for the recall_source tool class and registration."""

import pytest

from moira.tools.base import ToolResult
from moira.tools.builtin.recall_source import RecallSourceTool
from moira.tools.standard import STANDARD_TOOLS


class TestRecallSourceToolClass:
    """Tests for RecallSourceTool — the tool class and its definition."""

    def test_tool_name(self):
        assert RecallSourceTool.tool_name == "recall_source"

    def test_tool_description_non_empty(self):
        assert len(RecallSourceTool.tool_description) > 20
        # Should mention citation IDs so the model knows the interface
        assert "citation ID" in RecallSourceTool.tool_description

    def test_tool_group(self):
        assert RecallSourceTool.tool_group == "standard"

    def test_argument_schema_structure(self):
        schema = RecallSourceTool.tool_argument_schema
        assert schema["type"] == "object"
        assert "citation_id" in schema["properties"]
        assert schema["properties"]["citation_id"]["type"] == "string"
        assert "citation_id" in schema["required"]

    @pytest.mark.asyncio
    async def test_execute_stub_returns_failure(self):
        """The stub execute() returns failure — recall_source must be
        called within a research workflow where the loop intercepts it."""
        definition = RecallSourceTool.make_definition()
        tool = RecallSourceTool(definition)
        result = await tool.execute({"citation_id": "cit001"})
        assert isinstance(result, ToolResult)
        assert result.success is False


class TestRecallSourceRegistration:
    """Tests for registration in STANDARD_TOOLS."""

    def test_appears_in_standard_tools(self):
        names = [t.name for t in STANDARD_TOOLS]
        assert "recall_source" in names

    def test_definition_defaults(self):
        """The registered definition has correct default attributes."""
        defn = next(t for t in STANDARD_TOOLS if t.name == "recall_source")
        assert defn.is_default is True
        assert defn.built_in is True
        assert defn.enabled is True
        assert defn.group_name == "standard"
        assert defn.invocation_cost == 0.0
        assert defn.call_limit_per_run == 10
        assert defn.call_limit_per_step == 5

    def test_implementation_points_to_class(self):
        defn = next(t for t in STANDARD_TOOLS if t.name == "recall_source")
        assert defn.implementation == ("moira.tools.builtin.recall_source.RecallSourceTool")
