import pytest

from moira.config import ToolConfig
from moira.tools.base import ToolDefinition, ToolResult
from moira.tools.catalog import ToolCatalog
from moira.tools.executor import ToolExecutor


@pytest.fixture
def sample_tools():
    return [
        ToolConfig(
            name="web_search",
            description="Search the web for information on any topic",
            type="rest",
            endpoint="https://api.example.com/search",
            method="POST",
            argument_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            tags=["web", "search"],
            reliability="high",
        ),
        ToolConfig(
            name="paper_search",
            description="Search academic papers and scientific publications",
            type="rest",
            endpoint="https://api.example.com/papers",
            method="GET",
            argument_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            tags=["academic", "papers"],
            reliability="high",
        ),
    ]


class TestToolCatalog:
    def test_load_from_config(self, sample_tools):
        catalog = ToolCatalog()
        catalog.load_from_config(sample_tools)
        assert len(catalog.get_all()) == 2

    def test_get_by_name(self, sample_tools):
        catalog = ToolCatalog()
        catalog.load_from_config(sample_tools)
        tool = catalog.get("web_search")
        assert tool is not None
        assert tool.name == "web_search"

    def test_get_missing_returns_none(self, sample_tools):
        catalog = ToolCatalog()
        catalog.load_from_config(sample_tools)
        assert catalog.get("nonexistent") is None

    def test_get_by_names(self, sample_tools):
        catalog = ToolCatalog()
        catalog.load_from_config(sample_tools)
        tools = catalog.get_by_names(["web_search", "nonexistent"])
        assert len(tools) == 1
        assert tools[0].name == "web_search"

    def test_empty_catalog(self):
        catalog = ToolCatalog()
        assert len(catalog.get_all()) == 0

    def test_tool_definition_to_dict(self):
        defn = ToolDefinition(
            name="test",
            description="A test tool",
            tags=["test"],
        )
        d = defn.to_dict()
        assert d["name"] == "test"
        assert d["tags"] == ["test"]


class TestToolExecutor:
    async def test_execute_missing_tool(self):
        executor = ToolExecutor()
        r = await executor.execute("nonexistent", {})
        assert r.success is False
        assert "not found" in r.error

    def test_register_rest_tool(self):
        executor = ToolExecutor()
        defn = ToolDefinition(
            name="test",
            description="test",
            implementation="moira.tools.builtin.calculator.CalculatorTool",
            config={},
        )
        executor.register_tools([defn])
        assert "test" in executor._tool_instances


class TestFormatToolDescriptions:
    def test_basic_description(self):
        from moira.workflow.nodes.research_nodes import _format_tool_descriptions

        tools = [
            ToolDefinition(name="calc", description="A calculator"),
        ]
        result = _format_tool_descriptions(tools)
        assert "- calc: A calculator" in result

    def test_with_argument_schema(self):
        from moira.workflow.nodes.research_nodes import _format_tool_descriptions

        tools = [
            ToolDefinition(
                name="web_search",
                description="Search the web",
                argument_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max results",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            ),
        ]
        result = _format_tool_descriptions(tools)
        assert "- web_search: Search the web" in result
        assert "query (string, required): The search query" in result
        assert "max_results (integer, optional, default: 5): Max results" in result
        assert "Parameters:" in result

    def test_no_schema(self):
        from moira.workflow.nodes.research_nodes import _format_tool_descriptions

        tools = [ToolDefinition(name="x", description="desc")]
        result = _format_tool_descriptions(tools)
        assert "Parameters" not in result

    def test_empty_tools(self):
        from moira.workflow.nodes.research_nodes import _format_tool_descriptions

        result = _format_tool_descriptions([])
        assert result == ""

    def test_uses_builtin_tool_specs(self):
        """Verify the helper works with actual built-in tool definitions."""
        from moira.tools.builtin.calculator import CalculatorTool
        from moira.tools.builtin.url_content import UrlContentTool
        from moira.workflow.nodes.research_nodes import _format_tool_descriptions

        tools = [
            CalculatorTool.make_definition(),
            UrlContentTool.make_definition(),
        ]
        result = _format_tool_descriptions(tools)
        assert "- calculator:" in result
        assert "expression (string, required)" in result
        assert "- url_content:" in result
        assert "url (string, required)" in result
        assert "text_only (boolean, optional" in result


class TestToolResult:
    def test_success_result(self):
        result = ToolResult(
            tool_name="test",
            output="hello",
            success=True,
            duration_ms=100,
        )
        assert result.success is True
        assert result.output == "hello"

    def test_failure_result(self):
        result = ToolResult(
            tool_name="test",
            output="",
            success=False,
            error="timeout",
        )
        assert result.success is False
        assert result.error == "timeout"
