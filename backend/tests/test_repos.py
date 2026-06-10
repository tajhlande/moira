import pytest

from moira.persistence.interfaces import DEFAULT_USER_ID
from moira.persistence.sqlite.repos import (
    SqliteConversationRepository,
    SqliteModelPreferencesRepository,
)
from moira.persistence.sqlite.schema import run_migrations


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def conversation_repo(db_path):
    run_migrations(db_path)
    return SqliteConversationRepository(db_path)


@pytest.fixture
def prefs_repo(db_path):
    run_migrations(db_path)
    return SqliteModelPreferencesRepository(db_path)


@pytest.mark.asyncio
async def test_create_and_get_conversation(conversation_repo):
    conversation = await conversation_repo.create_conversation(
        DEFAULT_USER_ID, "Test Conversation"
    )
    assert conversation.id
    assert conversation.title == "Test Conversation"
    assert conversation.user_id == DEFAULT_USER_ID

    fetched = await conversation_repo.get_conversation(conversation.id)
    assert fetched is not None
    assert fetched.id == conversation.id
    assert fetched.title == "Test Conversation"


@pytest.mark.asyncio
async def test_list_conversations(conversation_repo):
    c1 = await conversation_repo.create_conversation(DEFAULT_USER_ID, "Conversation 1")
    c2 = await conversation_repo.create_conversation(DEFAULT_USER_ID, "Conversation 2")
    conversations = await conversation_repo.list_conversations(DEFAULT_USER_ID)
    ids = {c.id for c in conversations}
    assert c1.id in ids
    assert c2.id in ids


@pytest.mark.asyncio
async def test_insert_and_get_messages(conversation_repo):
    conversation = await conversation_repo.create_conversation(DEFAULT_USER_ID, "Chat")
    await conversation_repo.insert_message(conversation.id, "user", "Hello")
    await conversation_repo.insert_message(conversation.id, "assistant", "Hi there")

    messages = await conversation_repo.get_messages(conversation.id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Hello"
    assert messages[1].role == "assistant"
    assert messages[1].content == "Hi there"


@pytest.mark.asyncio
async def test_get_conversation_not_found(conversation_repo):
    result = await conversation_repo.get_conversation("nonexistent")
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


@pytest.mark.asyncio
async def test_truncate_from_message(conversation_repo):
    """truncate_from_message keeps the user message, deletes everything after."""
    from moira.persistence.interfaces import WorkflowRun

    conv = await conversation_repo.create_conversation(DEFAULT_USER_ID, "Chat")
    u1 = await conversation_repo.insert_message(conv.id, "user", "Q1")
    a1 = await conversation_repo.insert_message(conv.id, "assistant_report", "A1")
    u2 = await conversation_repo.insert_message(conv.id, "user", "Q2")
    await conversation_repo.insert_message(conv.id, "assistant_report", "A2")

    await conversation_repo.save_workflow_run(
        WorkflowRun(
            id="run-1",
            conversation_id=conv.id,
            user_message_id=u1.id,
            thread_id="t1",
            tool_executions="[]",
            report=None,
            budget_limit=50,
            budget_consumed=10,
            error="",
            status="completed",
            state_version=1,
            started_at="2025-01-01",
            completed_at="2025-01-01",
            updated_at="2025-01-01",
            total_elapsed_ms=100,
        )
    )
    await conversation_repo.save_workflow_run(
        WorkflowRun(
            id="run-2",
            conversation_id=conv.id,
            user_message_id=u2.id,
            thread_id="t2",
            tool_executions="[]",
            report=None,
            budget_limit=50,
            budget_consumed=20,
            error="",
            status="completed",
            state_version=1,
            started_at="2025-01-01",
            completed_at="2025-01-01",
            updated_at="2025-01-01",
            total_elapsed_ms=200,
        )
    )

    deleted = await conversation_repo.truncate_from_message(conv.id, u2.id)
    assert deleted is True

    messages = await conversation_repo.get_messages(conv.id)
    assert len(messages) == 3
    assert messages[0].id == u1.id
    assert messages[1].id == a1.id
    assert messages[2].id == u2.id
    assert u2.content == "Q2"

    runs = await conversation_repo.get_workflow_runs(conv.id)
    assert len(runs) == 1
    assert runs[0].id == "run-1"


@pytest.mark.asyncio
async def test_truncate_from_first_message(conversation_repo):
    """Truncating from the first message removes both runs."""
    from moira.persistence.interfaces import WorkflowRun

    conv = await conversation_repo.create_conversation(DEFAULT_USER_ID, "Chat")
    u1 = await conversation_repo.insert_message(conv.id, "user", "Q1")
    await conversation_repo.insert_message(conv.id, "assistant_report", "A1")
    await conversation_repo.insert_message(conv.id, "user", "Q2")

    await conversation_repo.save_workflow_run(
        WorkflowRun(
            id="run-1",
            conversation_id=conv.id,
            user_message_id=u1.id,
            thread_id="t1",
            tool_executions="[]",
            report=None,
            budget_limit=50,
            budget_consumed=10,
            error="",
            status="completed",
            state_version=1,
            started_at="2025-01-01",
            completed_at="2025-01-01",
            updated_at="2025-01-01",
            total_elapsed_ms=100,
        )
    )

    deleted = await conversation_repo.truncate_from_message(conv.id, u1.id)
    assert deleted is True

    messages = await conversation_repo.get_messages(conv.id)
    assert len(messages) == 1
    assert messages[0].id == u1.id
    assert messages[0].content == "Q1"

    runs = await conversation_repo.get_workflow_runs(conv.id)
    assert len(runs) == 0
