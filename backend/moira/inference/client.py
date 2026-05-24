import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ModelInfo:
    id: str
    owned_by: str = ""
    # Tracks which endpoint this model was discovered from, so the registry
    # can route requests to the correct client.
    source_endpoint: str = ""


class InferenceClient:
    # Two-phase initialization: __init__ stores config, start() creates the
    # async client. Required because httpx.AsyncClient must be created inside
    # an event loop. Callers must call start() before use and stop() to
    # release the connection pool.
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 120.0):
        self._base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = {}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

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
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        # Default temperature and max_tokens are reasonable for chat but should
        # be configurable per-purpose (intelligence vs. task) when the workflow
        # engine is implemented.
        assert self._client is not None, "Client not started"
        logger.info("Chat completion request: model=%s, messages=%d", model, len(messages))
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        # No response validation -- assumes a well-formed OpenAI-compatible
        # response with at least one choice. Will raise KeyError if the
        # provider returns tool calls instead of content.
        return data["choices"][0]["message"]["content"]
