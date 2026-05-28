import asyncio
import logging
from typing import Any

from moira.tools.base import BaseTool, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

# Default execution limits. Configurable at the executor level so tests
# can override them without touching the graph.
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 1


class ToolExecutor:
    """Executes tools with timeout and basic retry for transient failures.
    Returns a ToolResult for every execution (success or failure) so the
    model can reason about errors."""

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self._timeout = timeout
        self._max_retries = max_retries
        self._tool_instances: dict[str, BaseTool] = {}

    def register_tool(self, tool: BaseTool) -> None:
        self._tool_instances[tool.name] = tool

    def register_tools(self, definitions: list[ToolDefinition]) -> None:
        """Register REST tools from definitions. MCP tools are added in
        Phase 3."""
        from moira.tools.rest_tool import RESTTool

        for defn in definitions:
            if defn.tool_type == "rest":
                self._tool_instances[defn.name] = RESTTool(defn, timeout=self._timeout)
                logger.debug("Registered REST tool '%s'", defn.name)

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> ToolResult:
        tool = self._tool_instances.get(tool_name)
        if tool is None:
            logger.error("Tool '%s' not found in executor", tool_name)
            return ToolResult(
                tool_name=tool_name,
                output="",
                success=False,
                error=f"Tool '{tool_name}' not found",
            )

        last_result = None
        for attempt in range(self._max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    tool.execute(args),
                    timeout=self._timeout,
                )
                if result.success:
                    return result
                last_result = result
                if attempt < self._max_retries:
                    logger.warning(
                        "Tool '%s' failed (attempt %d/%d), retrying",
                        tool_name,
                        attempt + 1,
                        self._max_retries + 1,
                    )
                    await asyncio.sleep(0.5 * (attempt + 1))
            except asyncio.TimeoutError:
                elapsed = self._timeout
                logger.error("Tool '%s' timed out after %.1fs", tool_name, elapsed)
                return ToolResult(
                    tool_name=tool_name,
                    output="",
                    success=False,
                    duration_ms=int(elapsed * 1000),
                    error=f"Timeout after {elapsed}s",
                )
            except Exception as e:
                logger.error("Tool '%s' unexpected error: %s", tool_name, e)
                return ToolResult(
                    tool_name=tool_name,
                    output="",
                    success=False,
                    error=str(e),
                )

        assert last_result is not None
        return last_result

    async def execute_batch(
        self,
        calls: list[tuple[str, dict[str, Any]]],
    ) -> list[ToolResult]:
        """Execute multiple tool calls concurrently."""
        tasks = [self.execute(name, args) for name, args in calls]
        return list(await asyncio.gather(*tasks))
