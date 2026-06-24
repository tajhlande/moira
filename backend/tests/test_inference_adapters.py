"""Tests for the tool-calling adapters and inference client tool support."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from moira.inference.adapters import OpenAICompletionsAdapter, get_adapter
from moira.inference.client import ChatResponse, InferenceClient
from moira.tools.base import ToolCall, ToolDefinition

# ---------------------------------------------------------------------------
# OpenAICompletionsAdapter
# ---------------------------------------------------------------------------


class TestOpenAICompletionsAdapterFormatTools:
    def test_basic_format(self):
        adapter = OpenAICompletionsAdapter()
        tools = [
            ToolDefinition(
                name="web_search",
                description="Search the web",
                argument_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            ),
        ]
        result = adapter.format_tools(tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "web_search"
        assert result[0]["function"]["description"] == "Search the web"
        assert "properties" in result[0]["function"]["parameters"]

    def test_empty_list(self):
        adapter = OpenAICompletionsAdapter()
        assert adapter.format_tools([]) == []

    def test_multiple_tools(self):
        adapter = OpenAICompletionsAdapter()
        tools = [
            ToolDefinition(name="a", description="Tool A"),
            ToolDefinition(name="b", description="Tool B"),
        ]
        result = adapter.format_tools(tools)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "a"
        assert result[1]["function"]["name"] == "b"


class TestOpenAICompletionsAdapterParseToolCalls:
    def test_single_call(self):
        adapter = OpenAICompletionsAdapter()
        message = {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_001",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": '{"query": "test"}',
                    },
                }
            ],
        }
        calls = adapter.parse_tool_calls(message)
        assert len(calls) == 1
        assert calls[0].id == "call_001"
        assert calls[0].name == "web_search"
        assert calls[0].arguments == {"query": "test"}

    def test_multiple_calls(self):
        adapter = OpenAICompletionsAdapter()
        message = {
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"q": "a"}'},
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "calc", "arguments": '{"expr": "1+1"}'},
                },
            ],
        }
        calls = adapter.parse_tool_calls(message)
        assert len(calls) == 2
        assert calls[0].name == "search"
        assert calls[1].name == "calc"

    def test_empty_tool_calls(self):
        adapter = OpenAICompletionsAdapter()
        assert adapter.parse_tool_calls({"content": "hello"}) == []
        assert adapter.parse_tool_calls({"content": "", "tool_calls": []}) == []

    def test_missing_id_skipped(self):
        adapter = OpenAICompletionsAdapter()
        message = {
            "tool_calls": [
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
        }
        assert adapter.parse_tool_calls(message) == []

    def test_missing_name_skipped(self):
        adapter = OpenAICompletionsAdapter()
        message = {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "", "arguments": "{}"},
                }
            ],
        }
        assert adapter.parse_tool_calls(message) == []

    def test_malformed_arguments_default_empty(self):
        adapter = OpenAICompletionsAdapter()
        message = {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "not valid json{"},
                }
            ],
        }
        calls = adapter.parse_tool_calls(message)
        assert len(calls) == 1
        assert calls[0].arguments == {}

    def test_arguments_already_dict(self):
        """Some servers may send arguments as a dict instead of a JSON string."""
        adapter = OpenAICompletionsAdapter()
        message = {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": {"q": "direct"}},
                }
            ],
        }
        calls = adapter.parse_tool_calls(message)
        assert len(calls) == 1
        assert calls[0].arguments == {"q": "direct"}


class TestOpenAICompletionsAdapterToolResult:
    def test_format_tool_result(self):
        adapter = OpenAICompletionsAdapter()
        msg = adapter.format_tool_result("call_001", "search results here")
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_001"
        assert msg["content"] == "search results here"


class TestOpenAICompletionsAdapterAssistantMessage:
    def test_format_with_tool_calls(self):
        adapter = OpenAICompletionsAdapter()
        calls = [
            ToolCall(id="call_1", name="search", arguments={"q": "test"}),
        ]
        msg = adapter.format_assistant_message("thinking...", calls)
        assert msg["role"] == "assistant"
        assert msg["content"] == "thinking..."
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["id"] == "call_1"
        assert msg["tool_calls"][0]["type"] == "function"
        assert msg["tool_calls"][0]["function"]["name"] == "search"
        # Arguments should be a JSON string
        assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"q": "test"}

    def test_format_with_empty_content(self):
        adapter = OpenAICompletionsAdapter()
        calls = [ToolCall(id="call_1", name="search", arguments={})]
        msg = adapter.format_assistant_message("", calls)
        assert msg["content"] == ""


# ---------------------------------------------------------------------------
# get_adapter factory
# ---------------------------------------------------------------------------


class TestGetAdapter:
    def test_completions(self):
        adapter = get_adapter("completions")
        assert isinstance(adapter, OpenAICompletionsAdapter)

    def test_unknown_returns_none(self):
        assert get_adapter("unknown_provider") is None


# ---------------------------------------------------------------------------
# ChatResponse tool_calls field
# ---------------------------------------------------------------------------


class TestChatResponseToolCalls:
    def test_default_empty(self):
        resp = ChatResponse(content="hello")
        assert resp.tool_calls == []

    def test_with_tool_calls(self):
        calls = [ToolCall(id="c1", name="search", arguments={})]
        resp = ChatResponse(content="", tool_calls=calls)
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "search"


# ---------------------------------------------------------------------------
# InferenceClient with tools
# ---------------------------------------------------------------------------


class TestInferenceClientToolCalling:
    @pytest.mark.asyncio
    async def test_chat_completion_with_tools(self):
        """When tools are provided, the client should include them in the
        payload and parse tool_calls from the response."""
        client = InferenceClient(
            base_url="http://localhost:8080/v1",
            provider_type="completions",
        )

        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "model": "test-model",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_001",
                                "type": "function",
                                "function": {
                                    "name": "web_search",
                                    "arguments": '{"query": "test"}',
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        mock_response.text = ""

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        tools = [
            ToolDefinition(
                name="web_search",
                description="Search",
                argument_schema={"type": "object"},
            ),
        ]
        result = await client.chat_completion(
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
            tools=tools,
        )

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].id == "call_001"
        assert result.tool_calls[0].name == "web_search"
        assert result.tool_calls[0].arguments == {"query": "test"}

        # Verify tools were sent in the payload
        sent_payload = mock_http.post.call_args[1]["json"]
        assert "tools" in sent_payload
        assert sent_payload["tools"][0]["type"] == "function"
        assert sent_payload["tool_choice"] == "auto"

    @pytest.mark.asyncio
    async def test_chat_completion_without_tools(self):
        """When no tools are provided, the payload should not include them."""
        client = InferenceClient(
            base_url="http://localhost:8080/v1",
            provider_type="completions",
        )

        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "model": "test-model",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "hello world"},
                }
            ],
            "usage": {},
        }
        mock_response.text = ""

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        client._client = mock_http

        result = await client.chat_completion(
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
        )

        assert result.tool_calls == []
        sent_payload = mock_http.post.call_args[1]["json"]
        assert "tools" not in sent_payload
