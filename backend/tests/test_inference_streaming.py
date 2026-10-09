"""Tests for InferenceClient streaming (SSE) support.

Streaming exists to keep slow models from tripping the read timeout: with
SSE the timeout applies between chunks rather than to the whole
generation. These tests cover the happy path (delta accumulation),
discovery (rejection fallback, JSON-body fallback), forced/disabled
modes, tool-call fragment assembly, and transient-error retry behavior
around streams.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from moira.inference.client import InferenceClient


def _sse_response(
    chunks: list[dict], status: int = 200, content_type: str = "text/event-stream"
) -> httpx.Response:
    """Build a real httpx.Response carrying SSE lines.

    Real responses matter here: the client reads the body via aread() on
    error paths and needs request= set for HTTPStatusError construction."""
    body = "".join(f"data: {json.dumps(c)}\n" for c in chunks) + "data: [DONE]\n"
    request = httpx.Request("POST", "http://test/chat/completions")
    return httpx.Response(
        status,
        content=body.encode(),
        headers={"content-type": content_type},
        request=request,
    )


def _response(status: int, payload: dict | None = None) -> httpx.Response:
    request = httpx.Request("POST", "http://test/chat/completions")
    return httpx.Response(status, json=payload or {}, request=request)


class _StreamContext:
    """Async context manager standing in for httpx.AsyncClient.stream()."""

    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc_info):
        return False


def _client_with_streams(responses: list, post_responses: list | None = None, **kwargs):
    """Build a client whose HTTP layer replays the given streaming
    responses (and optionally POST responses, for fallback tests)."""
    client = InferenceClient(base_url="http://test", **kwargs)
    http = MagicMock()
    http.stream = MagicMock(side_effect=[_StreamContext(r) for r in responses])
    if post_responses is not None:
        http.post = AsyncMock(side_effect=post_responses)
    client._client = http
    return client


class TestStreamingHappyPath:
    async def test_content_and_thinking_accumulated(self):
        chunks = [
            {"model": "test-model", "choices": [{"delta": {"reasoning_content": "think "}}]},
            {"model": "test-model", "choices": [{"delta": {"reasoning_content": "hard"}}]},
            {"model": "test-model", "choices": [{"delta": {"content": "Hello "}}]},
            {
                "model": "test-model",
                "choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}],
            },
            {
                "choices": [],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5},
                "timings": {"prompt_ms": 1.5, "predicted_ms": 2.5},
            },
        ]
        client = _client_with_streams([_sse_response(chunks)])
        result = await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        assert result.content == "Hello world"
        assert result.thinking == "think hard"
        assert result.finish_reason == "stop"
        assert result.model == "test-model"
        assert result.input_tokens == 3
        assert result.output_tokens == 5
        assert result.prompt_time_ms == 1.5
        assert result.gen_time_ms == 2.5
        assert client._stream_supported is True

    async def test_stream_and_usage_keys_sent(self):
        chunks = [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]
        client = _client_with_streams([_sse_response(chunks)])
        await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        sent = client._client.stream.call_args.kwargs["json"]
        assert sent["stream"] is True
        assert sent["stream_options"] == {"include_usage": True}

    async def test_second_call_reuses_stream_after_discovery(self):
        chunks = [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]
        client = _client_with_streams([_sse_response(chunks), _sse_response(chunks)])
        first = await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        second = await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        assert first.content == second.content == "ok"
        assert client._client.stream.call_count == 2

    async def test_extra_body_overrides_stream_keys(self):
        chunks = [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]
        client = _client_with_streams([_sse_response(chunks)])
        await client.chat_completion(
            "test-model",
            [{"role": "user", "content": "hi"}],
            extra_body={"stream": False, "stream_options": None},
        )
        # Client still consumes the (mocked) stream, but the payload the
        # server would see honors the caller's override.
        sent = client._client.stream.call_args.kwargs["json"]
        assert sent["stream"] is False
        assert sent["stream_options"] is None

    async def test_unparseable_and_empty_lines_skipped(self):
        chunks = [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]
        request = httpx.Request("POST", "http://test/chat/completions")
        body = (
            ": keep-alive comment\n\ndata: not-json\n"
            + _sse_response(chunks).text
            + "data: [DONE]\n"
        )
        raw = httpx.Response(
            200,
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
            request=request,
        )
        client = _client_with_streams([raw])
        result = await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        assert result.content == "ok"

    async def test_empty_stream_raises(self):
        client = _client_with_streams([_sse_response([])])
        with pytest.raises(RuntimeError, match="no choices"):
            await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])

    async def test_error_event_raises(self):
        chunks = [{"error": {"message": "model overloaded"}}]
        client = _client_with_streams([_sse_response(chunks)])
        with pytest.raises(RuntimeError, match="model overloaded"):
            await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])


class TestStreamingDiscovery:
    async def test_rejected_stream_falls_back_to_post(self):
        client = _client_with_streams(
            [_response(400, {"error": {"message": "stream not supported"}})],
            post_responses=[_response(200, {"choices": [{"message": {"content": "ok"}}]})],
        )
        result = await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        assert result.content == "ok"
        assert client._stream_supported is False

    async def test_cached_fallback_skips_stream_on_later_calls(self):
        client = _client_with_streams(
            [_response(400, {"error": {"message": "stream not supported"}})],
            post_responses=[_response(200, {"choices": [{"message": {"content": "ok"}}]})],
        )
        await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        client._client.post = AsyncMock(
            return_value=_response(200, {"choices": [{"message": {"content": "again"}}]})
        )
        result = await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        assert result.content == "again"
        # Only the initial discovery attempt used stream()
        assert client._client.stream.call_count == 1
        assert "stream" not in client._client.post.await_args.kwargs["json"]

    async def test_json_body_when_stream_ignored(self):
        client = _client_with_streams(
            [
                httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
                    headers={"content-type": "application/json"},
                    request=httpx.Request("POST", "http://test/chat/completions"),
                )
            ]
        )
        result = await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        assert result.content == "ok"
        assert client._stream_supported is False

    async def test_forced_stream_surfaces_error_without_fallback(self):
        client = _client_with_streams(
            [_response(400, {"error": {"message": "stream not supported"}})], stream=True
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        assert exc_info.value.response.status_code == 400
        client._client.post.assert_not_called()

    async def test_stream_false_never_attempts_stream(self):
        client = _client_with_streams(
            [],
            post_responses=[_response(200, {"choices": [{"message": {"content": "ok"}}]})],
            stream=False,
        )
        result = await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        assert result.content == "ok"
        client._client.stream.assert_not_called()
        assert "stream" not in client._client.post.await_args.kwargs["json"]


class TestStreamingToolCalls:
    async def test_tool_call_fragments_assembled(self):
        chunks = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "id": "call_1", "function": {"name": "get_weather"}}
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": '{"city":'}}]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": ' "Paris"}'}}]
                        }
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        client = _client_with_streams([_sse_response(chunks)])
        result = await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].id == "call_1"
        assert result.tool_calls[0].name == "get_weather"
        assert result.tool_calls[0].arguments == {"city": "Paris"}
        assert result.finish_reason == "tool_calls"

    async def test_parallel_tool_calls_assembled_by_index(self):
        chunks = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_a",
                                    "function": {"name": "f", "arguments": '{"x":1}'},
                                },
                                {
                                    "index": 1,
                                    "id": "call_b",
                                    "function": {"name": "g", "arguments": '{"y":2}'},
                                },
                            ]
                        }
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        client = _client_with_streams([_sse_response(chunks)])
        result = await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        assert [tc.id for tc in result.tool_calls] == ["call_a", "call_b"]
        assert result.tool_calls[1].arguments == {"y": 2}


class TestStreamingRetries:
    async def test_transient_status_retried_in_stream(self):
        good = _sse_response(
            [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]
        )
        client = _client_with_streams([_response(502), good])
        with patch("moira.inference.client.asyncio.sleep", new=AsyncMock()) as slept:
            result = await client.chat_completion(
                "test-model", [{"role": "user", "content": "hi"}]
            )
        assert result.content == "ok"
        assert client._client.stream.call_count == 2
        slept.assert_awaited_once_with(3.0)

    async def test_mid_stream_disconnect_retried(self):
        class _DyingStream:
            """Duck-typed SSE response that yields a line, then dies."""

            status_code = 200
            is_success = True
            headers = {"content-type": "text/event-stream"}

            async def aiter_lines(self):
                yield 'data: {"choices": [{"delta": {"content": "par"}}]}'
                raise httpx.RemoteProtocolError("peer closed connection")

        good = _sse_response(
            [{"choices": [{"delta": {"content": "full answer"}, "finish_reason": "stop"}]}]
        )
        client = _client_with_streams([_DyingStream(), good])
        with patch("moira.inference.client.asyncio.sleep", new=AsyncMock()) as slept:
            result = await client.chat_completion(
                "test-model", [{"role": "user", "content": "hi"}]
            )
        assert result.content == "full answer"
        assert client._client.stream.call_count == 2
        slept.assert_awaited_once_with(3.0)

    async def test_mid_stream_disconnect_raises_after_max_retries(self):
        class _DyingStream:
            status_code = 200
            is_success = True
            headers = {"content-type": "text/event-stream"}

            async def aiter_lines(self):
                raise httpx.RemoteProtocolError("peer closed connection")
                yield  # unreachable; makes this an async generator

        client = _client_with_streams([_DyingStream()] * 3)
        with patch("moira.inference.client.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(httpx.RemoteProtocolError):
                await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])
        assert client._client.stream.call_count == 3
