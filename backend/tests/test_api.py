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
        self._counter = 0

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
        from datetime import datetime, timezone

        self._counter += 1
        return Message(
            id=self._counter,
            conversation_id=conversation_id,
            role=role,
            content=content,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    async def get_messages(self, conversation_id: str):
        return []

    async def update_conversation(self, conversation_id: str, title: str) -> Conversation | None:
        c = self._conversations.get(conversation_id)
        if c is None:
            return None
        updated = Conversation(id=c.id, user_id=c.user_id, title=title, created_at=c.created_at)
        self._conversations[conversation_id] = updated
        return updated

    async def save_workflow_run(self, run: WorkflowRun) -> None:
        pass

    async def get_workflow_runs(self, conversation_id: str) -> list[WorkflowRun]:
        return []

    async def delete_conversation(self, conversation_id: str) -> bool:
        if conversation_id in self._conversations:
            del self._conversations[conversation_id]
            return True
        return False

    async def truncate_from_message(self, conversation_id: str, user_message_id: int) -> bool:
        return False

    async def cleanup_stale_runs(self) -> int:
        return 0


class FakePrefsRepo(ModelPreferencesRepository):
    def __init__(self):
        self._prefs = ModelPreferences(
            user_id="default",
            intelligence_endpoint="test",
            intelligence_model="test-model",
            task_endpoint="",
            task_model="",
        )

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
    conversation_repo = FakeConversationRepo()
    prefs_repo = FakePrefsRepo()

    from moira.config import resolve_db_path
    from moira.persistence.sqlite.schema import run_migrations

    run_migrations(resolve_db_path(config))

    await init_services(config, conversation_repo=conversation_repo, prefs_repo=prefs_repo)
    from moira.main import create_app

    app = create_app()
    client = TestClient(app)
    yield client
    await shutdown_services()
    os.environ.pop("MOIRA_DATA_DIR", None)


def test_health_endpoint(app_client):
    resp = app_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_create_conversation(app_client):
    resp = app_client.post("/api/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["title"] == "New Conversation"


def test_list_conversations(app_client):
    app_client.post("/api/conversations")
    app_client.post("/api/conversations")
    resp = app_client.get("/api/conversations")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_conversation(app_client):
    create_resp = app_client.post("/api/conversations")
    conversation_id = create_resp.json()["id"]

    get_resp = app_client.get(f"/api/conversations/{conversation_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == conversation_id


def test_get_conversation_not_found(app_client):
    resp = app_client.get("/api/conversations/nonexistent")
    assert resp.status_code == 404


def test_update_conversation_title(app_client):
    create_resp = app_client.post("/api/conversations")
    conversation_id = create_resp.json()["id"]

    patch_resp = app_client.patch(
        f"/api/conversations/{conversation_id}",
        json={"title": "Renamed Conversation"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["title"] == "Renamed Conversation"

    get_resp = app_client.get(f"/api/conversations/{conversation_id}")
    assert get_resp.json()["title"] == "Renamed Conversation"


def test_update_conversation_not_found(app_client):
    resp = app_client.patch("/api/conversations/nonexistent", json={"title": "x"})
    assert resp.status_code == 404


def test_update_conversation_empty_title(app_client):
    create_resp = app_client.post("/api/conversations")
    conversation_id = create_resp.json()["id"]

    resp = app_client.patch(f"/api/conversations/{conversation_id}", json={"title": ""})
    assert resp.status_code == 400


def test_delete_conversation(app_client):
    create_resp = app_client.post("/api/conversations")
    conversation_id = create_resp.json()["id"]

    delete_resp = app_client.delete(f"/api/conversations/{conversation_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["status"] == "deleted"

    get_resp = app_client.get(f"/api/conversations/{conversation_id}")
    assert get_resp.status_code == 404


def test_delete_conversation_not_found(app_client):
    resp = app_client.delete("/api/conversations/nonexistent")
    assert resp.status_code == 404


def test_models_endpoint(app_client):
    resp = app_client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "assignments" in data


def test_set_model_assignments(app_client):
    resp = app_client.put(
        "/api/models",
        json={
            "intelligence": {"endpoint": "test", "model": "new-model"},
            "task": {"endpoint": "test", "model": "tiny-model"},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["intelligence"]["model"] == "new-model"
    assert data["task"]["model"] == "tiny-model"


def test_get_setting_definitions(app_client):
    resp = app_client.get("/api/settings/definitions")
    assert resp.status_code == 200
    data = resp.json()
    assert "definitions" in data
    assert len(data["definitions"]) > 0
    defn = data["definitions"][0]
    assert "key" in defn
    assert "type" in defn
    assert "default" in defn
    assert "label" in defn
    assert "group" in defn
    assert "constraints" in defn


def test_get_settings_prefix(app_client):
    resp = app_client.get("/api/settings?prefix=budget")
    assert resp.status_code == 200
    data = resp.json()
    assert "settings" in data
    assert len(data["settings"]) > 0
    assert all(s["key"].startswith("budget.") for s in data["settings"])


def test_get_single_setting(app_client):
    resp = app_client.get("/api/settings/budget.default_limit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "budget.default_limit"
    assert data["type"] == "integer"
    assert "label" in data
    assert "constraints" in data


def test_get_unknown_setting(app_client):
    resp = app_client.get("/api/settings/nonexistent.key")
    assert resp.status_code == 404


def test_set_setting(app_client):
    resp = app_client.put(
        "/api/settings/budget.default_limit",
        json={"value": 75},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["value"] == "75"

    get_resp = app_client.get("/api/settings/budget.default_limit")
    assert get_resp.json()["value"] == "75"


def test_set_setting_invalid_value(app_client):
    resp = app_client.put(
        "/api/settings/budget.default_limit",
        json={"value": -5},
    )
    assert resp.status_code == 422


def test_set_unknown_setting(app_client):
    resp = app_client.put(
        "/api/settings/nonexistent.key",
        json={"value": "test"},
    )
    assert resp.status_code == 404


def test_batch_set_settings(app_client):
    resp = app_client.put(
        "/api/settings",
        json={
            "settings": [
                {"key": "budget.cost.planning", "value": "10"},
                {"key": "budget.cost.verification", "value": "8"},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    keys = {s["key"] for s in data["settings"]}
    assert "budget.cost.planning" in keys
    assert "budget.cost.verification" in keys


def test_reset_settings(app_client):
    app_client.put("/api/settings/budget.default_limit", json={"value": 99})

    resp = app_client.delete("/api/settings?keys=budget.default_limit")
    assert resp.status_code == 200
    data = resp.json()
    reset = {s["key"]: s["value"] for s in data["settings"]}
    assert reset["budget.default_limit"] == "60"
