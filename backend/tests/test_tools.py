import pytest

from moira.config import ToolConfig
from moira.tools.base import ToolCall, ToolDefinition, ToolResult
from moira.tools.catalog import ToolCatalog
from moira.tools.executor import ToolExecutor


@pytest.fixture
def sample_tools():
    return [
        ToolConfig(
            name="web_search",
            description="Search the web for information on any topic",
            endpoint="https://api.example.com/search",
            method="POST",
            argument_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            tags=["web", "search"],
            reliability="high",
        ),
        ToolConfig(
            name="paper_search",
            description="Search academic papers and scientific publications",
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


class TestToolCall:
    def test_basic_construction(self):
        call = ToolCall(id="call_1", name="web_search", arguments={"query": "test"})
        assert call.id == "call_1"
        assert call.name == "web_search"
        assert call.arguments == {"query": "test"}

    def test_arguments_default_empty(self):
        call = ToolCall(id="call_1", name="calculator")
        assert call.arguments == {}

    def test_id_required_at_construction(self):
        """Omitting id entirely must raise — the field has no default."""
        with pytest.raises(TypeError):
            ToolCall(name="web_search", arguments={})  # type: ignore

    def test_id_rejects_empty_string(self):
        with pytest.raises(ValueError, match="non-empty"):
            ToolCall(id="", name="web_search", arguments={})

    def test_id_rejects_whitespace_only(self):
        with pytest.raises(ValueError, match="non-empty"):
            ToolCall(id="   ", name="web_search", arguments={})

    def test_equality(self):
        a = ToolCall(id="call_1", name="web_search", arguments={"q": "x"})
        b = ToolCall(id="call_1", name="web_search", arguments={"q": "x"})
        c = ToolCall(id="call_2", name="web_search", arguments={"q": "x"})
        assert a == b
        assert a != c
