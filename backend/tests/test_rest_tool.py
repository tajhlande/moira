"""Tests for the RESTTool module."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moira.tools.base import ToolDefinition
from moira.tools.rest_tool import RESTTool


def _rest_tool(
    config: dict,
    name: str = "test_tool",
    argument_schema: dict | None = None,
) -> RESTTool:
    defn = ToolDefinition(
        name=name,
        description="Test tool",
        implementation="moira.tools.rest_tool.RESTTool",
        config=config,
        argument_schema=argument_schema or {},
    )
    return RESTTool(defn)


class TestRESTToolPathResolution:
    def test_single_path_param(self):
        tool = _rest_tool(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/weather/{city}",
                "method": "GET",
                "parameters": [
                    {"name": "city", "location": "path", "required": True},
                ],
            }
        )
        args = {"city": "London"}
        url = tool._resolve_path(
            "https://api.example.com/weather/{city}", args, tool.definition.config
        )
        assert url == "https://api.example.com/weather/London"
        assert "city" not in args

    def test_multiple_path_params(self):
        tool = _rest_tool(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/repos/{owner}/{repo}/issues",
                "method": "GET",
                "parameters": [
                    {"name": "owner", "location": "path", "required": True},
                    {"name": "repo", "location": "path", "required": True},
                ],
            }
        )
        args = {"owner": "octocat", "repo": "hello-world"}
        url = tool._resolve_path(
            "https://api.example.com/repos/{owner}/{repo}/issues",
            args,
            tool.definition.config,
        )
        assert url == "https://api.example.com/repos/octocat/hello-world/issues"
        assert args == {}

    def test_undocumented_path_param_substituted(self):
        """Placeholders not in spec parameters are still substituted."""
        tool = _rest_tool(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/items/{id}",
                "method": "GET",
                "parameters": [],
            }
        )
        args = {"id": "42"}
        url = tool._resolve_path(
            "https://api.example.com/items/{id}", args, tool.definition.config
        )
        assert url == "https://api.example.com/items/42"
        assert "id" not in args


class TestRESTToolArgCategorization:
    def test_query_params_isolated(self):
        tool = _rest_tool(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/search",
                "method": "GET",
                "parameters": [
                    {"name": "q", "location": "query", "required": True},
                    {"name": "limit", "location": "query", "required": False},
                ],
            }
        )
        args = {"q": "cats", "limit": 10}
        query, body, headers = tool._categorize_args(args, tool.definition.config)
        assert query == {"q": "cats", "limit": 10}
        assert body == {}
        assert headers == {}

    def test_header_params_isolated(self):
        tool = _rest_tool(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/data",
                "method": "GET",
                "parameters": [
                    {"name": "X-Custom-Header", "location": "header", "required": True},
                    {"name": "page", "location": "query", "required": False},
                ],
            }
        )
        args = {"X-Custom-Header": "value123", "page": 2}
        query, body, headers = tool._categorize_args(args, tool.definition.config)
        assert query == {"page": 2}
        assert body == {}
        assert headers == {"X-Custom-Header": "value123"}

    def test_body_params_for_post(self):
        tool = _rest_tool(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/orders",
                "method": "POST",
                "parameters": [
                    {"name": "item", "location": "query", "required": False},
                ],
            }
        )
        args = {"item": "widget", "quantity": 5, "price": 9.99}
        query, body, headers = tool._categorize_args(args, tool.definition.config)
        assert query == {"item": "widget"}
        assert body == {"quantity": 5, "price": 9.99}
        assert headers == {}

    def test_no_location_defaults_to_query_for_get(self):
        tool = _rest_tool(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/search",
                "method": "GET",
                "parameters": [],
            }
        )
        args = {"q": "test"}
        query, body, headers = tool._categorize_args(args, tool.definition.config)
        assert query == {"q": "test"}
        assert body == {}

    def test_no_location_defaults_to_body_for_post(self):
        tool = _rest_tool(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/items",
                "method": "POST",
                "parameters": [],
            }
        )
        args = {"name": "widget"}
        query, body, headers = tool._categorize_args(args, tool.definition.config)
        assert query == {}
        assert body == {"name": "widget"}

    def test_schema_x_parameter_location(self):
        """x-parameter-location in argument_schema is respected."""
        tool = _rest_tool(
            config={
                "base_url": "https://api.example.com",
                "endpoint": "/data",
                "method": "GET",
                "parameters": [],
            },
            argument_schema={
                "type": "object",
                "properties": {
                    "q": {"type": "string", "x-parameter-location": "query"},
                    "X-Token": {"type": "string", "x-parameter-location": "header"},
                },
            },
        )
        args = {"q": "search", "X-Token": "abc"}
        query, body, headers = tool._categorize_args(args, tool.definition.config)
        assert query == {"q": "search"}
        assert headers == {"X-Token": "abc"}

    def test_path_params_excluded_from_categorization(self):
        tool = _rest_tool(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/items/{id}",
                "method": "GET",
                "parameters": [
                    {"name": "id", "location": "path", "required": True},
                    {"name": "fields", "location": "query", "required": False},
                ],
            }
        )
        args = {"fields": "name,price"}
        query, body, headers = tool._categorize_args(args, tool.definition.config)
        assert "id" not in query
        assert "id" not in body
        assert query == {"fields": "name,price"}


class TestRESTToolArgsPreservation:
    """RESTTool.execute() must not mutate the caller's args dict.

    _resolve_path pops consumed path params from args.  Before the fix,
    this mutated the same dict that research.py re-reads for the
    tool_result event, causing args to appear as empty {} in the UI.
    """

    @pytest.mark.asyncio
    async def test_execute_preserves_path_param_in_caller_args(self):
        tool = _rest_tool(
            {
                "base_url": "http://nonexistent.example.invalid",
                "endpoint": "/type/{type}",
                "method": "GET",
                "parameters": [
                    {"name": "type", "location": "path", "required": True},
                ],
            },
            name="pokeapi__type_retrieve",
        )
        original_args = {"type": "fire"}
        await tool.execute(original_args)
        assert original_args == {"type": "fire"}


class TestJsonTruncation:
    """Tests for _truncate_json_value and _serialize_json_truncated."""

    def test_small_object_unchanged(self):
        from moira.tools.rest_tool import _serialize_json_truncated

        data = {"name": "fire", "id": 2, "damage": {"double": ["water"]}}
        result = _serialize_json_truncated(data)
        assert json.loads(result) == data

    def test_long_array_truncated(self):
        from moira.tools.rest_tool import _truncate_json_value

        arr = list(range(50))
        result = _truncate_json_value(arr, max_array_items=5, max_string_len=200)
        assert len(result) == 6  # 5 items + 1 marker
        assert result[:5] == [0, 1, 2, 3, 4]
        assert "more items" in result[5]

    def test_long_string_truncated(self):
        from moira.tools.rest_tool import _truncate_json_value

        s = "x" * 500
        result = _truncate_json_value(s, max_array_items=10, max_string_len=100)
        assert len(result) < 200
        assert result.startswith("x" * 100)
        assert "more chars" in result

    def test_dict_keys_preserved(self):
        from moira.tools.rest_tool import _truncate_json_value

        data = {
            "name": "fire",
            "pokemon": [{"name": f"p{i}"} for i in range(30)],
            "moves": [{"name": f"m{i}"} for i in range(20)],
        }
        result = _truncate_json_value(data, max_array_items=5, max_string_len=200)
        assert set(result.keys()) == {"name", "pokemon", "moves"}
        assert len(result["pokemon"]) == 6  # 5 + marker
        assert len(result["moves"]) == 6

    def test_nested_arrays_truncated(self):
        from moira.tools.rest_tool import _truncate_json_value

        data = {
            "outer": [
                {"inner": list(range(30))},
                {"inner": list(range(30))},
            ],
        }
        result = _truncate_json_value(data, max_array_items=10, max_string_len=200)
        assert len(result["outer"]) == 2
        assert len(result["outer"][0]["inner"]) == 11  # 10 + marker

    def test_scalars_unchanged(self):
        from moira.tools.rest_tool import _truncate_json_value

        data = {"i": 42, "f": 3.14, "b": True, "n": None, "s": "short"}
        result = _truncate_json_value(data)
        assert result == data

    def test_serialize_fits_budget(self):
        """Large JSON should be truncated to fit within the budget."""
        from moira.tools.rest_tool import (
            _MAX_OUTPUT_CHARS,
            _serialize_json_truncated,
        )

        data = {
            "name": "fire",
            "pokemon": [
                {"name": f"pokemon_{i}", "url": f"https://pokeapi.co/api/v2/pokemon/{i}/"}
                for i in range(500)
            ],
        }
        full = json.dumps(data, indent=2)
        assert len(full) > _MAX_OUTPUT_CHARS

        result = _serialize_json_truncated(data)
        assert len(result) <= _MAX_OUTPUT_CHARS
        # Result must be valid JSON
        parsed = json.loads(result)
        assert parsed["name"] == "fire"
        # Pokemon array should be truncated with a marker
        last = parsed["pokemon"][-1]
        assert isinstance(last, str)
        assert "more items" in last

    def test_serialize_preserves_important_fields(self):
        """Damage relations (small dict) should survive truncation."""
        from moira.tools.rest_tool import _serialize_json_truncated

        damage_relations = {
            "double_damage_to": [{"name": "grass", "url": "..."}],
            "half_damage_to": [{"name": "fire", "url": "..."}],
            "no_damage_to": [{"name": "water", "url": "..."}],
        }
        data = {
            "damage_relations": damage_relations,
            "pokemon": [{"name": f"p{i}"} for i in range(1000)],
            "moves": [{"name": f"m{i}"} for i in range(200)],
        }
        result = _serialize_json_truncated(data)
        parsed = json.loads(result)
        assert parsed["damage_relations"] == damage_relations
        assert "more items" in parsed["pokemon"][-1]


class TestRESTToolMetadata:
    """Tests for structured metadata output (URL + title in results).

    RESTTool must populate ``metadata["results"]`` so that
    _process_execution_results creates citations with url + title via
    Path A, not bare Path B citations with no URL or title.
    """

    @staticmethod
    def _mock_response(json_data: dict) -> MagicMock:
        """Create a mock httpx response that returns json_data."""
        resp = MagicMock()
        resp.json.return_value = json_data
        resp.raise_for_status = MagicMock()
        return resp

    @staticmethod
    def _mock_client(resp: MagicMock) -> AsyncMock:
        """Create a mock AsyncClient whose request() returns resp."""
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.request = AsyncMock(return_value=resp)
        return client

    @pytest.mark.asyncio
    async def test_success_returns_url_and_title(self):
        """A successful REST call surfaces the resolved URL and a derived
        title in metadata["results"]."""
        tool = _rest_tool(
            {
                "base_url": "https://pokeapi.co/api/v2",
                "endpoint": "/type/{id}",
                "method": "GET",
                "parameters": [
                    {"name": "id", "location": "path", "required": True},
                ],
            },
            name="pokeapi__type_retrieve",
        )
        resp = self._mock_response({"name": "rock", "id": 6})
        client = self._mock_client(resp)

        with patch("moira.tools.rest_tool.httpx.AsyncClient", return_value=client):
            result = await tool.execute({"id": "rock"})

        assert result.success is True
        results = result.metadata.get("results", [])
        assert len(results) == 1
        assert results[0]["url"] == "https://pokeapi.co/api/v2/type/rock"
        assert "pokeapi__type_retrieve" in results[0]["title"]
        assert "rock" in results[0]["title"]

    @pytest.mark.asyncio
    async def test_title_includes_path_param(self):
        """The title must include the path param value, which is consumed
        by _resolve_path before the title is used — so the title must be
        derived before _resolve_path runs."""
        tool = _rest_tool(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/pokemon/{name}/stats",
                "method": "GET",
                "parameters": [
                    {"name": "name", "location": "path", "required": True},
                ],
            },
            name="pokeapi__pokemon_stats",
        )
        resp = self._mock_response({"stats": []})
        client = self._mock_client(resp)

        with patch("moira.tools.rest_tool.httpx.AsyncClient", return_value=client):
            result = await tool.execute({"name": "tyranitar"})

        title = result.metadata["results"][0]["title"]
        assert "tyranitar" in title

    @pytest.mark.asyncio
    async def test_title_filters_sensitive_args(self):
        """API keys and tokens must not appear in the citation title."""
        tool = _rest_tool(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/data",
                "method": "GET",
                "parameters": [],
            },
            name="data_fetch",
        )
        resp = self._mock_response({"data": "ok"})
        client = self._mock_client(resp)

        with patch("moira.tools.rest_tool.httpx.AsyncClient", return_value=client):
            result = await tool.execute({"api_key": "SECRET123", "query": "test"})

        title = result.metadata["results"][0]["title"]
        assert "SECRET123" not in title
        assert "api_key" not in title
        assert "query=test" in title

    @pytest.mark.asyncio
    async def test_failure_returns_empty_results(self):
        """A failed REST call returns metadata with empty results list,
        matching web_search/url_content convention (no noise citation)."""
        tool = _rest_tool(
            {
                "base_url": "http://nonexistent.example.invalid",
                "endpoint": "/type/{id}",
                "method": "GET",
                "parameters": [
                    {"name": "id", "location": "path", "required": True},
                ],
            },
            name="pokeapi__type_retrieve",
        )

        result = await tool.execute({"id": "rock"})

        assert result.success is False
        assert result.metadata.get("results") == []

    @pytest.mark.asyncio
    async def test_snippet_and_content_populated(self):
        """The metadata results include snippet and content for citation
        creation via Path A."""
        tool = _rest_tool(
            {
                "base_url": "https://api.example.com",
                "endpoint": "/data",
                "method": "GET",
                "parameters": [],
            },
            name="data_fetch",
        )
        resp = self._mock_response({"result": "value", "count": 42})
        client = self._mock_client(resp)

        with patch("moira.tools.rest_tool.httpx.AsyncClient", return_value=client):
            result = await tool.execute({})

        entry = result.metadata["results"][0]
        assert entry["snippet"]
        assert entry["content"]
        assert "result" in entry["content"]
