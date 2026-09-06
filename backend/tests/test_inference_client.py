"""Tests for InferenceClient transient-error retry behavior.

The client retries 502/503/504 gateway errors with a short linear backoff
(llama-swap flaps these when the upstream model server is momentarily
unavailable) and surfaces non-transient failures immediately.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from moira.inference.client import InferenceClient
from moira.inference.defaults import DEFAULT_INTELLIGENCE_EXTRA_BODY


def _success_payload() -> dict:
    return {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


def _response(status: int, payload: dict | None = None) -> httpx.Response:
    """Build a real httpx.Response so HTTPStatusError carries request/response."""
    request = httpx.Request("POST", "http://test/chat/completions")
    return httpx.Response(status, json=payload or {}, request=request)


def _client_with_responses(responses: list[httpx.Response]) -> InferenceClient:
    """Build a started client whose HTTP layer replays the given responses."""
    client = InferenceClient(base_url="http://test")
    http = MagicMock()
    http.post = AsyncMock(side_effect=responses)
    client._client = http
    return client


async def _send(client: InferenceClient):
    return await client.chat_completion("test-model", [{"role": "user", "content": "hi"}])


class TestTransientRetry:
    async def test_502_retried_then_success(self):
        client = _client_with_responses([_response(502), _response(200, _success_payload())])
        with patch("moira.inference.client.asyncio.sleep", new=AsyncMock()) as slept:
            resp = await _send(client)
        assert resp.content == "ok"
        assert client._client.post.await_count == 2
        # Linear backoff: first retry waits _TRANSIENT_BACKOFF_S
        slept.assert_awaited_once_with(3.0)

    async def test_two_502s_retried_then_success(self):
        client = _client_with_responses(
            [_response(502), _response(503), _response(200, _success_payload())]
        )
        with patch("moira.inference.client.asyncio.sleep", new=AsyncMock()) as slept:
            resp = await _send(client)
        assert resp.content == "ok"
        assert client._client.post.await_count == 3
        assert [c.args[0] for c in slept.await_args_list] == [3.0, 6.0]

    async def test_persistent_502_raises_after_max_retries(self):
        client = _client_with_responses([_response(502)] * 3)
        with patch("moira.inference.client.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await _send(client)
        assert exc_info.value.response.status_code == 502
        assert client._client.post.await_count == 3

    async def test_non_transient_error_not_retried(self):
        client = _client_with_responses([_response(400, {"error": {"message": "bad"}})])
        with patch("moira.inference.client.asyncio.sleep", new=AsyncMock()) as slept:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await _send(client)
        assert exc_info.value.response.status_code == 400
        assert client._client.post.await_count == 1
        slept.assert_not_awaited()


class TestSamplingDefaults:
    """The client is a policy-free transport: no sampling parameters beyond
    temperature/max_tokens are sent unless a caller passes extra_body
    (intelligence-model call sites pass DEFAULT_INTELLIGENCE_EXTRA_BODY)."""

    async def test_no_sampling_params_by_default(self):
        # Guards task-model and eval-judge calls: they must rely on server
        # defaults, so the payload carries only the core fields.
        client = _client_with_responses([_response(200, _success_payload())])
        await _send(client)
        sent = client._client.post.await_args.kwargs["json"]
        assert sent == {
            "model": "test-model",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 1.0,
            "max_tokens": 65536,
        }

    async def test_extra_body_sent_when_passed(self):
        client = _client_with_responses([_response(200, _success_payload())])
        await client.chat_completion(
            "test-model",
            [{"role": "user", "content": "hi"}],
            temperature=0.5,
            extra_body=dict(DEFAULT_INTELLIGENCE_EXTRA_BODY, top_k=40),
        )
        sent = client._client.post.await_args.kwargs["json"]
        assert sent["top_k"] == 40
        assert sent["top_p"] == DEFAULT_INTELLIGENCE_EXTRA_BODY["top_p"]
        assert sent["temperature"] == 0.5
