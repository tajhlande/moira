"""Parse security schemes from OpenAPI/Swagger specs.

Extracts and classifies authentication requirements from a parsed spec
dictionary. Returns a mapping of scheme name to SecuritySchemeInfo for
use in the ingestion wizard's informational display."""

import logging
from typing import Any

from moira.persistence.interfaces import SecuritySchemeInfo

logger = logging.getLogger(__name__)


def extract_security_schemes(spec: dict[str, Any]) -> dict[str, SecuritySchemeInfo]:
    """Extract security schemes from an OpenAPI 3.x or Swagger 2.0 spec.

    Returns a dict keyed by scheme name. Works with both OpenAPI 3.x
    (components.securitySchemes) and Swagger 2.0 (securityDefinitions)."""
    raw_schemes: dict[str, Any] = {}

    # OpenAPI 3.x
    components = spec.get("components", {})
    if isinstance(components, dict):
        raw_schemes = components.get("securitySchemes", {}) or {}

    # Swagger 2.0 fallback
    if not raw_schemes:
        raw_schemes = spec.get("securityDefinitions", {}) or {}

    result: dict[str, SecuritySchemeInfo] = {}
    for name, scheme in raw_schemes.items():
        if not isinstance(scheme, dict):
            continue
        info = _classify_scheme(scheme)
        if info is not None:
            result[name] = info

    return result


def get_auth_type_for_spec(spec: dict[str, Any]) -> str | None:
    """Return the dominant auth type for the spec, or None if no auth required.

    Examines the top-level security requirements and the declared schemes.
    Returns the first scheme's type. If multiple schemes exist, returns the
    first one found. Returns None if no security is declared."""
    schemes = extract_security_schemes(spec)
    if not schemes:
        return None

    # Check top-level security requirements
    global_security = spec.get("security", [])
    if global_security:
        for req in global_security:
            if isinstance(req, dict):
                for scheme_name in req:
                    if scheme_name in schemes:
                        return schemes[scheme_name].scheme_type

    # No global security requirement — schemes are declared but not enforced.
    # Many specs define optional auth schemes (e.g. admin vs public endpoints).
    return None


def _classify_scheme(scheme: dict[str, Any]) -> SecuritySchemeInfo | None:
    """Classify a single security scheme into a SecuritySchemeInfo."""
    scheme_type = scheme.get("type", "")

    if scheme_type == "http":
        http_scheme = scheme.get("scheme", "").lower()
        if http_scheme == "bearer":
            return SecuritySchemeInfo(
                scheme_type="bearer",
                name="Authorization",
                location="header",
                description=scheme.get("description", "Bearer token authentication"),
            )
        elif http_scheme == "basic":
            return SecuritySchemeInfo(
                scheme_type="basic",
                name="Authorization",
                location="header",
                description=scheme.get("description", "HTTP Basic authentication"),
            )

    elif scheme_type == "apiKey":
        location = scheme.get("in", "header")
        param_name = scheme.get("name", "X-API-Key")
        if location == "header":
            return SecuritySchemeInfo(
                scheme_type="api_key_header",
                name=param_name,
                location="header",
                description=scheme.get("description", f"API key in header ({param_name})"),
            )
        elif location == "query":
            return SecuritySchemeInfo(
                scheme_type="api_key_query",
                name=param_name,
                location="query",
                description=scheme.get("description", f"API key in query param ({param_name})"),
            )

    elif scheme_type == "oauth2":
        logger.debug("OAuth2 scheme found — deferred, not classified")
        return None

    return None
