"""Tool-calling adapters for provider API translation.

Each adapter converts between the canonical internal representation
(``ToolDefinition``, ``ToolCall``) and a specific provider's wire format.
The adapter is selected by ``provider_type`` from the resolved model's
endpoint configuration.
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from moira.tools.base import ToolCall, ToolDefinition

logger = logging.getLogger(__name__)


class ToolCallingAdapter(ABC):
    """Translates tool-related data between canonical and wire formats.

    The inference client delegates request formatting and response parsing
    to the adapter so that the client itself stays provider-agnostic for
    tool calling concerns.
    """

    @abstractmethod
    def format_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """Convert ToolDefinitions to the provider's tool definition wire format."""

    @abstractmethod
    def parse_tool_calls(self, message: dict[str, Any]) -> list[ToolCall]:
        """Extract and normalize tool calls from the provider's response message.

        The message dict is the raw ``choices[0].message`` (or equivalent)
        from the HTTP response.
        """

    @abstractmethod
    def format_tool_result(self, tool_call_id: str, content: str) -> dict[str, Any]:
        """Build the message that feeds a tool execution result back to the model."""

    @abstractmethod
    def format_assistant_message(self, content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        """Build the assistant message for the conversation history when it
        contained tool calls."""


class OpenAICompletionsAdapter(ToolCallingAdapter):
    """Adapter for the OpenAI Chat Completions API dialect.

    Used by OpenAI-compatible servers (vLLM, llama.cpp, OpenAI itself).

    Wire format reference:
    - Tool definitions: ``{"type": "function", "function": {name, description, parameters}}``
    - Tool calls in response: ``message.tool_calls[]`` with ``function.arguments``
      as a JSON **string** that must be parsed
    - Tool results: ``{"role": "tool", "tool_call_id": ..., "content": ...}``
    """

    def format_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.argument_schema,
                },
            }
            for t in tools
        ]

    def parse_tool_calls(self, message: dict[str, Any]) -> list[ToolCall]:
        raw_calls = message.get("tool_calls") or []
        result: list[ToolCall] = []
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            call_id = call.get("id", "")
            func = call.get("function", {})
            if not isinstance(func, dict):
                continue
            name = func.get("name", "")
            if not call_id or not name:
                logger.warning("Skipping malformed tool call (id=%r, name=%r)", call_id, name)
                continue
            args_raw = func.get("arguments", "{}")
            try:
                arguments = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "Failed to parse tool call arguments for '%s': %s",
                    name,
                    str(args_raw)[:200],
                )
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            result.append(ToolCall(id=call_id, name=name, arguments=arguments))
        return result

    def format_tool_result(self, tool_call_id: str, content: str) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }

    def format_assistant_message(self, content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in tool_calls
            ],
        }


# --- Factory ---

_ADAPTERS: dict[str, type[ToolCallingAdapter]] = {
    "completions": OpenAICompletionsAdapter,
}

# Sentinel for providers that don't support tool calling.
_NO_ADAPTER: ToolCallingAdapter | None = None


def get_adapter(provider_type: str) -> ToolCallingAdapter | None:
    """Return the adapter for the given provider_type, or None if the
    provider type doesn't support native tool calling."""
    cls = _ADAPTERS.get(provider_type)
    if cls is None:
        logger.warning(
            "No tool-calling adapter for provider_type '%s' — native tool calling disabled",
            provider_type,
        )
        return None
    return cls()
