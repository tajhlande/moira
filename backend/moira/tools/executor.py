import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, cast

from moira.persistence.write_queue import AsyncWriteQueue
from moira.tools.base import BaseTool, ToolCall, ToolDefinition, ToolResult
from moira.tools.metrics import ToolMetricsRepository

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
        metrics_repo: ToolMetricsRepository | None = None,
    ):
        self._timeout = timeout
        self._max_retries = max_retries
        self._metrics_repo = metrics_repo
        self._tool_instances: dict[str, BaseTool] = {}

    def register_tool(self, tool: BaseTool) -> None:
        self._tool_instances[tool.name] = tool

    def register_tools(self, definitions: list[ToolDefinition]) -> None:
        """Instantiate and register tools from their definitions. Uses the
        implementation field (fully qualified class name) to find the class,
        then passes the definition to its constructor. Tools with an empty
        implementation are skipped — they appear in the catalog but cannot
        be executed."""
        for defn in definitions:
            if not defn.implementation:
                continue
            tool_cls = self._resolve_implementation(defn.implementation)
            if tool_cls is not None:
                self._tool_instances[defn.name] = tool_cls(defn)
                logger.debug("Registered tool '%s' (%s)", defn.name, defn.implementation)

    @staticmethod
    def _resolve_implementation(fqcn: str) -> type | None:
        """Resolve a fully qualified class name to a class object.
        Returns None if the module or class cannot be found."""
        try:
            module_path, class_name = fqcn.rsplit(".", 1)
            import importlib

            module = importlib.import_module(module_path)
            return getattr(module, class_name)
        except (ValueError, ImportError, AttributeError) as e:
            logger.warning("Cannot resolve tool implementation '%s': %s", fqcn, e)
            return None

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        allowed_tools: set[str] | None = None,
    ) -> ToolResult:
        if allowed_tools is not None and tool_name not in allowed_tools:
            logger.warning("Tool '%s' blocked — not in allowed set", tool_name)
            result = ToolResult(
                tool_name=tool_name,
                output="",
                success=False,
                error=f"Tool '{tool_name}' is not available for this run",
            )
            await self._record_metrics(tool_name, args, result)
            return result

        tool = self._tool_instances.get(tool_name)
        if tool is None:
            logger.error("Tool '%s' not found in executor", tool_name)
            result = ToolResult(
                tool_name=tool_name,
                output="",
                success=False,
                error=f"Tool '{tool_name}' not found",
            )
            await self._record_metrics(tool_name, args, result)
            return result

        last_result = None
        for attempt in range(self._max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    tool.execute(args),
                    timeout=self._timeout,
                )
                if result.success:
                    await self._record_metrics(tool_name, args, result)
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
                result = ToolResult(
                    tool_name=tool_name,
                    output="",
                    success=False,
                    duration_ms=int(elapsed * 1000),
                    error=f"Timeout after {elapsed}s",
                )
                await self._record_metrics(tool_name, args, result)
                return result
            except Exception as e:
                logger.error("Tool '%s' unexpected error: %s", tool_name, e)
                result = ToolResult(
                    tool_name=tool_name,
                    output="",
                    success=False,
                    error=str(e),
                )
                await self._record_metrics(tool_name, args, result)
                return result

        assert last_result is not None
        await self._record_metrics(tool_name, args, last_result)
        return last_result

    async def _record_metrics(self, tool_name: str, args: dict, result: ToolResult) -> None:
        """Record metrics for a tool call. Silently skipped when no
        metrics_repo is configured. Exceptions from report_call_type() are
        swallowed so metrics never break execution."""
        if self._metrics_repo is None:
            return
        call_type = "default"
        tool = self._tool_instances.get(tool_name)
        if tool is not None:
            try:
                reported = tool.report_call_type(args, result)
                if reported is not None:
                    call_type = reported
            except Exception:
                pass
        period_hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
        try:
            from moira.service_setup import service_provider

            write_queue = cast(AsyncWriteQueue, service_provider("write_queue"))
            write_queue.enqueue(
                lambda: self._metrics_repo.record_call(  # type: ignore
                    tool_name=tool_name,
                    call_type=call_type,
                    period_hour=period_hour,
                    success=result.success,
                    duration_ms=result.duration_ms,
                )
            )
        except Exception:
            try:
                await self._metrics_repo.record_call(
                    tool_name=tool_name,
                    call_type=call_type,
                    period_hour=period_hour,
                    success=result.success,
                    duration_ms=result.duration_ms,
                )
            except Exception:
                logger.debug("Failed to record metrics for %s", tool_name, exc_info=True)

    async def execute_batch(
        self,
        calls: list[ToolCall],
        allowed_tools: set[str] | None = None,
    ) -> list[ToolResult]:
        """Execute multiple tool calls concurrently.
        If allowed_tools is provided, calls for tools outside the set are
        rejected immediately without execution."""
        tasks = [
            self.execute(call.name, call.arguments, allowed_tools=allowed_tools) for call in calls
        ]
        return list(await asyncio.gather(*tasks))
