"""Tests for SqliteToolRepository: round-trip persistence of tool definitions
including Phase C invocation_cost and call_limit_per_run fields."""

import sqlite3

import pytest

from moira.persistence.sqlite.repos import SqliteToolRepository
from moira.persistence.sqlite.schema import run_migrations
from moira.tools.base import ToolDefinition


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def tool_repo(db_path):
    run_migrations(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR IGNORE INTO tool_groups (name, display_name) VALUES ('', 'Default')")
    conn.commit()
    conn.close()
    return SqliteToolRepository(db_path)


@pytest.mark.asyncio
async def test_save_and_retrieve_default_costs(tool_repo):
    tool = ToolDefinition(
        name="test_tool",
        description="A test tool",
        argument_schema={"type": "object", "properties": {}},
        config={},
        tags=[],
    )
    await tool_repo.save_tool(tool)

    retrieved = await tool_repo.get_tool("test_tool")
    assert retrieved is not None
    assert retrieved.name == "test_tool"
    assert retrieved.invocation_cost == 1.0
    assert retrieved.call_limit_per_run == 0


@pytest.mark.asyncio
async def test_save_and_retrieve_custom_cost_and_limit(tool_repo):
    tool = ToolDefinition(
        name="web_search",
        description="Search the web",
        argument_schema={"type": "object", "properties": {}},
        config={},
        tags=["search"],
        invocation_cost=5.0,
        call_limit_per_run=10,
    )
    await tool_repo.save_tool(tool)

    retrieved = await tool_repo.get_tool("web_search")
    assert retrieved is not None
    assert retrieved.invocation_cost == 5.0
    assert retrieved.call_limit_per_run == 10


@pytest.mark.asyncio
async def test_save_and_retrieve_zero_call_limit(tool_repo):
    tool = ToolDefinition(
        name="calculator",
        description="Do math",
        argument_schema={},
        config={},
        tags=[],
        invocation_cost=0.1,
        call_limit_per_run=0,
    )
    await tool_repo.save_tool(tool)

    retrieved = await tool_repo.get_tool("calculator")
    assert retrieved is not None
    assert retrieved.invocation_cost == 0.1
    assert retrieved.call_limit_per_run == 0


@pytest.mark.asyncio
async def test_get_all_tools_with_costs(tool_repo):
    tools = [
        ToolDefinition(
            name="web_search",
            description="Search",
            invocation_cost=5.0,
            call_limit_per_run=10,
        ),
        ToolDefinition(
            name="calculator",
            description="Math",
            invocation_cost=0.1,
        ),
        ToolDefinition(
            name="url_content",
            description="Fetch URL",
            invocation_cost=3.0,
            call_limit_per_run=15,
        ),
    ]
    for t in tools:
        await tool_repo.save_tool(t)

    all_tools = await tool_repo.get_all_tools()
    by_name = {t.name: t for t in all_tools}

    assert by_name["web_search"].invocation_cost == 5.0
    assert by_name["web_search"].call_limit_per_run == 10
    assert by_name["calculator"].invocation_cost == 0.1
    assert by_name["calculator"].call_limit_per_run == 0
    assert by_name["url_content"].invocation_cost == 3.0
    assert by_name["url_content"].call_limit_per_run == 15


@pytest.mark.asyncio
async def test_update_tool_preserves_cost_and_limit(tool_repo):
    original = ToolDefinition(
        name="web_search",
        description="Search the web",
        invocation_cost=5.0,
        call_limit_per_run=10,
    )
    await tool_repo.save_tool(original)

    updated = ToolDefinition(
        name="web_search",
        description="Search the web - updated",
        invocation_cost=7.0,
        call_limit_per_run=20,
    )
    await tool_repo.save_tool(updated)

    retrieved = await tool_repo.get_tool("web_search")
    assert retrieved.description == "Search the web - updated"
    assert retrieved.invocation_cost == 7.0
    assert retrieved.call_limit_per_run == 20


@pytest.mark.asyncio
async def test_to_dict_includes_cost_and_limit(tool_repo):
    tool = ToolDefinition(
        name="test",
        description="test",
        invocation_cost=3.0,
        call_limit_per_run=5,
    )
    await tool_repo.save_tool(tool)

    retrieved = await tool_repo.get_tool("test")
    d = retrieved.to_dict()
    assert d["invocation_cost"] == 3.0
    assert d["call_limit_per_run"] == 5


@pytest.mark.asyncio
async def test_save_zero_call_limit_persists_as_zero_not_null(tool_repo, db_path):
    """Regression: save_tool previously converted 0 → NULL, which broke
    the distinction between 'no limit' and 'unset'.  Verify 0 is stored
    as 0 in the raw database column."""
    tool = ToolDefinition(
        name="no_limit_tool",
        description="Tool with no call limit",
        invocation_cost=0.1,
        call_limit_per_run=0,
    )
    await tool_repo.save_tool(tool)

    # Check the raw DB column — should be 0, not NULL
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT call_limit_per_run FROM tools WHERE name = ?",
        ("no_limit_tool",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 0  # Stored as integer 0, not NULL


@pytest.mark.asyncio
async def test_save_tool_round_trips_zero_limit(tool_repo):
    """Verify that call_limit_per_run=0 survives a save→read round-trip."""
    tool = ToolDefinition(
        name="unlimited_tool",
        description="Unlimited calls",
        invocation_cost=1.0,
        call_limit_per_run=0,
    )
    await tool_repo.save_tool(tool)

    retrieved = await tool_repo.get_tool("unlimited_tool")
    assert retrieved is not None
    assert retrieved.call_limit_per_run == 0


@pytest.mark.asyncio
async def test_save_and_retrieve_per_step_limit(tool_repo):
    """call_limit_per_step round-trips correctly."""
    tool = ToolDefinition(
        name="stepped_tool",
        description="Tool with per-step limit",
        invocation_cost=5.0,
        call_limit_per_run=10,
        call_limit_per_step=3,
    )
    await tool_repo.save_tool(tool)

    retrieved = await tool_repo.get_tool("stepped_tool")
    assert retrieved is not None
    assert retrieved.call_limit_per_run == 10
    assert retrieved.call_limit_per_step == 3


@pytest.mark.asyncio
async def test_save_zero_per_step_persists(tool_repo):
    """call_limit_per_step=0 (unlimited) persists as 0, not NULL."""
    tool = ToolDefinition(
        name="no_step_limit",
        description="No per-step cap",
        invocation_cost=1.0,
        call_limit_per_step=0,
    )
    await tool_repo.save_tool(tool)

    retrieved = await tool_repo.get_tool("no_step_limit")
    assert retrieved is not None
    assert retrieved.call_limit_per_step == 0


@pytest.mark.asyncio
async def test_to_dict_includes_per_step(tool_repo):
    """to_dict should include call_limit_per_step."""
    tool = ToolDefinition(
        name="dict_test",
        description="test",
        invocation_cost=3.0,
        call_limit_per_run=10,
        call_limit_per_step=5,
    )
    await tool_repo.save_tool(tool)

    retrieved = await tool_repo.get_tool("dict_test")
    d = retrieved.to_dict()
    assert d["call_limit_per_run"] == 10
    assert d["call_limit_per_step"] == 5
