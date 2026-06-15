"""Tests for tool description enrichment."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moira.tools.base import ToolDefinition
from moira.tools.enrichment import enrich_tool_descriptions


def _make_tool(name="test_tool", description="A test tool", **kwargs):
    return ToolDefinition(
        name=name,
        description=description,
        argument_schema=kwargs.get("argument_schema", {"type": "object", "properties": {}}),
        group_name=kwargs.get("group_name", "test"),
        tags=kwargs.get("tags", []),
        enabled=kwargs.get("enabled", True),
        original_description=kwargs.get("original_description", ""),
    )


def _make_mock_registry(response_contents):
    """Build a mock registry that returns the given content(s).

    If response_contents is a list, each call returns the next item.
    Otherwise the same content is returned every time.
    """
    if isinstance(response_contents, str):
        response_contents = [response_contents]

    mock_client = MagicMock()
    responses = [
        MagicMock(content=rc) for rc in response_contents
    ]
    mock_client.chat_completion = AsyncMock(side_effect=responses)

    mock_resolved = MagicMock()
    mock_resolved.client = mock_client
    mock_resolved.model_id = "test-model"

    mock_registry = MagicMock()
    mock_registry.resolve = AsyncMock(return_value=mock_resolved)
    return mock_registry


@pytest.mark.asyncio
async def test_enrich_returns_enriched_description():
    tool = _make_tool(
        name="pokeapi",
        description="GET /api/v2/pokemon/{name}",
    )

    enriched_text = (
        "Answers questions about Pokemon species: typing, "
        "base stats, and abilities."
    )
    mock_registry = _make_mock_registry(enriched_text)
    with patch("moira.tools.enrichment.service_provider", return_value=mock_registry):
        result = await enrich_tool_descriptions([tool])

    assert result["pokeapi"] == enriched_text


@pytest.mark.asyncio
async def test_enrich_multiple_tools_each_get_own_call():
    """Each tool should get its own model call with its own response."""
    tools = [
        _make_tool(name="pokeapi", description="Get Pokemon data"),
        _make_tool(name="web_search", description="Search the web"),
    ]

    mock_registry = _make_mock_registry([
        "Answers Pokemon species questions.",
        "Finds web pages matching a search query.",
    ])
    with patch("moira.tools.enrichment.service_provider", return_value=mock_registry):
        result = await enrich_tool_descriptions(tools)

    assert result["pokeapi"] == "Answers Pokemon species questions."
    assert result["web_search"] == "Finds web pages matching a search query."
    assert mock_registry.resolve.return_value.client.chat_completion.call_count == 2


@pytest.mark.asyncio
async def test_enrich_returns_empty_when_no_model_registry():
    tools = [_make_tool()]
    with patch("moira.tools.enrichment.service_provider", side_effect=RuntimeError("no service")):
        result = await enrich_tool_descriptions(tools)
    assert result == {}


@pytest.mark.asyncio
async def test_enrich_returns_empty_on_model_resolve_failure():
    tools = [_make_tool()]
    mock_registry = MagicMock()
    mock_registry.resolve = AsyncMock(side_effect=ValueError("no task model"))
    with patch("moira.tools.enrichment.service_provider", return_value=mock_registry):
        result = await enrich_tool_descriptions(tools)
    assert result == {}


@pytest.mark.asyncio
async def test_enrich_returns_empty_on_empty_model_response():
    tools = [_make_tool(name="my_tool")]
    mock_registry = _make_mock_registry("")
    with patch("moira.tools.enrichment.service_provider", return_value=mock_registry):
        result = await enrich_tool_descriptions(tools)
    assert result == {}


@pytest.mark.asyncio
async def test_enrich_returns_empty_on_model_call_error():
    tools = [_make_tool(name="my_tool")]
    mock_client = MagicMock()
    mock_client.chat_completion = AsyncMock(side_effect=RuntimeError("network error"))
    mock_resolved = MagicMock()
    mock_resolved.client = mock_client
    mock_resolved.model_id = "test-model"
    mock_registry = MagicMock()
    mock_registry.resolve = AsyncMock(return_value=mock_resolved)
    with patch("moira.tools.enrichment.service_provider", return_value=mock_registry):
        result = await enrich_tool_descriptions(tools)
    assert result == {}


@pytest.mark.asyncio
async def test_enrich_continues_on_individual_tool_failure():
    """If one tool's enrichment fails, others should still be enriched."""
    tools = [
        _make_tool(name="good_tool", description="A good tool"),
        _make_tool(name="bad_tool", description="A bad tool"),
        _make_tool(name="another_good", description="Another good tool"),
    ]

    mock_client = MagicMock()
    mock_client.chat_completion = AsyncMock(side_effect=[
        MagicMock(content="Enriched good_tool."),
        RuntimeError("transient error"),
        MagicMock(content="Enriched another_good."),
    ])
    mock_resolved = MagicMock()
    mock_resolved.client = mock_client
    mock_resolved.model_id = "test-model"
    mock_registry = MagicMock()
    mock_registry.resolve = AsyncMock(return_value=mock_resolved)

    with patch("moira.tools.enrichment.service_provider", return_value=mock_registry):
        result = await enrich_tool_descriptions(tools)

    assert "bad_tool" not in result
    assert result["good_tool"] == "Enriched good_tool."
    assert result["another_good"] == "Enriched another_good."


@pytest.mark.asyncio
async def test_enrich_prompt_includes_parameter_names():
    """Verify the prompt includes parameter names for context."""
    tool = _make_tool(
        name="pokeapi",
        description="Get Pokemon data",
        argument_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "fields": {"type": "string"},
            },
        },
    )
    mock_registry = _make_mock_registry("Enriched.")
    with patch("moira.tools.enrichment.service_provider", return_value=mock_registry):
        await enrich_tool_descriptions([tool])

    chat_call = mock_registry.resolve.return_value.client.chat_completion.call_args
    messages = chat_call.kwargs.get("messages") or chat_call.args[0]
    user_msg = messages[-1]["content"]
    assert "pokeapi" in user_msg
    assert "name" in user_msg
    assert "fields" in user_msg
