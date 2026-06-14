"""Tests for tool ingestion API endpoints.

Tests the /api/tools/ingest/* endpoints using the full FastAPI test client
with real database backing. The preview/start endpoint requires network or
spec content; we test with inline spec_content to avoid external deps."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moira.config import (
    DatabaseConfig,
    InferenceConfig,
    InferenceEndpointConfig,
    InferenceModelsConfig,
    MoiraConfig,
)
from moira.persistence.interfaces import (
    Conversation,
    ConversationRepository,
    Message,
    ModelPreferences,
    ModelPreferencesRepository,
    WorkflowRun,
)
from moira.service_setup import init_services, shutdown_services


class FakeConversationRepo(ConversationRepository):
    def __init__(self):
        self._conversations: dict[str, Conversation] = {}

    async def create_conversation(self, user_id: str, title: str) -> Conversation:
        import uuid
        from datetime import datetime, timezone

        cid = str(uuid.uuid4())
        c = Conversation(
            id=cid,
            user_id=user_id,
            title=title,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._conversations[cid] = c
        return c

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        return self._conversations.get(conversation_id)

    async def list_conversations(self, user_id: str) -> list[Conversation]:
        return list(self._conversations.values())

    async def insert_message(self, conversation_id: str, role: str, content: str):
        return Message(
            id=1,
            conversation_id=conversation_id,
            role=role,
            content=content,
            created_at="",
        )

    async def get_messages(self, conversation_id: str):
        return []

    async def update_conversation(self, conversation_id: str, title: str):
        return None

    async def save_workflow_run(self, run: WorkflowRun) -> None:
        pass

    async def get_workflow_runs(self, conversation_id: str):
        return []

    async def get_workflow_run(self, run_id: str) -> WorkflowRun | None:
        return None

    async def delete_conversation(self, conversation_id: str) -> bool:
        return False

    async def truncate_from_message(self, conversation_id: str, user_message_id: int) -> bool:
        return False

    async def cleanup_stale_runs(self) -> int:
        return 0


class FakePrefsRepo(ModelPreferencesRepository):
    def __init__(self):
        self._prefs = ModelPreferences(user_id="default")

    async def get_preferences(self, user_id: str) -> ModelPreferences:
        return self._prefs

    async def set_preferences(self, preferences: ModelPreferences) -> None:
        self._prefs = preferences


def _config(tmp_dir: str):
    return MoiraConfig(
        database=DatabaseConfig(
            sqlite_path=str(Path(tmp_dir) / "test.db"),
            lancedb_path=str(Path(tmp_dir) / "vectors"),
        ),
        inference=InferenceConfig(
            providers=[InferenceEndpointConfig(name="test", base_url="http://localhost:9999/v1")],
            models=InferenceModelsConfig(
                intelligence_endpoint="test",
                intelligence_model="test-model",
                task_endpoint="",
                task_model="",
            ),
        ),
    )


@pytest.fixture
async def app_client(tmp_path):
    import os

    data_dir = str(tmp_path / "moira_data")
    os.makedirs(data_dir, exist_ok=True)
    os.environ["MOIRA_DATA_DIR"] = data_dir

    config = _config(str(tmp_path))
    conv_repo = FakeConversationRepo()
    prefs_repo = FakePrefsRepo()

    from moira.config import resolve_db_path
    from moira.persistence.sqlite.schema import run_migrations

    run_migrations(resolve_db_path(config))

    await init_services(config, conversation_repo=conv_repo, prefs_repo=prefs_repo)
    from moira.main import create_app

    app = create_app()
    client = TestClient(app)
    yield client
    await shutdown_services()
    os.environ.pop("MOIRA_DATA_DIR", None)


MINIMAL_SPEC = json.dumps(
    {
        "openapi": "3.0.3",
        "info": {"title": "Petstore", "version": "1.0.0"},
        "servers": [{"url": "https://petstore.example.com/v1"}],
        "paths": {
            "/pets": {
                "get": {
                    "operationId": "listPets",
                    "summary": "List all pets",
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer"},
                        },
                    ],
                    "responses": {"200": {"description": "A list of pets"}},
                },
            },
            "/pets/{petId}": {
                "get": {
                    "operationId": "showPetById",
                    "summary": "Get a pet by ID",
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "A pet"}},
                },
            },
        },
    }
)


def test_ingest_start_with_spec_content(app_client):
    """POST /tools/ingest/start with inline spec_content returns a preview."""
    resp = app_client.post(
        "/api/tools/ingest/start",
        json={
            "spec_content": MINIMAL_SPEC,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["api_title"] == "Petstore"
    assert data["spec_format"] == "openapi_3_0"
    assert len(data["operations"]) == 2
    assert data["total_operations"] == 2
    assert data["auth_required"] is False
    assert data["auth_type"] is None
    assert "source_id" in data
    assert data["server_urls"] == ["https://petstore.example.com/v1"]

    # Check generated tool names
    names = {op["name"] for op in data["operations"]}
    assert "petstore__list_pets" in names
    assert "petstore__show_pet_by_id" in names


def test_ingest_start_no_input_returns_400(app_client):
    """POST /tools/ingest/start with no inputs returns 400."""
    resp = app_client.post("/api/tools/ingest/start", json={})
    assert resp.status_code == 400


def test_ingest_start_group_name_override(app_client):
    """POST /tools/ingest/start with group_name override."""
    resp = app_client.post(
        "/api/tools/ingest/start",
        json={
            "spec_content": MINIMAL_SPEC,
            "group_name": "My Pet API",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["group_name"] == "My Pet API"
    assert data["group_slug"] == "my_pet_api"
    names = {op["name"] for op in data["operations"]}
    assert all(n.startswith("my_pet_api__") for n in names)


def test_ingest_start_invalid_spec_returns_422(app_client):
    """POST /tools/ingest/start with invalid spec returns 422."""
    resp = app_client.post(
        "/api/tools/ingest/start",
        json={
            "spec_content": "not a spec",
        },
    )
    assert resp.status_code == 422


def test_ingest_commit_creates_tools(app_client):
    """Full happy path: start → commit creates tools."""
    start_resp = app_client.post(
        "/api/tools/ingest/start",
        json={
            "spec_content": MINIMAL_SPEC,
        },
    )
    assert start_resp.status_code == 200
    preview = start_resp.json()

    commit_resp = app_client.post(
        "/api/tools/ingest/commit",
        json={
            "source_id": preview["source_id"],
            "base_url": preview["base_url"],
            "spec_url": preview.get("spec_url"),
            "spec_format": preview["spec_format"],
            "group_name": preview["group_name"],
            "auth_type": preview["auth_type"],
            "selected_operations": [
                preview["operations"][0]["name"],
                preview["operations"][1]["name"],
            ],
            "operations": preview["operations"],
            "server_url": preview["server_urls"][0],
        },
    )
    assert commit_resp.status_code == 200
    result = commit_resp.json()
    assert result["total"] == 2
    assert len(result["succeeded"]) == 2
    assert result["failed"] == []
    assert result["disabled"] == []


def test_ingest_commit_tools_appear_in_list(app_client):
    """Tools created via commit show up in GET /tools."""
    start_resp = app_client.post(
        "/api/tools/ingest/start",
        json={
            "spec_content": MINIMAL_SPEC,
        },
    )
    preview = start_resp.json()

    app_client.post(
        "/api/tools/ingest/commit",
        json={
            "source_id": preview["source_id"],
            "base_url": preview["base_url"],
            "spec_format": preview["spec_format"],
            "group_name": preview["group_name"],
            "auth_type": preview["auth_type"],
            "selected_operations": [preview["operations"][0]["name"]],
            "operations": preview["operations"],
            "server_url": preview["server_urls"][0],
        },
    )

    tools_resp = app_client.get("/api/tools")
    assert tools_resp.status_code == 200
    tool_names = {t["name"] for t in tools_resp.json()["tools"]}
    assert preview["operations"][0]["name"] in tool_names


def test_ingest_commit_missing_fields_returns_400(app_client):
    """POST /tools/ingest/commit with missing fields returns 400."""
    resp = app_client.post("/api/tools/ingest/commit", json={})
    assert resp.status_code == 400


def test_ingest_commit_no_operations_returns_400(app_client):
    resp = app_client.post(
        "/api/tools/ingest/commit",
        json={
            "source_id": "test",
            "group_name": "Test",
            "server_url": "https://example.com",
        },
    )
    assert resp.status_code == 400


def test_list_sources_empty(app_client):
    """GET /tools/ingest/sources returns empty for a fresh DB."""
    resp = app_client.get("/api/tools/ingest/sources")
    assert resp.status_code == 200
    assert isinstance(resp.json()["sources"], list)


def test_list_sources_after_commit(app_client):
    """GET /tools/ingest/sources returns source after commit."""
    start_resp = app_client.post(
        "/api/tools/ingest/start",
        json={
            "spec_content": MINIMAL_SPEC,
        },
    )
    preview = start_resp.json()

    app_client.post(
        "/api/tools/ingest/commit",
        json={
            "source_id": preview["source_id"],
            "base_url": preview["base_url"],
            "spec_format": preview["spec_format"],
            "group_name": preview["group_name"],
            "auth_type": preview["auth_type"],
            "selected_operations": [preview["operations"][0]["name"]],
            "operations": preview["operations"],
            "server_url": preview["server_urls"][0],
        },
    )

    resp = app_client.get("/api/tools/ingest/sources")
    assert resp.status_code == 200
    sources = resp.json()["sources"]
    assert len(sources) == 1
    assert sources[0]["name"] == preview["group_name"]
    assert sources[0]["tool_count"] == 1


def test_get_source(app_client):
    """GET /tools/ingest/sources/{id} returns a single source."""
    start_resp = app_client.post(
        "/api/tools/ingest/start",
        json={
            "spec_content": MINIMAL_SPEC,
        },
    )
    preview = start_resp.json()

    app_client.post(
        "/api/tools/ingest/commit",
        json={
            "source_id": preview["source_id"],
            "base_url": preview["base_url"],
            "spec_format": preview["spec_format"],
            "group_name": preview["group_name"],
            "auth_type": preview["auth_type"],
            "selected_operations": [preview["operations"][0]["name"]],
            "operations": preview["operations"],
            "server_url": preview["server_urls"][0],
        },
    )

    resp = app_client.get(f"/api/tools/ingest/sources/{preview['source_id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == preview["group_name"]


def test_get_source_not_found(app_client):
    resp = app_client.get("/api/tools/ingest/sources/nonexistent")
    assert resp.status_code == 404


def test_delete_source(app_client):
    """DELETE /tools/ingest/sources/{id} removes source and tools."""
    start_resp = app_client.post(
        "/api/tools/ingest/start",
        json={
            "spec_content": MINIMAL_SPEC,
        },
    )
    preview = start_resp.json()

    app_client.post(
        "/api/tools/ingest/commit",
        json={
            "source_id": preview["source_id"],
            "base_url": preview["base_url"],
            "spec_format": preview["spec_format"],
            "group_name": preview["group_name"],
            "auth_type": preview["auth_type"],
            "selected_operations": [preview["operations"][0]["name"]],
            "operations": preview["operations"],
            "server_url": preview["server_urls"][0],
        },
    )

    del_resp = app_client.delete(f"/api/tools/ingest/sources/{preview['source_id']}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"
    assert len(del_resp.json()["deleted_tools"]) == 1

    # Source no longer in list
    sources_resp = app_client.get("/api/tools/ingest/sources")
    assert len(sources_resp.json()["sources"]) == 0


def test_delete_source_not_found(app_client):
    resp = app_client.delete("/api/tools/ingest/sources/nonexistent")
    assert resp.status_code == 404


def test_ingest_start_with_auth_spec(app_client):
    """Spec with security schemes is reflected in preview."""
    spec_with_auth = json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": "Secure API", "version": "1.0.0"},
            "servers": [{"url": "https://secure.example.com"}],
            "security": [{"bearerAuth": []}],
            "components": {
                "securitySchemes": {
                    "bearerAuth": {
                        "type": "http",
                        "scheme": "bearer",
                    },
                },
            },
            "paths": {
                "/data": {
                    "get": {
                        "operationId": "getData",
                        "summary": "Get data",
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            },
        }
    )

    resp = app_client.post(
        "/api/tools/ingest/start",
        json={
            "spec_content": spec_with_auth,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["auth_required"] is True
    assert data["auth_type"] == "bearer"
    assert "bearerAuth" in data["security_schemes"]


def test_ingest_commit_auth_tools_disabled(app_client):
    """Auth-required API registers tools as disabled."""
    spec_with_auth = json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": "Auth API", "version": "1.0.0"},
            "servers": [{"url": "https://auth.example.com"}],
            "security": [{"apiKey": []}],
            "components": {
                "securitySchemes": {
                    "apiKey": {
                        "type": "apiKey",
                        "in": "header",
                        "name": "X-API-Key",
                    },
                },
            },
            "paths": {
                "/things": {
                    "get": {
                        "operationId": "getThings",
                        "summary": "Get things",
                        "responses": {"200": {"description": "OK"}},
                    },
                },
            },
        }
    )

    start_resp = app_client.post(
        "/api/tools/ingest/start",
        json={
            "spec_content": spec_with_auth,
        },
    )
    preview = start_resp.json()

    commit_resp = app_client.post(
        "/api/tools/ingest/commit",
        json={
            "source_id": preview["source_id"],
            "base_url": preview["base_url"],
            "spec_format": preview["spec_format"],
            "group_name": preview["group_name"],
            "auth_type": preview["auth_type"],
            "selected_operations": [preview["operations"][0]["name"]],
            "operations": preview["operations"],
            "server_url": preview["server_urls"][0],
        },
    )
    assert commit_resp.status_code == 200
    result = commit_resp.json()
    assert result["disabled"] == [preview["operations"][0]["name"]]

    # Verify the tool is disabled in the DB
    tools_resp = app_client.get("/api/tools")
    tool = next(
        t for t in tools_resp.json()["tools"] if t["name"] == preview["operations"][0]["name"]
    )
    assert tool["enabled"] is False


def _ingest_tools(app_client, spec_content=None):
    """Helper: ingest tools and return the preview + commit result."""
    if spec_content is None:
        spec_content = MINIMAL_SPEC
    start_resp = app_client.post(
        "/api/tools/ingest/start",
        json={
            "spec_content": spec_content,
        },
    )
    assert start_resp.status_code == 200
    preview = start_resp.json()
    commit_resp = app_client.post(
        "/api/tools/ingest/commit",
        json={
            "source_id": preview["source_id"],
            "base_url": preview["base_url"],
            "spec_format": preview["spec_format"],
            "group_name": preview["group_name"],
            "auth_type": preview["auth_type"],
            "selected_operations": [op["name"] for op in preview["operations"]],
            "operations": preview["operations"],
            "server_url": preview["server_urls"][0],
        },
    )
    assert commit_resp.status_code == 200
    return preview, commit_resp.json()


class TestProtectedGroup:
    """Tests for the 'standard' group protection guards."""

    def test_rename_standard_group_returns_403(self, app_client):
        resp = app_client.patch(
            "/api/tool-admin/groups/standard",
            json={
                "display_name": "Renamed",
            },
        )
        assert resp.status_code == 403
        assert "protected" in resp.json()["detail"].lower()

    def test_delete_standard_group_returns_403(self, app_client):
        resp = app_client.delete("/api/tool-admin/groups/standard")
        assert resp.status_code == 403
        assert "protected" in resp.json()["detail"].lower()

    def test_toggle_standard_group_returns_403(self, app_client):
        resp = app_client.post(
            "/api/tool-admin/groups/standard/toggle",
            json={
                "enabled": False,
            },
        )
        assert resp.status_code == 403
        assert "protected" in resp.json()["detail"].lower()

    def test_rename_to_standard_returns_403(self, app_client):
        """Renaming any group to produce slug 'standard' is rejected."""
        preview, _ = _ingest_tools(app_client)
        group_slug = preview["group_slug"]
        resp = app_client.patch(
            f"/api/tool-admin/groups/{group_slug}",
            json={
                "display_name": "Standard",
            },
        )
        assert resp.status_code == 403
        assert "reserved" in resp.json()["detail"].lower()

    def test_rename_non_standard_group_succeeds(self, app_client):
        """Renaming a non-standard group works fine."""
        preview, _ = _ingest_tools(app_client)
        group_slug = preview["group_slug"]
        resp = app_client.patch(
            f"/api/tool-admin/groups/{group_slug}",
            json={
                "display_name": "Renamed API",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "renamed_api"

    def test_delete_non_standard_group_succeeds(self, app_client):
        """Deleting a non-standard group works fine."""
        preview, _ = _ingest_tools(app_client)
        group_slug = preview["group_slug"]
        resp = app_client.delete(f"/api/tool-admin/groups/{group_slug}")
        assert resp.status_code == 200
        assert len(resp.json()["deleted"]) > 0


class TestDeleteTool:
    """Tests for the individual tool deletion endpoint."""

    def test_delete_discovered_tool(self, app_client):
        """DELETE /tool-admin/tools/{name} removes a single tool."""
        preview, _ = _ingest_tools(app_client)
        tool_name = preview["operations"][0]["name"]

        resp = app_client.delete(f"/api/tool-admin/tools/{tool_name}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == tool_name

        tools_resp = app_client.get("/api/tools")
        names = {t["name"] for t in tools_resp.json()["tools"]}
        assert tool_name not in names

    def test_delete_tool_not_found(self, app_client):
        """DELETE /tool-admin/tools/{name} returns 404 for unknown tool."""
        resp = app_client.delete("/api/tool-admin/tools/nonexistent")
        assert resp.status_code == 404

    def test_delete_standard_group_tool_returns_403(self, app_client):
        """Tools in the 'standard' group cannot be deleted individually."""
        # The 'standard' group is created by migrations with built-in tools.
        # Find one to test with.
        tools_resp = app_client.get("/api/tools")
        standard_tools = [t for t in tools_resp.json()["tools"] if t["group_name"] == "standard"]
        assert len(standard_tools) > 0, "No standard tools found"

        tool_name = standard_tools[0]["name"]
        resp = app_client.delete(f"/api/tool-admin/tools/{tool_name}")
        assert resp.status_code == 403
        assert "protected" in resp.json()["detail"].lower()

    def test_delete_tool_leaves_other_tools(self, app_client):
        """Deleting one tool does not affect others in the same group."""
        preview, _ = _ingest_tools(app_client)
        names = [op["name"] for op in preview["operations"]]
        assert len(names) >= 2

        resp = app_client.delete(f"/api/tool-admin/tools/{names[0]}")
        assert resp.status_code == 200

        tools_resp = app_client.get("/api/tools")
        remaining = {t["name"] for t in tools_resp.json()["tools"]}
        assert names[0] not in remaining
        assert names[1] in remaining


class TestToolDescriptionEdit:
    """Tests for editing and resetting tool descriptions."""

    def test_ingest_populates_original_description(self, app_client):
        """Ingest sets both description and original_description."""
        preview, _ = _ingest_tools(app_client)
        tool_name = preview["operations"][0]["name"]

        resp = app_client.get(f"/api/tools/{tool_name}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["original_description"] != ""
        # original_description is the base spec text (no usage hint)
        assert data["description"].startswith(data["original_description"])

    def test_patch_description_updates_tool(self, app_client):
        """PATCH /tools/{name} with description updates it."""
        preview, _ = _ingest_tools(app_client)
        tool_name = preview["operations"][0]["name"]

        resp = app_client.patch(
            f"/api/tools/{tool_name}",
            json={
                "description": "Custom description",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Custom description"

        # Persisted
        resp2 = app_client.get(f"/api/tools/{tool_name}")
        assert resp2.json()["description"] == "Custom description"

    def test_patch_description_null_resets_to_original(self, app_client):
        """PATCH /tools/{name} with description=null resets to original."""
        preview, _ = _ingest_tools(app_client)
        tool_name = preview["operations"][0]["name"]

        tool_resp = app_client.get(f"/api/tools/{tool_name}")
        original = tool_resp.json()["original_description"]

        # Edit first
        app_client.patch(
            f"/api/tools/{tool_name}",
            json={
                "description": "Edited",
            },
        )

        # Reset
        resp = app_client.patch(
            f"/api/tools/{tool_name}",
            json={
                "description": None,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == original

    def test_original_description_unchanged_after_edit(self, app_client):
        """Editing description does not change original_description."""
        preview, _ = _ingest_tools(app_client)
        tool_name = preview["operations"][0]["name"]

        tool_resp = app_client.get(f"/api/tools/{tool_name}")
        original = tool_resp.json()["original_description"]

        app_client.patch(
            f"/api/tools/{tool_name}",
            json={
                "description": "Something new",
            },
        )

        tool_resp2 = app_client.get(f"/api/tools/{tool_name}")
        assert tool_resp2.json()["original_description"] == original
