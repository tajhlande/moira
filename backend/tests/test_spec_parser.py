"""Tests for the spec_parser module."""

import json

from _tool_ingestion_helpers import _openapi_spec, _swagger_spec

from moira.services.tool_ingestion.spec_parser import (
    _derive_method_id,
    _sanitize_operation_id,
    derive_group_slug,
    generate_tool_name,
    parse_spec_content,
)


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
        spec = _openapi_spec(
            paths={
                "/status": {
                    "get": {
                        "summary": "Health check",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            }
        )
        parsed = parse_spec_content(json.dumps(spec))
        assert parsed.title == "Test API"
        assert parsed.version == "openapi_3_0"
        assert len(parsed.operations) == 1
        assert parsed.operations[0].method == "GET"
        assert parsed.operations[0].path == "/status"
        assert parsed.operations[0].description == "Health check"

    def test_swagger_2(self):
        spec = _swagger_spec(
            paths={
                "/pets": {
                    "get": {
                        "summary": "List pets",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            }
        )
        parsed = parse_spec_content(json.dumps(spec))
        assert parsed.version == "swagger_2"
        assert len(parsed.operations) == 1
        assert parsed.server_urls == ["https://api.example.com/v1"]

    def test_parameters_extracted(self):
        spec = _openapi_spec(
            paths={
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
            }
        )
        parsed = parse_spec_content(json.dumps(spec))
        op = parsed.operations[0]
        assert len(op.parameters) == 2
        assert op.parameters[0].name == "city"
        assert op.parameters[0].location == "query"
        assert op.parameters[0].required is True
        assert op.parameters[1].name == "unit"
        assert op.parameters[1].required is False

    def test_request_body_extracted(self):
        spec = _openapi_spec(
            paths={
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
            }
        )
        parsed = parse_spec_content(json.dumps(spec))
        op = parsed.operations[0]
        assert op.request_body is not None
        assert op.request_body.content_type == "application/json"
        assert op.request_body.required is True
        assert "item" in op.request_body.schema_def.get("properties", {})

    def test_path_level_parameters_merged(self):
        spec = _openapi_spec(
            paths={
                "/items/{id}": {
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "get": {
                        "summary": "Get item",
                        "parameters": [
                            {"name": "fields", "in": "query", "schema": {"type": "string"}},
                        ],
                        "responses": {"200": {"description": "OK"}},
                    },
                }
            }
        )
        parsed = parse_spec_content(json.dumps(spec))
        op = parsed.operations[0]
        param_names = [p.name for p in op.parameters]
        assert "id" in param_names
        assert "fields" in param_names

    def test_deprecated_flag(self):
        spec = _openapi_spec(
            paths={
                "/old": {
                    "get": {
                        "summary": "Deprecated endpoint",
                        "deprecated": True,
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            }
        )
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
        spec = _openapi_spec(
            paths={
                "/items": {
                    "get": {"summary": "List", "responses": {"200": {"description": "OK"}}},
                    "post": {
                        "summary": "Create",
                        "responses": {"201": {"description": "Created"}},
                    },
                },
                "/items/{id}": {
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "get": {"summary": "Get", "responses": {"200": {"description": "OK"}}},
                    "delete": {
                        "summary": "Delete",
                        "responses": {"204": {"description": "No content"}},
                    },
                },
            }
        )
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
