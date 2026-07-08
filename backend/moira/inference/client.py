import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from moira.inference.adapters import ToolCallingAdapter, get_adapter
from moira.inference.defaults import DEFAULT_TEMPERATURE
from moira.tools.base import ToolCall, ToolDefinition

logger = logging.getLogger(__name__)


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
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 600.0,
        provider_type: str = "completions",
    ):
        self._base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = {}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._adapter: ToolCallingAdapter | None = get_adapter(provider_type)

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
        max_tokens: int = 65536,
        extra_body: dict | None = None,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str = "auto",
    ) -> ChatResponse:
        """Send a chat completion request.

        When ``tools`` is provided and the client has a tool-calling adapter,
        the tool definitions are formatted and sent with the request. The
        response is parsed for tool calls via the adapter.
        """
        assert self._client is not None, "Client not started"
        logger.info("Chat completion request: model=%s, messages=%d", model, len(messages))
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
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

        resp = await self._client.post("/chat/completions", json=payload)
        if not resp.is_success:
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
        data = resp.json()
        if "choices" not in data or not data["choices"]:
            raise httpx.HTTPStatusError(
                message=(f"Response missing 'choices' key. Body: {json.dumps(data)[:500]}"),
                request=resp.request,
                response=resp,
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
            model=data.get("model", model),
            finish_reason=choice.get("finish_reason", ""),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            thinking_tokens=completion_details.get("reasoning_tokens"),
            prompt_time_ms=timings.get("prompt_ms"),
            gen_time_ms=timings.get("predicted_ms"),
            tool_calls=tool_calls,
        )
