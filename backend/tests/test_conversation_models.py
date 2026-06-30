"""Tests for SqliteConversationModelRepository.

Tests CRUD operations on the conversation_models table, including
upsert semantics (insert then update) and delete.

The conversations table is created by migration 001 so FK references
are valid. A test conversation is inserted before testing overrides."""

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from moira.persistence.interfaces import ConversationModelOverride
from moira.persistence.sqlite.repos.conversation_models import (
    SqliteConversationModelRepository,
)
from moira.persistence.sqlite.schema import run_migrations


@pytest_asyncio.fixture
async def repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        run_migrations(db_path)

        # Insert a conversation so FK references are valid
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO conversations (id, user_id, title, created_at) "
            "VALUES ('conv-1', 'default', 'Test', datetime('now'))"
        )
        conn.commit()
        conn.close()

        yield SqliteConversationModelRepository(db_path)


class TestConversationModelCRUD:
    @pytest.mark.asyncio
    async def test_get_no_override(self, repo):
        result = await repo.get_override("conv-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_and_get(self, repo):
        await repo.upsert_override(
            ConversationModelOverride(
                conversation_id="conv-1",
                intelligence_endpoint="open-router",
                intelligence_model="deepseek/deepseek-v4-flash",
            )
        )
        result = await repo.get_override("conv-1")
        assert result is not None
        assert result.conversation_id == "conv-1"
        assert result.intelligence_endpoint == "open-router"
        assert result.intelligence_model == "deepseek/deepseek-v4-flash"
        assert result.updated_at != ""

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, repo):
        await repo.upsert_override(
            ConversationModelOverride(
                conversation_id="conv-1",
                intelligence_endpoint="local-lab",
                intelligence_model="llama3:70b",
            )
        )
        await repo.upsert_override(
            ConversationModelOverride(
                conversation_id="conv-1",
                intelligence_endpoint="open-router",
                intelligence_model="gpt-4o",
            )
        )
        result = await repo.get_override("conv-1")
        assert result is not None
        assert result.intelligence_endpoint == "open-router"
        assert result.intelligence_model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_delete_override(self, repo):
        await repo.upsert_override(
            ConversationModelOverride(
                conversation_id="conv-1",
                intelligence_endpoint="local-lab",
                intelligence_model="llama3:70b",
            )
        )
        deleted = await repo.delete_override("conv-1")
        assert deleted is True
        assert await repo.get_override("conv-1") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, repo):
        deleted = await repo.delete_override("conv-1")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_multiple_conversations_independent(self, repo):
        # Insert a second conversation
        import sqlite3

        conn = sqlite3.connect(repo._db_path)
        conn.execute(
            "INSERT INTO conversations (id, user_id, title, created_at) "
            "VALUES ('conv-2', 'default', 'Test 2', datetime('now'))"
        )
        conn.commit()
        conn.close()

        await repo.upsert_override(
            ConversationModelOverride(
                conversation_id="conv-1",
                intelligence_endpoint="local-lab",
                intelligence_model="llama3:70b",
            )
        )
        await repo.upsert_override(
            ConversationModelOverride(
                conversation_id="conv-2",
                intelligence_endpoint="open-router",
                intelligence_model="gpt-4o",
            )
        )

        r1 = await repo.get_override("conv-1")
        r2 = await repo.get_override("conv-2")
        assert r1.intelligence_model == "llama3:70b"
        assert r2.intelligence_model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_cascade_on_conversation_delete(self, repo):
        await repo.upsert_override(
            ConversationModelOverride(
                conversation_id="conv-1",
                intelligence_endpoint="local-lab",
                intelligence_model="llama3:70b",
            )
        )
        assert await repo.get_override("conv-1") is not None

        # Delete the conversation — FK cascade should remove the override
        import sqlite3

        conn = sqlite3.connect(repo._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM conversations WHERE id = 'conv-1'")
        conn.commit()
        conn.close()

        assert await repo.get_override("conv-1") is None
