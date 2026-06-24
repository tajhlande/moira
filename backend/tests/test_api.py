from pathlib import Path

import pytest
import pytest_asyncio
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

    async def get_workflow_run(self, run_id: str) -> WorkflowRun | None:
        return None

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


@pytest_asyncio.fixture(scope="module")
async def _app_setup(tmp_path_factory):
    """Module-scoped expensive setup: migrations, init_services, create_app.

    Runs once per module. Function-scoped wrappers (app_client,
    app_client_with_runs) reset repo state between tests.
    """
    import os

    tmp_path = tmp_path_factory.mktemp("api_tests")
    data_dir = str(tmp_path / "moira_data")
    os.makedirs(data_dir, exist_ok=True)
    os.environ["MOIRA_DATA_DIR"] = data_dir

    config = _config(str(tmp_path))
    conversation_repo = WorkflowRunAwareRepo()
    prefs_repo = FakePrefsRepo()

    from moira.config import resolve_db_path
    from moira.persistence.sqlite.schema import run_migrations

    run_migrations(resolve_db_path(config))

    await init_services(config, conversation_repo=conversation_repo, prefs_repo=prefs_repo)
    from moira.main import create_app

    app = create_app()
    client = TestClient(app)
    yield client, conversation_repo
    await shutdown_services()
    os.environ.pop("MOIRA_DATA_DIR", None)


def _reset_repo(repo):
    """Clear in-memory repo state for test isolation."""
    repo._conversations.clear()
    repo._counter = 0
    repo._runs.clear()


@pytest.fixture
async def app_client(_app_setup):
    """Function-scoped TestClient with clean repo state."""
    client, repo = _app_setup
    _reset_repo(repo)
    yield client


@pytest.fixture
async def app_client_with_runs(_app_setup):
    """Function-scoped TestClient + repo with clean state."""
    client, repo = _app_setup
    _reset_repo(repo)
    yield client, repo


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
                {"key": "budget.cost.research_review", "value": "3"},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    keys = {s["key"] for s in data["settings"]}
    assert "budget.cost.planning" in keys
    assert "budget.cost.research_review" in keys


def test_reset_settings(app_client):
    app_client.put("/api/settings/budget.default_limit", json={"value": 99})

    resp = app_client.delete("/api/settings?keys=budget.default_limit")
    assert resp.status_code == 200
    data = resp.json()
    reset = {s["key"]: s["value"] for s in data["settings"]}
    assert reset["budget.default_limit"] == "100"


class WorkflowRunAwareRepo(FakeConversationRepo):
    def __init__(self):
        super().__init__()
        self._runs: dict[str, WorkflowRun] = {}

    async def save_workflow_run(self, run: WorkflowRun) -> None:
        self._runs[run.id] = run

    async def get_workflow_run(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    async def get_workflow_runs(self, conversation_id: str) -> list[WorkflowRun]:
        return [r for r in self._runs.values() if r.conversation_id == conversation_id]


def test_knowledge_endpoint_no_knowledge(app_client_with_runs):
    client, repo = app_client_with_runs
    run = WorkflowRun(
        id="run-1",
        conversation_id="conv-1",
        user_message_id=1,
        status="completed",
        knowledge_snapshot="",
    )
    import asyncio

    asyncio.get_event_loop().run_until_complete(repo.save_workflow_run(run))

    resp = client.get("/api/runs/run-1/knowledge")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-1"
    assert data["knowledge"] is None


def test_knowledge_endpoint_with_knowledge(app_client_with_runs):
    import asyncio
    import json

    client, repo = app_client_with_runs
    knowledge_data = {
        "question": "What is Python?",
        "user_goal": "Learn about Python",
        "topic": "programming",
        "entities": ["Python"],
        "concepts": ["programming language"],
        "facts": {
            "unknown": [
                {
                    "id": "f1",
                    "subject": "Python",
                    "fact_needed": "What is it?",
                    "status": "unknown",
                }
            ]
        },
        "conclusions": {
            "verified": [
                {
                    "id": "c1",
                    "conclusion": "Python is a language",
                    "supporting_fact_ids": ["f1"],
                    "status": "verified",
                }
            ]
        },
        "citations": [],
    }
    run = WorkflowRun(
        id="run-2",
        conversation_id="conv-1",
        user_message_id=1,
        status="completed",
        knowledge_snapshot=json.dumps(knowledge_data),
    )
    asyncio.get_event_loop().run_until_complete(repo.save_workflow_run(run))

    resp = client.get("/api/runs/run-2/knowledge")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-2"
    assert data["knowledge"]["question"] == "What is Python?"
    assert data["knowledge"]["facts"]["unknown"][0]["subject"] == "Python"


def test_knowledge_endpoint_not_found(app_client_with_runs):
    client, _ = app_client_with_runs
    resp = client.get("/api/runs/nonexistent/knowledge")
    assert resp.status_code == 404


def test_coalesced_run_snapshot_includes_knowledge():
    import json

    from moira.api.routes.conversations import _coalesced_run_snapshot

    knowledge_data = {
        "question": "test",
        "user_goal": "test goal",
        "topic": "test topic",
        "entities": [],
        "concepts": [],
        "facts": {},
        "conclusions": {},
        "citations": [],
    }
    run = WorkflowRun(
        id="run-1",
        conversation_id="conv-1",
        user_message_id=1,
        status="completed",
        knowledge_snapshot=json.dumps(knowledge_data),
    )
    result = _coalesced_run_snapshot([run], {})
    assert result["knowledge"] == knowledge_data


def test_coalesced_run_snapshot_knowledge_null_when_empty():
    from moira.api.routes.conversations import _coalesced_run_snapshot

    run = WorkflowRun(
        id="run-1",
        conversation_id="conv-1",
        user_message_id=1,
        status="completed",
        knowledge_snapshot="",
    )
    result = _coalesced_run_snapshot([run], {})
    assert result["knowledge"] is None


def test_coalesced_run_snapshot_knowledge_null_when_invalid_json():
    from moira.api.routes.conversations import _coalesced_run_snapshot

    run = WorkflowRun(
        id="run-1",
        conversation_id="conv-1",
        user_message_id=1,
        status="completed",
        knowledge_snapshot="{invalid json",
    )
    result = _coalesced_run_snapshot([run], {})
    assert result["knowledge"] is None
