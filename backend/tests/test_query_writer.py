"""Unit tests for the delegated query-writer pass (Phase 3).

The writer is contract-tested with a mocked client: register vocab and
length bounds come from the module constants, so these tests pin the
normalization behavior the research loop relies on (never empty-string,
never overlong, never a repeat of something already tried, never more
than MAX_QUERIES, and total failure → empty list).
"""

import json
from unittest.mock import AsyncMock

from moira.inference.client import ChatResponse, InferenceClient
from moira.workflow.nodes.query_writer import (
    MAX_QUERIES,
    MAX_QUERY_CHARS,
    write_queries,
)


def _client(payload: dict | str) -> AsyncMock:
    """Build a mocked InferenceClient capturing the messages it receives."""
    content = payload if isinstance(payload, str) else json.dumps(payload)
    mock = AsyncMock(spec=InferenceClient)
    mock.chat_completion = AsyncMock(
        return_value=ChatResponse(content=content, model="test-model")
    )
    return mock


async def test_write_queries_passthrough_register_diverse_set():
    client = _client(
        {
            "queries": [
                {"query": "monstera deliciosa light tolerance", "register": "technical"},
                {"query": "why do monstera leaves yellow", "register": "question"},
                {
                    "query": "monstera light guide site:rhs.org.uk",
                    "register": "site_scoped",
                },
            ]
        }
    )
    out = await write_queries(
        "light tolerance range", "Monstera deliciosa", "", [], [], client, "m1"
    )
    assert [q["register"] for q in out] == ["technical", "question", "site_scoped"]
    assert out[0]["query"] == "monstera deliciosa light tolerance"


async def test_write_queries_enforces_length_cap():
    client = _client(
        {
            "queries": [
                {"query": "x" * 200, "register": "technical"},
                {"query": "short one", "register": "question"},
            ]
        }
    )
    out = await write_queries("f", "s", "", [], [], client)
    assert all(len(q["query"]) <= MAX_QUERY_CHARS for q in out)
    assert out[0]["query"] == "x" * MAX_QUERY_CHARS


async def test_write_queries_suppresses_already_tried():
    client = _client(
        {
            "queries": [
                # Same query modulo case/whitespace as a tried one.
                {"query": "  Monstera   Light Tolerance ", "register": "technical"},
                {"query": "monstera light needs site:rhs.org.uk", "register": "site_scoped"},
            ]
        }
    )
    out = await write_queries("f", "s", "", ["monstera light tolerance"], [], client)
    assert [q["query"] for q in out] == ["monstera light needs site:rhs.org.uk"]


async def test_write_queries_caps_count_and_dedups():
    client = _client(
        {
            "queries": [
                {"query": "a query", "register": "technical"},
                {"query": "a query", "register": "question"},  # dup — dropped
                {"query": "b query", "register": "question"},
                {"query": "c query", "register": "site_scoped"},
                {"query": "d query", "register": "technical"},  # over cap
            ]
        }
    )
    out = await write_queries("f", "s", "", [], [], client)
    assert len(out) == MAX_QUERIES
    assert [q["query"] for q in out] == ["a query", "b query", "c query"]


async def test_write_queries_bad_json_returns_empty():
    client = _client("the queries are: monstera light, monstera water")
    out = await write_queries("f", "s", "", [], [], client)
    assert out == []


async def test_write_queries_client_error_returns_empty():
    client = AsyncMock(spec=InferenceClient)
    client.chat_completion = AsyncMock(side_effect=RuntimeError("boom"))
    out = await write_queries("f", "s", "", [], [], client)
    assert out == []


async def test_write_queries_prompt_carries_context():
    client = _client({"queries": []})
    await write_queries(
        "required light intensity",
        "Monstera deliciosa",
        "care guides with measured lux values",
        ["monstera light tolerance"],
        ["RHS guide: bright indirect light"],
        client,
        "m1",
    )
    messages = client.chat_completion.call_args.kwargs["messages"]
    user_text = messages[1]["content"]
    assert "required light intensity" in user_text
    assert "Monstera deliciosa" in user_text
    assert "measured lux values" in user_text
    assert "monstera light tolerance" in user_text
    assert "bright indirect light" in user_text
    assert messages[0]["role"] == "system"
    # Low temperature: query writing should be near-deterministic.
    assert client.chat_completion.call_args.kwargs["temperature"] == 0.2
