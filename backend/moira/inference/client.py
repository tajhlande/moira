import asyncio
import json
import logging
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any, NoReturn

import httpx

from moira.inference.adapters import ToolCallingAdapter, get_adapter
from moira.inference.defaults import DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE
from moira.tools.base import ToolCall, ToolDefinition

logger = logging.getLogger(__name__)

try:
    __version__ = version("moira-backend")  # matches what is in pyproject.toml
except PackageNotFoundError:  # running from a checkout without install
    __version__ = "0.1.0+source"

_DEFAULT_USER_AGENT = f"MOiRA/{__version__}"

# llama-swap occasionally flaps transient gateway errors (502/503/504) mid-batch
# — the upstream model server is momentarily unavailable. These are worth a
# short retry: a failed request otherwise kills the whole workflow run, while
# resuming the run and re-executing the step (what the user does manually)
# succeeds. Two retries with a short linear backoff covers observed flaps
# without materially delaying a genuinely down backend.
# 524 is Cloudflare's "origin timed out" edge status: the provider's gateway
# gave up waiting on the model server (e.g. a long thinking-model judge call).
# The origin may still complete, and a retry often succeeds on a faster pass,
# so it gets the same transient treatment as 504.
_TRANSIENT_STATUS_CODES = frozenset({502, 503, 504, 524})
_TRANSIENT_MAX_RETRIES = 2
_TRANSIENT_BACKOFF_S = 3.0

# Streaming: with a non-streaming POST the entire generation must complete
# within a single read-timeout window, which slow models routinely exceed.
# With server-sent events, httpx's read timeout applies between chunks, so
# an actively-generating model never trips it. Streaming is therefore the
# preferred transport where available.
#
# Discovery: servers in the OpenAI completions dialect don't advertise
# streaming support — there is no capability endpoint to ask — so the first
# request attempts ``stream: true`` and falls back
# to a non-streaming POST when the server rejects the request outright or
# answers with a plain JSON body (i.e. it ignored the flag). The outcome is
# cached for the client's lifetime. ``stream=True`` forces streaming (errors
# surface rather than fall back), ``stream=False`` disables it, ``None``
# (default) auto-discovers.
_DEFAULT_STREAMING: bool | None = None
# Sent with streaming requests so servers that support it include token
# usage on the final chunk (llama.cpp volunteers timings regardless). Strict
# servers that reject unknown fields are classified as non-streaming by the
# discovery fallback, which degrades gracefully to today's behavior.
_STREAM_INCLUDE_USAGE = True


@dataclass
class ModelInfo:
    id: str
    owned_by: str = ""
    # Tracks which endpoint this model was discovered from, so the registry
    # can route requests to the correct client.
    source_endpoint: str = ""


@dataclass
class ChatResponse:
    """Structured response from a chat completion call. Captures both the
    model's content and its thinking/reasoning content when available
    (e.g. Qwen models that return reasoning_content).

    When the model uses native tool calling, ``tool_calls`` contains the
    parsed calls and ``content`` may be empty.
    """

    content: str
    thinking: str = ""
    model: str = ""
    finish_reason: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    prompt_time_ms: float | None = None
    gen_time_ms: float | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class InferenceClient:
    # Two-phase initialization: __init__ stores config, start() creates the
    # async client. Required because httpx.AsyncClient must be created inside
    # an event loop. Callers must call start() before use and stop() to
    # release the connection pool.
    #
    # ``provider_type`` selects the tool-calling adapter used when
    # ``tools`` is passed to ``chat_completion``.
    #
    # ``stream`` controls streaming responses: ``None`` (default)
    # auto-discovers per server, ``True`` forces streaming, ``False``
    # disables it.
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 600.0,
        provider_type: str = "completions",
        stream: bool | None = _DEFAULT_STREAMING,
    ):
        self._base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = {}
        self._headers["User-Agent"] = _DEFAULT_USER_AGENT
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._adapter: ToolCallingAdapter | None = get_adapter(provider_type)
        # User preference from the constructor (None = auto-discover).
        self._stream_pref = stream
        # Discovered capability, cached after the first completion call:
        # None = not yet known, True/False = observed server behavior.
        self._stream_supported: bool | None = None

    async def start(self) -> None:
        logger.info("Starting inference client for %s", self._base_url)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=httpx.Timeout(self._timeout),
        )

    async def stop(self) -> None:
        if self._client:
            logger.info("Stopping inference client for %s", self._base_url)
            await self._client.aclose()
            self._client = None

    async def list_models(self) -> list[ModelInfo]:
        assert self._client is not None, "Client not started"
        logger.debug("Listing models from %s", self._base_url)
        resp = await self._client.get("/models")
        resp.raise_for_status()
        data = resp.json()
        return [
            ModelInfo(id=m["id"], owned_by=m.get("owned_by", "")) for m in data.get("data", [])
        ]

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        extra_body: dict | None = None,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str = "auto",
    ) -> ChatResponse:
        """Send a chat completion request.

        When ``tools`` is provided and the client has a tool-calling adapter,
        the tool definitions are formatted and sent with the request. The
        response is parsed for tool calls via the adapter.

        Streaming is used when enabled/discovered: SSE chunks are
        accumulated internally and the caller receives the same single
        ``ChatResponse`` as with a non-streaming request. This keeps the
        read timeout scoped to inter-chunk gaps rather than the whole
        generation, which is what slow models need.

        Transient gateway errors (502/503/504) are retried up to
        ``_TRANSIENT_MAX_RETRIES`` times with a short linear backoff before
        surfacing the HTTPStatusError to the caller.
        """
        assert self._client is not None, "Client not started"
        logger.info("Chat completion request: model=%s, messages=%d", model, len(messages))
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "reasoning_budget_tokens": max_tokens / 2 if max_tokens > 0 else 8192,
            "reasoning_budget_message": "Finish your reasoning and give your final answer now.",
        }
        if extra_body:
            payload.update(extra_body)

        if tools is not None:
            if self._adapter is None:
                raise ValueError(
                    "Tools requested but no tool-calling adapter is configured for this provider"
                )
            payload["tools"] = self._adapter.format_tools(tools)
            payload["tool_choice"] = tool_choice

        if self._should_attempt_stream():
            streamed = await self._streaming_chat_completion(model, payload)
            if streamed is not None:
                return streamed

        resp = await self._post_with_transient_retries(model, payload)
        if not resp.is_success:
            await self._raise_status_error(resp, model)
        return self._parse_completion(resp.json(), model, resp)

    def _should_attempt_stream(self) -> bool:
        """Whether the next chat completion should be attempted via SSE.

        Honors the constructor ``stream`` preference; in auto mode
        (``None``), streaming is attempted until the server is known not
        to support it.
        """
        if self._stream_pref is not None:
            return self._stream_pref
        return self._stream_supported is not False

    async def _post_with_transient_retries(
        self, model: str, payload: dict[str, Any]
    ) -> httpx.Response:
        """POST the payload, retrying transient gateway errors (502/503/504,
        Cloudflare 524) with a short linear backoff.

        Returns the final response regardless of status; the caller is
        responsible for raising on failure."""
        assert self._client is not None, "Client not started"
        for attempt in range(_TRANSIENT_MAX_RETRIES + 1):
            resp = await self._client.post("/chat/completions", json=payload)
            if resp.is_success or resp.status_code not in _TRANSIENT_STATUS_CODES:
                return resp
            if attempt < _TRANSIENT_MAX_RETRIES:
                delay = _TRANSIENT_BACKOFF_S * (attempt + 1)
                logger.warning(
                    "Transient upstream error (HTTP %d) on chat completion for model=%s, "
                    "retrying in %.0fs (attempt %d/%d)",
                    resp.status_code,
                    model,
                    delay,
                    attempt + 1,
                    _TRANSIENT_MAX_RETRIES,
                )
                await asyncio.sleep(delay)
        return resp

    async def _raise_status_error(self, resp: httpx.Response, model: str) -> NoReturn:
        """Raise the HTTPStatusError for a failed completion response, with
        the server's error message extracted where possible.

        Reads the response body first, so it works for both buffered
        responses and error statuses on an opened stream."""
        await resp.aread()
        body = resp.text[:2000]
        logger.error(
            "Chat completion failed: model=%s, status=%d, response body: %s",
            model,
            resp.status_code,
            body,
        )
        try:
            error_data = resp.json()
            server_message = (
                error_data.get("error", {}).get("message", "")
                if isinstance(error_data.get("error"), dict)
                else str(error_data.get("error", ""))
            )
            if not server_message:
                server_message = body[:500]
        except Exception:
            server_message = body[:500]
        raise httpx.HTTPStatusError(
            message=f"{resp.status_code} {resp.reason_phrase}: {server_message}",
            request=resp.request,
            response=resp,
        )

    def _parse_completion(
        self, data: dict[str, Any], fallback_model: str, response: httpx.Response
    ) -> ChatResponse:
        """Convert a non-streaming completion body — or the equivalent
        structure accumulated from SSE chunks — into a ChatResponse."""
        if "choices" not in data or not data["choices"]:
            raise httpx.HTTPStatusError(
                message=(f"Response missing 'choices' key. Body: {json.dumps(data)[:500]}"),
                request=response.request,
                response=response,
            )
        choice = data["choices"][0]
        message = choice["message"]
        thinking = message.get("reasoning_content", "")
        if thinking:
            logger.debug("Model thinking (reasoning_content): %s", thinking[:2000])

        # Parse tool calls from the response using the adapter
        tool_calls: list[ToolCall] = []
        if self._adapter is not None and message.get("tool_calls"):
            tool_calls = self._adapter.parse_tool_calls(message)
            logger.info("Parsed %d tool calls from response", len(tool_calls))

        usage = data.get("usage") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        timings = data.get("timings") or {}
        return ChatResponse(
            content=message.get("content") or "",
            thinking=thinking,
            model=data.get("model", fallback_model),
            finish_reason=choice.get("finish_reason", ""),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            thinking_tokens=completion_details.get("reasoning_tokens"),
            prompt_time_ms=timings.get("prompt_ms"),
            gen_time_ms=timings.get("predicted_ms"),
            tool_calls=tool_calls,
        )

    async def _streaming_chat_completion(
        self, model: str, payload: dict[str, Any]
    ) -> ChatResponse | None:
        """Attempt a streaming chat completion, accumulating the SSE chunks
        into a single ChatResponse.

        Returns None when the server turned out not to support streaming —
        it rejected the request before producing any events, or answered
        with a plain JSON body — so the caller can fall back to a
        non-streaming POST. Discovery results are cached on the client.

        Mid-stream transport failures (disconnects) are treated like
        transient gateway errors: the whole request is retried."""
        assert self._client is not None, "Client not started"
        # Stream keys first so an explicit extra_body can still override
        # them (e.g. to force a one-off non-streaming request).
        stream_payload = {
            "stream": True,
            "stream_options": {"include_usage": _STREAM_INCLUDE_USAGE},
            **payload,
        }
        # Auto-discovery is only armed while the capability is unknown.
        discovering = self._stream_pref is None and self._stream_supported is None

        for attempt in range(_TRANSIENT_MAX_RETRIES + 1):
            try:
                async with self._client.stream(
                    "POST", "/chat/completions", json=stream_payload
                ) as response:
                    if response.status_code in _TRANSIENT_STATUS_CODES:
                        if attempt < _TRANSIENT_MAX_RETRIES:
                            delay = _TRANSIENT_BACKOFF_S * (attempt + 1)
                            logger.warning(
                                "Transient upstream error (HTTP %d) on streamed chat "
                                "completion for model=%s, retrying in %.0fs (attempt %d/%d)",
                                response.status_code,
                                model,
                                delay,
                                attempt + 1,
                                _TRANSIENT_MAX_RETRIES,
                            )
                            await asyncio.sleep(delay)
                            continue
                        await self._raise_status_error(response, model)

                    if not response.is_success:
                        if discovering:
                            logger.info(
                                "Streaming rejected by %s (HTTP %d) — falling back to "
                                "non-streaming requests",
                                self._base_url,
                                response.status_code,
                            )
                            if self._stream_pref is None:
                                self._stream_supported = False
                            return None
                        await self._raise_status_error(response, model)

                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" not in content_type.lower():
                        # Server ignored the stream flag and returned a
                        # complete JSON body. Parse it directly and stop
                        # asking for streams.
                        logger.info(
                            "%s answered stream=true with %s — using non-streaming requests",
                            self._base_url,
                            content_type or "a non-SSE body",
                        )
                        if self._stream_pref is None:
                            self._stream_supported = False
                        await response.aread()
                        return self._parse_completion(response.json(), model, response)

                    if self._stream_supported is not True:
                        self._stream_supported = True
                        logger.info(
                            "Streaming supported by %s — using SSE responses", self._base_url
                        )
                    return await self._consume_sse(response, model)
            except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout) as exc:
                # Mid-stream transport failure. We were mid-conversation
                # with a working SSE stream, so treat it as transient and
                # retry the whole request.
                if attempt >= _TRANSIENT_MAX_RETRIES:
                    raise
                delay = _TRANSIENT_BACKOFF_S * (attempt + 1)
                logger.warning(
                    "Stream interrupted for model=%s (%s), retrying in %.0fs (attempt %d/%d)",
                    model,
                    exc,
                    delay,
                    attempt + 1,
                    _TRANSIENT_MAX_RETRIES,
                )
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable: streaming retry loop always returns or raises")

    async def _consume_sse(self, response: httpx.Response, model: str) -> ChatResponse:
        """Accumulate a chat completion SSE stream into a ChatResponse.

        Delta fragments (content, reasoning, tool-call arguments split
        across chunks) are assembled the same way the OpenAI client
        libraries do it. Usage/timings are captured best-effort from the
        final chunks where servers include them."""
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        finish_reason = ""
        model_name = ""
        usage: dict[str, Any] = {}
        timings: dict[str, Any] = {}
        # index -> {"id": str, "name_parts": [...], "arg_parts": [...]}
        tool_call_frags: dict[int, dict[str, Any]] = {}
        saw_choices = False

        async for line in response.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                # Blank separators, SSE comments (":..."), and any event
                # framing we don't consume.
                continue
            data_str = line[len("data:") :].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                logger.debug("Skipping unparseable SSE line: %.200s", data_str)
                continue
            if not isinstance(chunk, dict):
                continue
            err = chunk.get("error")
            if err:
                # llama.cpp reports mid-stream failures as error events.
                raise RuntimeError(f"Streaming error from {self._base_url}: {err}")
            model_name = chunk.get("model") or model_name
            chunk_usage = chunk.get("usage")
            if isinstance(chunk_usage, dict):
                usage.update(chunk_usage)
            chunk_timings = chunk.get("timings")
            if isinstance(chunk_timings, dict):
                timings.update(chunk_timings)
            for choice in chunk.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                saw_choices = True
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    content_parts.append(delta["content"])
                if delta.get("reasoning_content"):
                    thinking_parts.append(delta["reasoning_content"])
                for tc in delta.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    # Tool calls arrive as fragments: the first carries id and
                    # function name, later ones append argument bytes.
                    index = tc.get("index", 0)
                    frag = tool_call_frags.setdefault(
                        index, {"id": "", "name_parts": [], "arg_parts": []}
                    )
                    if tc.get("id"):
                        frag["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        frag["name_parts"].append(fn["name"])
                    if fn.get("arguments"):
                        frag["arg_parts"].append(fn["arguments"])

        if not saw_choices:
            raise RuntimeError(f"Streaming response for model={model} contained no choices")

        message: dict[str, Any] = {
            "content": "".join(content_parts),
            "reasoning_content": "".join(thinking_parts),
        }
        if tool_call_frags:
            message["tool_calls"] = [
                {
                    "id": frag["id"] or f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": "".join(frag["name_parts"]),
                        "arguments": "".join(frag["arg_parts"]),
                    },
                }
                for index, frag in sorted(tool_call_frags.items())
            ]

        # Same shape the non-streaming path parses, so downstream handling
        # (thinking extraction, adapter tool-call parsing, usage) is shared.
        return self._parse_completion(
            {
                "choices": [{"message": message, "finish_reason": finish_reason}],
                "model": model_name or model,
                "usage": usage,
                "timings": timings,
            },
            model,
            response,
        )
