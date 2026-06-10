"""Tests for spec_parser, auth_analyzer, spec_resolver, and RESTTool."""

import json

import pytest

from moira.services.tool_ingestion.auth_analyzer import (
    extract_security_schemes,
    get_auth_type_for_spec,
)
from moira.services.tool_ingestion.spec_parser import (
    _derive_method_id,
    _sanitize_operation_id,
    derive_group_slug,
    generate_tool_name,
    parse_spec_content,
)
from moira.services.tool_ingestion.spec_resolver import _validate_spec_text
from moira.tools.base import ToolDefinition
from moira.tools.rest_tool import RESTTool

# ---- Minimal OpenAPI 3.0 spec fixtures ----


def _openapi_spec(
    paths=None,
    security_schemes=None,
    security=None,
    servers=None,
) -> dict:
    """Build a minimal OpenAPI 3.0 spec dict."""
    spec: dict = {
        "openapi": "3.0.3",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": paths or {},
    }
    if servers:
        spec["servers"] = servers
    if security_schemes:
        spec.setdefault("components", {})["securitySchemes"] = security_schemes
    if security is not None:
        spec["security"] = security
    return spec


def _swagger_spec(paths=None, security_definitions=None) -> dict:
    """Build a minimal Swagger 2.0 spec dict."""
    spec: dict = {
        "swagger": "2.0",
        "info": {"title": "Swagger API", "version": "1.0.0"},
        "host": "api.example.com",
        "basePath": "/v1",
        "schemes": ["https"],
        "paths": paths or {},
    }
    if security_definitions:
        spec["securityDefinitions"] = security_definitions
    return spec


# ---- auth_analyzer tests ----


class TestExtractSecuritySchemes:
    def test_no_schemes(self):
        spec = _openapi_spec()
        assert extract_security_schemes(spec) == {}

    def test_bearer_scheme(self):
        spec = _openapi_spec(security_schemes={
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": "JWT auth",
            }
        })
        schemes = extract_security_schemes(spec)
        assert "bearerAuth" in schemes
        assert schemes["bearerAuth"].scheme_type == "bearer"
        assert schemes["bearerAuth"].location == "header"

    def test_api_key_header(self):
        spec = _openapi_spec(security_schemes={
            "apiKey": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
            }
        })
        schemes = extract_security_schemes(spec)
        assert schemes["apiKey"].scheme_type == "api_key_header"
        assert schemes["apiKey"].name == "X-API-Key"

    def test_api_key_query(self):
        spec = _openapi_spec(security_schemes={
            "queryKey": {
                "type": "apiKey",
                "in": "query",
                "name": "api_key",
            }
        })
        schemes = extract_security_schemes(spec)
        assert schemes["queryKey"].scheme_type == "api_key_query"
        assert schemes["queryKey"].name == "api_key"

    def test_basic_auth(self):
        spec = _openapi_spec(security_schemes={
            "basicAuth": {
                "type": "http",
                "scheme": "basic",
            }
        })
        schemes = extract_security_schemes(spec)
        assert schemes["basicAuth"].scheme_type == "basic"

    def test_oauth2_ignored(self):
        spec = _openapi_spec(security_schemes={
            "oauth": {
                "type": "oauth2",
                "flows": {"clientCredentials": {"tokenUrl": "/token", "scopes": {}}},
            }
        })
        assert extract_security_schemes(spec) == {}

    def test_swagger_2_security_definitions(self):
        spec = _swagger_spec(security_definitions={
            "apiKey": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Api-Key",
            }
        })
        schemes = extract_security_schemes(spec)
        assert "apiKey" in schemes
        assert schemes["apiKey"].scheme_type == "api_key_header"


class TestGetAuthTypeForSpec:
    def test_no_auth(self):
        spec = _openapi_spec()
        assert get_auth_type_for_spec(spec) is None

    def test_global_security(self):
        spec = _openapi_spec(
            security_schemes={"bearerAuth": {"type": "http", "scheme": "bearer"}},
            security=[{"bearerAuth": []}],
        )
        assert get_auth_type_for_spec(spec) == "bearer"

    def test_no_global_security_returns_none(self):
        spec = _openapi_spec(security_schemes={
            "apiKey": {"type": "apiKey", "in": "header", "name": "X-Key"},
        })
        assert get_auth_type_for_spec(spec) is None


# ---- spec_parser tests ----


class TestDeriveGroupSlug:
    def test_simple_name(self):
        assert derive_group_slug("Weather API") == "weather_api"

    def test_hyphens_and_spaces(self):
        assert derive_group_slug("My Cool-API") == "my_cool_api"

    def test_special_chars_stripped(self):
        assert derive_group_slug("API v2.0 (beta)") == "api_v20_beta"

    def test_truncation(self):
        long_name = "A" * 100
        assert len(derive_group_slug(long_name)) == 48

    def test_empty_returns_default(self):
        assert derive_group_slug("!!!") == "api"


class TestGenerateToolName:
    def test_with_operation_id(self):
        name = generate_tool_name("weather_api", "GET", "/current", operation_id="getCurrent")
        assert name == "weather_api__get_current"

    def test_with_path_segments(self):
        name = generate_tool_name("pokeapi", "GET", "/pokemon/{name}")
        assert name == "pokeapi__get_pokemon"

    def test_nested_path_skips_params(self):
        name = generate_tool_name("media_api", "GET", "/books/{book_id}/reviews")
        assert name == "media_api__get_books_reviews"

    def test_collision_dedup(self):
        existing = {"api__get_items"}
        name = generate_tool_name("api", "GET", "/items", existing_names=existing)
        assert name == "api__get_items_2"

    def test_multiple_collisions(self):
        existing = {"api__get_items", "api__get_items_2"}
        name = generate_tool_name("api", "GET", "/items", existing_names=existing)
        assert name == "api__get_items_3"


class TestDeriveMethodId:
    def test_simple_path(self):
        assert _derive_method_id("get", "/current") == "get_current"

    def test_param_segments_skipped(self):
        assert _derive_method_id("get", "/books/{book_id}/reviews") == "get_books_reviews"

    def test_multiple_params(self):
        assert _derive_method_id("get", "/repos/{owner}/{repo}/issues") == "get_repos_issues"

    def test_root_path(self):
        assert _derive_method_id("get", "/") == "get_root"


class TestSanitizeOperationId:
    def test_camel_case(self):
        assert _sanitize_operation_id("getCurrent") == "get_current"

    def test_pascal_case(self):
        assert _sanitize_operation_id("GetCurrentWeather") == "get_current_weather"

    def test_special_chars(self):
        assert _sanitize_operation_id("get.user-data") == "get_user_data"


class TestParseSpecContent:
    def test_minimal_openapi(self):
        spec = _openapi_spec(paths={
            "/status": {
                "get": {
                    "summary": "Health check",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        })
        parsed = parse_spec_content(json.dumps(spec))
        assert parsed.title == "Test API"
        assert parsed.version == "openapi_3_0"
        assert len(parsed.operations) == 1
        assert parsed.operations[0].method == "GET"
        assert parsed.operations[0].path == "/status"
        assert parsed.operations[0].description == "Health check"

    def test_swagger_2(self):
        spec = _swagger_spec(paths={
            "/pets": {
                "get": {
                    "summary": "List pets",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        })
        parsed = parse_spec_content(json.dumps(spec))
        assert parsed.version == "swagger_2"
        assert len(parsed.operations) == 1
        assert parsed.server_urls == ["https://api.example.com/v1"]

    def test_parameters_extracted(self):
        spec = _openapi_spec(paths={
            "/weather": {
                "get": {
                    "summary": "Get weather",
                    "parameters": [
                        {
                            "name": "city",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "City name",
                        },
                        {
                            "name": "unit",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "default": "celsius"},
                            "description": "Temperature unit",
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        })
        parsed = parse_spec_content(json.dumps(spec))
        op = parsed.operations[0]
        assert len(op.parameters) == 2
        assert op.parameters[0].name == "city"
        assert op.parameters[0].location == "query"
        assert op.parameters[0].required is True
        assert op.parameters[1].name == "unit"
        assert op.parameters[1].required is False

    def test_request_body_extracted(self):
        spec = _openapi_spec(paths={
            "/orders": {
                "post": {
                    "summary": "Create order",
                    "requestBody": {
                        "required": True,
                        "description": "Order payload",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"item": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "responses": {"201": {"description": "Created"}},
                }
            }
        })
        parsed = parse_spec_content(json.dumps(spec))
        op = parsed.operations[0]
        assert op.request_body is not None
        assert op.request_body.content_type == "application/json"
        assert op.request_body.required is True
        assert "item" in op.request_body.schema_def.get("properties", {})

    def test_path_level_parameters_merged(self):
        spec = _openapi_spec(paths={
            "/items/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "get": {
                    "summary": "Get item",
                    "parameters": [
                        {"name": "fields", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
            }
        })
        parsed = parse_spec_content(json.dumps(spec))
        op = parsed.operations[0]
        param_names = [p.name for p in op.parameters]
        assert "id" in param_names
        assert "fields" in param_names

    def test_deprecated_flag(self):
        spec = _openapi_spec(paths={
            "/old": {
                "get": {
                    "summary": "Deprecated endpoint",
                    "deprecated": True,
                    "responses": {"200": {"description": "OK"}},
                }
            }
        })
        parsed = parse_spec_content(json.dumps(spec))
        assert parsed.operations[0].deprecated is True

    def test_security_requirements(self):
        spec = _openapi_spec(
            security_schemes={"bearerAuth": {"type": "http", "scheme": "bearer"}},
            security=[{"bearerAuth": []}],
            paths={
                "/me": {
                    "get": {
                        "summary": "Get current user",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        )
        parsed = parse_spec_content(json.dumps(spec))
        assert "bearerAuth" in parsed.operations[0].security_requirements

    def test_multiple_operations(self):
        spec = _openapi_spec(paths={
            "/items": {
                "get": {"summary": "List", "responses": {"200": {"description": "OK"}}},
                "post": {"summary": "Create", "responses": {"201": {"description": "Created"}}},
            },
            "/items/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "get": {"summary": "Get", "responses": {"200": {"description": "OK"}}},
                "delete": {
                    "summary": "Delete",
                    "responses": {"204": {"description": "No content"}},
                },
            },
        })
        parsed = parse_spec_content(json.dumps(spec))
        assert len(parsed.operations) == 4
        methods = {op.method for op in parsed.operations}
        assert methods == {"GET", "POST", "DELETE"}

    def test_server_urls_extracted(self):
        spec = _openapi_spec(
            servers=[
                {"url": "https://api.example.com/v1"},
                {"url": "https://staging.example.com/v1"},
            ],
        )
        parsed = parse_spec_content(json.dumps(spec))
        assert parsed.server_urls == [
            "https://api.example.com/v1",
            "https://staging.example.com/v1",
        ]

    def test_multiple_operations_same_path_tail_deduped(self):
        spec = _openapi_spec(
            paths={
                "/books/{book_id}/reviews": {
                    "parameters": [
                        {
                            "name": "book_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "get": {
                        "summary": "List reviews",
                        "responses": {"200": {"description": "OK"}},
                    },
                },
                "/movies/{movie_id}/reviews": {
                    "parameters": [
                        {
                            "name": "movie_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "get": {
                        "summary": "List movie reviews",
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            }
        )
        parsed = parse_spec_content(json.dumps(spec))
        assert len(parsed.operations) == 2
        names = {op.name for op in parsed.operations}
        assert len(names) == 2
        # Both should have distinct names
        assert "api__get_books_reviews" in names
        assert "api__get_movies_reviews" in names


# ---- spec_resolver validation tests ----


class TestValidateSpecText:
    def test_valid_json_openapi(self):
        text = json.dumps({"openapi": "3.0.3", "info": {"title": "T", "version": "1"}})
        _validate_spec_text(text)  # should not raise

    def test_valid_json_swagger(self):
        text = json.dumps({"swagger": "2.0", "info": {"title": "T", "version": "1"}})
        _validate_spec_text(text)  # should not raise

    def test_invalid_json(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            _validate_spec_text("{not valid json")

    def test_valid_yaml_openapi(self):
        _validate_spec_text("openapi: '3.0.3'\ninfo:\n  title: Test\n  version: '1'")

    def test_valid_yaml_swagger(self):
        _validate_spec_text("swagger: '2.0'\ninfo:\n  title: Test\n  version: '1'")

    def test_neither_openapi_nor_swagger(self):
        with pytest.raises(ValueError, match="openapi.*swagger"):
            _validate_spec_text('{"title": "not a spec"}')

    def test_plain_text_rejected(self):
        with pytest.raises(ValueError, match="does not appear"):
            _validate_spec_text("this is just text")


# ---- RESTTool enhancement tests ----


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
        tool = _rest_tool({
            "base_url": "https://api.example.com",
            "endpoint": "/weather/{city}",
            "method": "GET",
            "parameters": [
                {"name": "city", "location": "path", "required": True},
            ],
        })
        args = {"city": "London"}
        url = tool._resolve_path(
            "https://api.example.com/weather/{city}", args, tool.definition.config
        )
        assert url == "https://api.example.com/weather/London"
        assert "city" not in args

    def test_multiple_path_params(self):
        tool = _rest_tool({
            "base_url": "https://api.example.com",
            "endpoint": "/repos/{owner}/{repo}/issues",
            "method": "GET",
            "parameters": [
                {"name": "owner", "location": "path", "required": True},
                {"name": "repo", "location": "path", "required": True},
            ],
        })
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
        tool = _rest_tool({
            "base_url": "https://api.example.com",
            "endpoint": "/items/{id}",
            "method": "GET",
            "parameters": [],
        })
        args = {"id": "42"}
        url = tool._resolve_path(
            "https://api.example.com/items/{id}", args, tool.definition.config
        )
        assert url == "https://api.example.com/items/42"
        assert "id" not in args


class TestRESTToolArgCategorization:
    def test_query_params_isolated(self):
        tool = _rest_tool({
            "base_url": "https://api.example.com",
            "endpoint": "/search",
            "method": "GET",
            "parameters": [
                {"name": "q", "location": "query", "required": True},
                {"name": "limit", "location": "query", "required": False},
            ],
        })
        args = {"q": "cats", "limit": 10}
        query, body, headers = tool._categorize_args(args, tool.definition.config)
        assert query == {"q": "cats", "limit": 10}
        assert body == {}
        assert headers == {}

    def test_header_params_isolated(self):
        tool = _rest_tool({
            "base_url": "https://api.example.com",
            "endpoint": "/data",
            "method": "GET",
            "parameters": [
                {"name": "X-Custom-Header", "location": "header", "required": True},
                {"name": "page", "location": "query", "required": False},
            ],
        })
        args = {"X-Custom-Header": "value123", "page": 2}
        query, body, headers = tool._categorize_args(args, tool.definition.config)
        assert query == {"page": 2}
        assert body == {}
        assert headers == {"X-Custom-Header": "value123"}

    def test_body_params_for_post(self):
        tool = _rest_tool({
            "base_url": "https://api.example.com",
            "endpoint": "/orders",
            "method": "POST",
            "parameters": [
                {"name": "item", "location": "query", "required": False},
            ],
        })
        args = {"item": "widget", "quantity": 5, "price": 9.99}
        query, body, headers = tool._categorize_args(args, tool.definition.config)
        assert query == {"item": "widget"}
        assert body == {"quantity": 5, "price": 9.99}
        assert headers == {}

    def test_no_location_defaults_to_query_for_get(self):
        tool = _rest_tool({
            "base_url": "https://api.example.com",
            "endpoint": "/search",
            "method": "GET",
            "parameters": [],
        })
        args = {"q": "test"}
        query, body, headers = tool._categorize_args(args, tool.definition.config)
        assert query == {"q": "test"}
        assert body == {}

    def test_no_location_defaults_to_body_for_post(self):
        tool = _rest_tool({
            "base_url": "https://api.example.com",
            "endpoint": "/items",
            "method": "POST",
            "parameters": [],
        })
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
        tool = _rest_tool({
            "base_url": "https://api.example.com",
            "endpoint": "/items/{id}",
            "method": "GET",
            "parameters": [
                {"name": "id", "location": "path", "required": True},
                {"name": "fields", "location": "query", "required": False},
            ],
        })
        args = {"fields": "name,price"}
        query, body, headers = tool._categorize_args(args, tool.definition.config)
        assert "id" not in query
        assert "id" not in body
        assert query == {"fields": "name,price"}


# ---- Tool Provisioner tests ----


from unittest.mock import AsyncMock, MagicMock, patch

from moira.services.tool_ingestion.tool_provisioner import (
    REST_TOOL_IMPL,
    ProvisionResult,
    ToolProvisioner,
    build_argument_schema,
)
from moira.persistence.interfaces import ApiSource


class TestBuildArgumentSchema:
    """Tests for the build_argument_schema helper."""

    def test_empty_params(self):
        schema = build_argument_schema([], None)
        assert schema == {"type": "object", "properties": {}, "required": []}

    def test_params_with_locations(self):
        params = [
            {"name": "city", "location": "query", "required": True, "schema_def": {"type": "string"}, "description": "City name"},
            {"name": "X-Api-Key", "location": "header", "required": True, "schema_def": {"type": "string"}, "description": "API key"},
        ]
        schema = build_argument_schema(params, None)
        assert schema["properties"]["city"]["x-parameter-location"] == "query"
        assert schema["properties"]["X-Api-Key"]["x-parameter-location"] == "header"
        assert "city" in schema["required"]
        assert "X-Api-Key" in schema["required"]

    def test_body_schema_merged(self):
        body_schema = {
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        schema = build_argument_schema([], body_schema)
        assert "name" in schema["properties"]
        assert "age" in schema["properties"]
        assert "name" in schema["required"]

    def test_param_takes_precedence_over_body(self):
        """If a param and body property share a name, param wins."""
        params = [
            {"name": "name", "location": "query", "required": False, "schema_def": {"type": "string"}, "description": "Name"},
        ]
        body_schema = {
            "properties": {"name": {"type": "integer"}},
            "required": [],
        }
        schema = build_argument_schema(params, body_schema)
        # Param's definition wins (it has x-parameter-location)
        assert schema["properties"]["name"]["x-parameter-location"] == "query"


def _mock_source_repo():
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=None)
    repo.get_all = AsyncMock(return_value=[])
    repo.save = AsyncMock()
    repo.delete = AsyncMock(return_value=True)
    repo.update_tool_count = AsyncMock()
    return repo


def _mock_tool_repo():
    repo = AsyncMock()
    repo.get_all_tools = AsyncMock(return_value=[])
    repo.get_tool = AsyncMock(return_value=None)
    repo.save_tool = AsyncMock()
    repo.delete_tool = AsyncMock(return_value=True)
    repo.save_group = AsyncMock()
    return repo


def _sample_operations():
    return [
        {
            "name": "test_api__get_items",
            "description": "Get all items",
            "method": "GET",
            "path": "/items",
            "parameters": [
                {"name": "limit", "location": "query", "required": False, "schema_def": {"type": "integer"}, "description": "Max items"},
            ],
            "request_body": None,
            "tags": ["items"],
            "deprecated": False,
            "security_requirements": [],
            "operation_id": "getItems",
        },
        {
            "name": "test_api__get_item",
            "description": "Get a single item",
            "method": "GET",
            "path": "/items/{id}",
            "parameters": [
                {"name": "id", "location": "path", "required": True, "schema_def": {"type": "string"}, "description": "Item ID"},
            ],
            "request_body": None,
            "tags": ["items"],
            "deprecated": False,
            "security_requirements": [],
            "operation_id": "getItem",
        },
    ]


class TestToolProvisionerCommit:
    """Tests for the provisioner's commit phase."""

    @pytest.mark.asyncio
    async def test_commit_creates_tools(self):
        source_repo = _mock_source_repo()
        tool_repo = _mock_tool_repo()

        with patch("moira.services.tool_ingestion.tool_provisioner.service_provider") as mock_sp:
            mock_sp.return_value = None
            provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)

            result = await provisioner.commit(
                source_id="src-123",
                base_url="https://api.example.com",
                spec_url="https://api.example.com/openapi.json",
                spec_format="openapi_3_0",
                group_name="Test API",
                auth_type=None,
                selected_operations=["test_api__get_items"],
                operations_data=_sample_operations(),
                server_url="https://api.example.com/v1",
            )

        assert result.succeeded == ["test_api__get_items"]
        assert result.failed == []
        assert result.disabled == []
        assert tool_repo.save_tool.call_count == 1
        tool_repo.save_group.assert_called_once_with({
            "name": "test_api",
            "display_name": "Test API",
        })
        source_repo.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_commit_auth_required_disables_tools(self):
        source_repo = _mock_source_repo()
        tool_repo = _mock_tool_repo()

        with patch("moira.services.tool_ingestion.tool_provisioner.service_provider") as mock_sp:
            mock_sp.return_value = None
            provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)

            result = await provisioner.commit(
                source_id="src-456",
                base_url="https://api.example.com",
                spec_url=None,
                spec_format="openapi_3_0",
                group_name="Auth API",
                auth_type="bearer",
                selected_operations=["test_api__get_items"],
                operations_data=_sample_operations(),
                server_url="https://api.example.com",
            )

        assert result.succeeded == ["test_api__get_items"]
        assert result.disabled == ["test_api__get_items"]
        saved_tool = tool_repo.save_tool.call_args[0][0]
        assert saved_tool.enabled is False
        assert saved_tool.config["auth_type"] == "bearer"
        assert "credential_ref" in saved_tool.config

    @pytest.mark.asyncio
    async def test_commit_unknown_operation_fails(self):
        source_repo = _mock_source_repo()
        tool_repo = _mock_tool_repo()

        with patch("moira.services.tool_ingestion.tool_provisioner.service_provider") as mock_sp:
            mock_sp.return_value = None
            provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)

            result = await provisioner.commit(
                source_id="src-789",
                base_url="https://api.example.com",
                spec_url=None,
                spec_format="openapi_3_0",
                group_name="Test API",
                auth_type=None,
                selected_operations=["nonexistent_tool"],
                operations_data=_sample_operations(),
                server_url="https://api.example.com",
            )

        assert result.succeeded == []
        assert len(result.failed) == 1
        assert result.failed[0]["name"] == "nonexistent_tool"

    @pytest.mark.asyncio
    async def test_commit_exceeds_max_operations(self):
        source_repo = _mock_source_repo()
        tool_repo = _mock_tool_repo()
        provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)

        with pytest.raises(ValueError, match="Too many operations"):
            await provisioner.commit(
                source_id="src-max",
                base_url="https://api.example.com",
                spec_url=None,
                spec_format="openapi_3_0",
                group_name="Big API",
                auth_type=None,
                selected_operations=["op"] * 201,
                operations_data=[],
                server_url="https://api.example.com",
            )

    @pytest.mark.asyncio
    async def test_commit_tool_definition_fields(self):
        """Verify the generated ToolDefinition has correct fields."""
        source_repo = _mock_source_repo()
        tool_repo = _mock_tool_repo()

        with patch("moira.services.tool_ingestion.tool_provisioner.service_provider") as mock_sp:
            mock_sp.return_value = None
            provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)

            await provisioner.commit(
                source_id="src-fields",
                base_url="https://api.example.com",
                spec_url=None,
                spec_format="openapi_3_0",
                group_name="Fields API",
                auth_type=None,
                selected_operations=["test_api__get_item"],
                operations_data=_sample_operations(),
                server_url="https://api.example.com/v2",
            )

        saved_tool: ToolDefinition = tool_repo.save_tool.call_args[0][0]
        assert saved_tool.name == "test_api__get_item"
        assert saved_tool.implementation == REST_TOOL_IMPL
        assert saved_tool.config["base_url"] == "https://api.example.com/v2"
        assert saved_tool.config["endpoint"] == "/items/{id}"
        assert saved_tool.config["method"] == "GET"
        assert saved_tool.config["source_id"] == "src-fields"
        assert saved_tool.config["operation_fingerprint"] == "GET:/items/{id}"
        assert saved_tool.built_in is False
        assert "id" in saved_tool.argument_schema["properties"]
        assert saved_tool.argument_schema["properties"]["id"]["x-parameter-location"] == "path"


class TestToolProvisionerDelete:
    """Tests for deleting an API source and its tools."""

    @pytest.mark.asyncio
    async def test_delete_source_removes_tools(self):
        source_repo = _mock_source_repo()
        source_repo.get = AsyncMock(return_value=ApiSource(
            id="src-del",
            name="Delete API",
            base_url="https://api.example.com",
            spec_url=None,
            spec_format="openapi_3_0",
            auth_type=None,
            group_name="Delete API",
            tool_count=1,
            enabled=True,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        ))
        tool_repo = _mock_tool_repo()
        tool_repo.get_all_tools = AsyncMock(return_value=[
            ToolDefinition(
                name="delete_api__get_items",
                description="Get items",
                config={"source_id": "src-del"},
            ),
            ToolDefinition(
                name="other_api__get_items",
                description="Other items",
                config={"source_id": "src-other"},
            ),
        ])

        with patch("moira.services.tool_ingestion.tool_provisioner.service_provider") as mock_sp:
            mock_sp.return_value = None
            provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)
            deleted = await provisioner.delete_source("src-del")

        assert deleted == ["delete_api__get_items"]
        tool_repo.delete_tool.assert_called_once_with("delete_api__get_items")
        source_repo.delete.assert_called_once_with("src-del")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_source_raises(self):
        source_repo = _mock_source_repo()
        source_repo.get = AsyncMock(return_value=None)
        tool_repo = _mock_tool_repo()

        provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)
        with pytest.raises(ValueError, match="not found"):
            await provisioner.delete_source("nonexistent")
