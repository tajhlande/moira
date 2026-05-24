import pytest

from moira.persistence.interfaces import DEFAULT_USER_ID
from moira.persistence.sqlite.repos import (
    SqliteModelPreferencesRepository,
    SqliteSessionRepository,
)
from moira.persistence.sqlite.schema import run_migrations


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def session_repo(db_path):
    run_migrations(db_path)
    return SqliteSessionRepository(db_path)


@pytest.fixture
def prefs_repo(db_path):
    run_migrations(db_path)
    return SqliteModelPreferencesRepository(db_path)


@pytest.mark.asyncio
async def test_create_and_get_session(session_repo):
    session = await session_repo.create_session(DEFAULT_USER_ID, "Test Session")
    assert session.id
    assert session.title == "Test Session"
    assert session.user_id == DEFAULT_USER_ID

    fetched = await session_repo.get_session(session.id)
    assert fetched is not None
    assert fetched.id == session.id
    assert fetched.title == "Test Session"


@pytest.mark.asyncio
async def test_list_sessions(session_repo):
    s1 = await session_repo.create_session(DEFAULT_USER_ID, "Session 1")
    s2 = await session_repo.create_session(DEFAULT_USER_ID, "Session 2")
    sessions = await session_repo.list_sessions(DEFAULT_USER_ID)
    ids = {s.id for s in sessions}
    assert s1.id in ids
    assert s2.id in ids


@pytest.mark.asyncio
async def test_insert_and_get_messages(session_repo):
    session = await session_repo.create_session(DEFAULT_USER_ID, "Chat")
    await session_repo.insert_message(session.id, "user", "Hello")
    await session_repo.insert_message(session.id, "assistant", "Hi there")

    messages = await session_repo.get_messages(session.id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Hello"
    assert messages[1].role == "assistant"
    assert messages[1].content == "Hi there"


@pytest.mark.asyncio
async def test_get_session_not_found(session_repo):
    result = await session_repo.get_session("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_model_preferences_defaults(prefs_repo):
    prefs = await prefs_repo.get_preferences(DEFAULT_USER_ID)
    assert prefs.user_id == DEFAULT_USER_ID
    assert prefs.intelligence_endpoint == ""
    assert prefs.intelligence_model == ""
    assert prefs.task_endpoint == ""
    assert prefs.task_model == ""


@pytest.mark.asyncio
async def test_set_and_get_model_preferences(prefs_repo):
    from moira.persistence.interfaces import ModelPreferences

    await prefs_repo.set_preferences(
        ModelPreferences(
            user_id=DEFAULT_USER_ID,
            intelligence_endpoint="local",
            intelligence_model="llama3:70b",
            task_endpoint="cloud",
            task_model="gpt-4o-mini",
        )
    )
    prefs = await prefs_repo.get_preferences(DEFAULT_USER_ID)
    assert prefs.intelligence_endpoint == "local"
    assert prefs.intelligence_model == "llama3:70b"
    assert prefs.task_endpoint == "cloud"
    assert prefs.task_model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_update_model_preferences(prefs_repo):
    from moira.persistence.interfaces import ModelPreferences

    await prefs_repo.set_preferences(
        ModelPreferences(
            user_id=DEFAULT_USER_ID,
            intelligence_endpoint="local",
            intelligence_model="model-a",
            task_endpoint="local",
            task_model="model-b",
        )
    )
    await prefs_repo.set_preferences(
        ModelPreferences(
            user_id=DEFAULT_USER_ID,
            intelligence_endpoint="cloud",
            intelligence_model="model-c",
            task_endpoint="cloud",
            task_model="model-d",
        )
    )
    prefs = await prefs_repo.get_preferences(DEFAULT_USER_ID)
    assert prefs.intelligence_endpoint == "cloud"
    assert prefs.intelligence_model == "model-c"
    assert prefs.task_endpoint == "cloud"
    assert prefs.task_model == "model-d"
