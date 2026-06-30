"""Tests for /api/conversations/{id}/model endpoints.

Tests the per-conversation model override API: GET (with and without
override), PUT (set override), DELETE (reset to default).
Uses mock services to avoid full init_services startup."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from moira.persistence.interfaces import (
    Conversation,
    ConversationModelOverride,
    ModelPreferences,
)
from moira.service_setup import _services


@pytest.fixture
def mock_services():
    """Inject mock services for the conversation model API routes."""

    conv_repo = MagicMock()
    conv_repo.get_conversation = AsyncMock(
        return_value=Conversation(
            id="conv-1", user_id="default", title="Test", created_at="2025-01-01"
        )
    )

    conv_model_repo = MagicMock()
    conv_model_repo.get_override = AsyncMock(return_value=None)
    conv_model_repo.upsert_override = AsyncMock()
    conv_model_repo.delete_override = AsyncMock(return_value=True)

    prefs_repo = MagicMock()
    prefs_repo.get_preferences = AsyncMock(
        return_value=ModelPreferences(
            user_id="default",
            intelligence_endpoint="local-lab",
            intelligence_model="llama3:70b",
        )
    )

    saved = dict(_services)
    _services["conversation_repository"] = conv_repo
    _services["conversation_model_repository"] = conv_model_repo
    _services["model_preferences_repository"] = prefs_repo

    yield {
        "client": TestClient(_create_test_app()),
        "conv_repo": conv_repo,
        "conv_model_repo": conv_model_repo,
        "prefs_repo": prefs_repo,
    }

    _services.clear()
    _services.update(saved)


def _create_test_app():
    from fastapi import FastAPI

    from moira.api.routes.conversations import router as conversations_router

    app = FastAPI()
    app.include_router(conversations_router, prefix="/api")
    return app


class TestConversationModelAPI:
    def test_get_model_no_override_returns_global_default(self, mock_services):
        """GET returns global default with overridden=false when no override exists."""
        resp = mock_services["client"].get("/api/conversations/conv-1/model")
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint"] == "local-lab"
        assert data["model"] == "llama3:70b"
        assert data["overridden"] is False

    def test_get_model_with_override(self, mock_services):
        """GET returns override with overridden=true when an override exists."""
        mock_services["conv_model_repo"].get_override = AsyncMock(
            return_value=ConversationModelOverride(
                conversation_id="conv-1",
                intelligence_endpoint="cloud",
                intelligence_model="gpt-4o",
            )
        )
        resp = mock_services["client"].get("/api/conversations/conv-1/model")
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint"] == "cloud"
        assert data["model"] == "gpt-4o"
        assert data["overridden"] is True

    def test_set_model_override(self, mock_services):
        """PUT creates the override."""
        resp = mock_services["client"].put(
            "/api/conversations/conv-1/model",
            json={"endpoint": "cloud", "model": "gpt-4o"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint"] == "cloud"
        assert data["model"] == "gpt-4o"
        mock_services["conv_model_repo"].upsert_override.assert_called_once()

    def test_set_model_validation_missing_fields(self, mock_services):
        """PUT requires both endpoint and model."""
        resp = mock_services["client"].put(
            "/api/conversations/conv-1/model",
            json={"endpoint": "cloud"},
        )
        assert resp.status_code == 400

    def test_reset_model_override(self, mock_services):
        """DELETE removes the override."""
        resp = mock_services["client"].delete("/api/conversations/conv-1/model")
        assert resp.status_code == 200
        assert resp.json()["status"] == "reset"
        mock_services["conv_model_repo"].delete_override.assert_called_once_with("conv-1")

    def test_reset_model_no_override_404(self, mock_services):
        """DELETE returns 404 when no override exists."""
        mock_services["conv_model_repo"].delete_override = AsyncMock(return_value=False)
        resp = mock_services["client"].delete("/api/conversations/conv-1/model")
        assert resp.status_code == 404

    def test_get_model_conversation_not_found(self, mock_services):
        """GET returns 404 for a non-existent conversation."""
        mock_services["conv_repo"].get_conversation = AsyncMock(return_value=None)
        resp = mock_services["client"].get("/api/conversations/nonexistent/model")
        assert resp.status_code == 404
