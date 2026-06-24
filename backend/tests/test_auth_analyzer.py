"""Tests for the auth_analyzer module."""

from _tool_ingestion_helpers import _openapi_spec, _swagger_spec

from moira.services.tool_ingestion.auth_analyzer import (
    extract_security_schemes,
    get_auth_type_for_spec,
)


class TestExtractSecuritySchemes:
    def test_no_schemes(self):
        spec = _openapi_spec()
        assert extract_security_schemes(spec) == {}

    def test_bearer_scheme(self):
        spec = _openapi_spec(
            security_schemes={
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "JWT auth",
                }
            }
        )
        schemes = extract_security_schemes(spec)
        assert "bearerAuth" in schemes
        assert schemes["bearerAuth"].scheme_type == "bearer"
        assert schemes["bearerAuth"].location == "header"

    def test_api_key_header(self):
        spec = _openapi_spec(
            security_schemes={
                "apiKey": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key",
                }
            }
        )
        schemes = extract_security_schemes(spec)
        assert schemes["apiKey"].scheme_type == "api_key_header"
        assert schemes["apiKey"].name == "X-API-Key"

    def test_api_key_query(self):
        spec = _openapi_spec(
            security_schemes={
                "queryKey": {
                    "type": "apiKey",
                    "in": "query",
                    "name": "api_key",
                }
            }
        )
        schemes = extract_security_schemes(spec)
        assert schemes["queryKey"].scheme_type == "api_key_query"
        assert schemes["queryKey"].name == "api_key"

    def test_basic_auth(self):
        spec = _openapi_spec(
            security_schemes={
                "basicAuth": {
                    "type": "http",
                    "scheme": "basic",
                }
            }
        )
        schemes = extract_security_schemes(spec)
        assert schemes["basicAuth"].scheme_type == "basic"

    def test_oauth2_ignored(self):
        spec = _openapi_spec(
            security_schemes={
                "oauth": {
                    "type": "oauth2",
                    "flows": {"clientCredentials": {"tokenUrl": "/token", "scopes": {}}},
                }
            }
        )
        assert extract_security_schemes(spec) == {}

    def test_swagger_2_security_definitions(self):
        spec = _swagger_spec(
            security_definitions={
                "apiKey": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-Api-Key",
                }
            }
        )
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
        spec = _openapi_spec(
            security_schemes={
                "apiKey": {"type": "apiKey", "in": "header", "name": "X-Key"},
            }
        )
        assert get_auth_type_for_spec(spec) is None
