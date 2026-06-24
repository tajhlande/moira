"""Shared spec-builder helpers for tool ingestion tests."""


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
