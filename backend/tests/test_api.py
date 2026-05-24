import pytest
from fastapi.testclient import TestClient

from moira.config import (
    InferenceConfig,
    InferenceEndpointConfig,
    InferenceModelsConfig,
    MoiraConfig,
)
from moira.persistence.interfaces import (
    Message,
    ModelPreferences,
    ModelPreferencesRepository,
    Session,
    SessionRepository,
)
from moira.service_setup import init_services, shutdown_services


class FakeSessionRepo(SessionRepository):
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._counter = 0

    async def create_session(self, user_id: str, title: str) -> Session:
        import uuid
        from datetime import datetime, timezone

        sid = str(uuid.uuid4())
        s = Session(
            id=sid,
            user_id=user_id,
            title=title,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._sessions[sid] = s
        return s

    async def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    async def list_sessions(self, user_id: str) -> list[Session]:
        return list(self._sessions.values())

    async def insert_message(self, session_id: str, role: str, content: str):
        from datetime import datetime, timezone

        self._counter += 1
        return Message(
            id=self._counter,
            session_id=session_id,
            role=role,
            content=content,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    async def get_messages(self, session_id: str):
        return []


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


def _config():
    return MoiraConfig(
        inference=InferenceConfig(
            endpoints=[InferenceEndpointConfig(name="test", base_url="http://localhost:9999/v1")],
            models=InferenceModelsConfig(
                intelligence_endpoint="test",
                intelligence_model="test-model",
                task_endpoint="",
                task_model="",
            ),
        )
    )


@pytest.fixture
async def app_client():
    config = _config()
    session_repo = FakeSessionRepo()
    prefs_repo = FakePrefsRepo()
    await init_services(config, session_repo=session_repo, prefs_repo=prefs_repo)
    from moira.main import create_app

    app = create_app()
    client = TestClient(app)
    yield client
    await shutdown_services()


def test_health_endpoint(app_client):
    resp = app_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_create_session(app_client):
    resp = app_client.post("/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["title"] == "New Session"


def test_list_sessions(app_client):
    app_client.post("/api/sessions")
    app_client.post("/api/sessions")
    resp = app_client.get("/api/sessions")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_session(app_client):
    create_resp = app_client.post("/api/sessions")
    session_id = create_resp.json()["id"]

    get_resp = app_client.get(f"/api/sessions/{session_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == session_id


def test_get_session_not_found(app_client):
    resp = app_client.get("/api/sessions/nonexistent")
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
