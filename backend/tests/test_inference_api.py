"""Tests for /api/inference/* endpoints.

Uses mock services to avoid the heavy init_services startup (embeddings,
LanceDB, tool enrichment). Tests focus on routing, validation, and
correct integration with the registry and repos."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from moira.inference.registry import ModelRegistry
from moira.persistence.interfaces import (
    InferenceModelRow,
    InferenceProvider,
    ModelPreferences,
)
from moira.service_setup import _services


@pytest.fixture
def mock_services():
    """Inject mock registry, provider repo, and prefs repo into the service
    locator so the API routes can find them without full init_services."""

    provider_repo = MagicMock()
    provider_repo.get_all_providers = AsyncMock(return_value=[])
    provider_repo.get_provider = AsyncMock(return_value=None)
    provider_repo.get_all_models = AsyncMock(return_value=[])
    provider_repo.get_models = AsyncMock(return_value=[])
    provider_repo.upsert_model = AsyncMock()
    provider_repo.upsert_discovered_models = AsyncMock()
    provider_repo.delete_stale_models = AsyncMock()
    provider_repo.delete_provider = AsyncMock(return_value=True)
    provider_repo.upsert_provider = AsyncMock()

    prefs_repo = MagicMock()
    prefs_repo.get_preferences = AsyncMock(return_value=ModelPreferences(user_id="default"))
    prefs_repo.set_preferences = AsyncMock()

    registry = MagicMock(spec=ModelRegistry)
    registry._clients = {}
    registry.get_available_models = MagicMock(return_value=[])
    registry.resolve_api_key = AsyncMock(return_value="")
    registry.add_provider = AsyncMock(return_value="local-lab")
    registry.update_provider = AsyncMock()
    registry.remove_provider = AsyncMock(return_value=True)
    registry.refresh_models = AsyncMock(return_value=[])
    registry.set_assignments = AsyncMock()
    registry.set_model_capability = AsyncMock()
    registry.validate_connection = AsyncMock(return_value=(True, "", 5))

    saved = dict(_services)
    _services["inference_provider_repository"] = provider_repo
    _services["model_preferences_repository"] = prefs_repo
    _services["model_registry"] = registry

    yield {
        "client": TestClient(_create_test_app()),
        "provider_repo": provider_repo,
        "prefs_repo": prefs_repo,
        "registry": registry,
    }

    _services.clear()
    _services.update(saved)


def _create_test_app():
    """Create a minimal FastAPI app with just the inference router."""
    from fastapi import FastAPI

    from moira.api.routes.inference import router as inference_router

    app = FastAPI()
    app.include_router(inference_router, prefix="/api")
    return app


class TestProviderEndpoints:
    def test_list_empty(self, mock_services):
        resp = mock_services["client"].get("/api/inference/providers")
        assert resp.status_code == 200
        assert resp.json() == {"providers": []}

    def test_list_with_providers(self, mock_services):
        mock_services["provider_repo"].get_all_providers = AsyncMock(
            return_value=[
                InferenceProvider(
                    slug="local-lab",
                    display_name="Local Lab",
                    base_url="http://localhost:8080/v1",
                    provider_type="completions",
                    credential_name="provider.local_lab",
                    created_at="2024-01-01T00:00:00",
                    updated_at="2024-01-01T00:00:00",
                )
            ]
        )
        resp = mock_services["client"].get("/api/inference/providers")
        assert resp.status_code == 200
        providers = resp.json()["providers"]
        assert len(providers) == 1
        assert providers[0]["slug"] == "local-lab"
        assert providers[0]["display_name"] == "Local Lab"
        assert providers[0]["base_url"] == "http://localhost:8080/v1"

    def test_create_provider(self, mock_services):
        resp = mock_services["client"].post(
            "/api/inference/providers",
            json={
                "display_name": "Local Lab",
                "base_url": "http://localhost:8080/v1",
                "provider_type": "completions",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["slug"] == "local-lab"
        assert resp.json()["display_name"] == "Local Lab"
        mock_services["registry"].add_provider.assert_called_once()

    def test_create_missing_display_name(self, mock_services):
        resp = mock_services["client"].post(
            "/api/inference/providers",
            json={"base_url": "http://localhost:8080/v1"},
        )
        assert resp.status_code == 400

    def test_create_missing_base_url(self, mock_services):
        resp = mock_services["client"].post(
            "/api/inference/providers",
            json={"display_name": "Local Lab"},
        )
        assert resp.status_code == 400

    def test_delete_provider(self, mock_services):
        resp = mock_services["client"].delete("/api/inference/providers/local-lab")
        assert resp.status_code == 200
        mock_services["registry"].remove_provider.assert_called_once_with("local-lab")

    def test_delete_nonexistent(self, mock_services):
        mock_services["registry"].remove_provider = AsyncMock(return_value=False)
        resp = mock_services["client"].delete("/api/inference/providers/nope")
        assert resp.status_code == 404


class TestValidateEndpoint:
    def test_validate_success(self, mock_services):
        resp = mock_services["client"].post(
            "/api/inference/providers/validate",
            json={
                "base_url": "http://localhost:8080/v1",
                "api_key": "test-key",
                "provider_type": "completions",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["model_count"] == 5

    def test_validate_failure(self, mock_services):
        mock_services["registry"].validate_connection = AsyncMock(
            return_value=(False, "Connection refused", 0)
        )
        resp = mock_services["client"].post(
            "/api/inference/providers/validate",
            json={
                "base_url": "http://bad-host:9999/v1",
                "api_key": "test-key",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["error"] == "Connection refused"

    def test_validate_missing_base_url(self, mock_services):
        resp = mock_services["client"].post(
            "/api/inference/providers/validate",
            json={"api_key": "test-key"},
        )
        assert resp.status_code == 400

    def test_validate_passes_slug_for_credential_resolution(self, mock_services):
        resp = mock_services["client"].post(
            "/api/inference/providers/validate",
            json={
                "base_url": "http://localhost:8080/v1",
                "slug": "local-lab",
            },
        )
        assert resp.status_code == 200
        mock_services["registry"].validate_connection.assert_called_once_with(
            base_url="http://localhost:8080/v1",
            api_key="",
            provider_type="completions",
            slug="local-lab",
        )


class TestModelEndpoints:
    def test_get_models_empty(self, mock_services):
        resp = mock_services["client"].get("/api/inference/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["models"] == []
        assert data["assignments"]["intelligence"]["endpoint"] == ""

    def test_get_models_with_data(self, mock_services):
        mock_services["provider_repo"].get_all_models = AsyncMock(
            return_value=[
                InferenceModelRow("local-lab", "llama3:70b", True),
                InferenceModelRow("local-lab", "llama3:3b", False),
            ]
        )
        resp = mock_services["client"].get("/api/inference/models")
        data = resp.json()
        assert len(data["models"]) == 2
        assert data["models"][0]["id"] == "llama3:70b"
        assert data["models"][0]["native_tool_calling"] is True

    def test_set_assignments(self, mock_services):
        resp = mock_services["client"].put(
            "/api/inference/models/assign",
            json={
                "intelligence": {"endpoint": "local-lab", "model": "llama3:70b"},
                "task": {"endpoint": "local-lab", "model": "llama3:3b"},
            },
        )
        assert resp.status_code == 200
        mock_services["registry"].set_assignments.assert_called_once()

    def test_set_capability(self, mock_services):
        mock_services["provider_repo"].get_models = AsyncMock(
            return_value=[InferenceModelRow("local-lab", "llama3:70b", False)]
        )
        resp = mock_services["client"].patch(
            "/api/inference/models/local-lab/llama3:70b",
            json={"native_tool_calling": True},
        )
        assert resp.status_code == 200
        mock_services["registry"].set_model_capability.assert_called_once()

    def test_set_capability_model_not_found(self, mock_services):
        mock_services["provider_repo"].get_models = AsyncMock(return_value=[])
        resp = mock_services["client"].patch(
            "/api/inference/models/local-lab/nonexistent",
            json={"native_tool_calling": True},
        )
        assert resp.status_code == 404

    def test_set_capability_missing_param(self, mock_services):
        mock_services["provider_repo"].get_models = AsyncMock(
            return_value=[InferenceModelRow("local-lab", "llama3:70b", False)]
        )
        resp = mock_services["client"].patch(
            "/api/inference/models/local-lab/llama3:70b",
            json={"native_tool_calling": "not-a-bool"},
        )
        assert resp.status_code == 400

    def test_set_capability_slashed_model_id(self, mock_services):
        """Model IDs from OpenRouter contain slashes (e.g. deepseek/deepseek-v4-flash).
        The :path converter on the route must capture the full ID including slashes."""
        mock_services["provider_repo"].get_models = AsyncMock(
            return_value=[InferenceModelRow("open-router", "deepseek/deepseek-v4-flash", False)]
        )
        resp = mock_services["client"].patch(
            "/api/inference/models/open-router/deepseek/deepseek-v4-flash",
            json={"native_tool_calling": True},
        )
        assert resp.status_code == 200
        mock_services["registry"].set_model_capability.assert_called_once_with(
            "open-router", "deepseek/deepseek-v4-flash", True
        )
