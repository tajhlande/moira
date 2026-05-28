import logging
import time
from typing import Any

import httpx

from moira.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class RESTTool(BaseTool):
    """Executes a tool by calling a REST endpoint. Uses httpx for async
    HTTP with configurable timeout."""

    def __init__(self, definition, timeout: float = 30.0):
        super().__init__(definition)
        self._timeout = timeout

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                method = self.definition.method.upper()
                url = self.definition.endpoint
                if method == "GET":
                    resp = await client.request(method, url, params=args)
                else:
                    resp = await client.request(method, url, json=args)
                resp.raise_for_status()
                elapsed_ms = int((time.monotonic() - start) * 1000)
                output = resp.text[:10000]
                return ToolResult(
                    tool_name=self.name,
                    output=output,
                    success=True,
                    duration_ms=elapsed_ms,
                )
        except Exception as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.error("REST tool '%s' failed: %s", self.name, e)
            return ToolResult(
                tool_name=self.name,
                output="",
                success=False,
                duration_ms=elapsed_ms,
                error=str(e),
            )
