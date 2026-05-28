import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """Canonical representation of a tool. Created from config or MCP
    discovery and stored in the tool catalog."""

    name: str
    description: str
    tool_type: str
    argument_schema: dict[str, Any] = field(default_factory=dict)
    endpoint: str = ""
    method: str = "GET"
    tags: list[str] = field(default_factory=list)
    reliability: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "type": self.tool_type,
            "endpoint": self.endpoint,
            "method": self.method,
            "argument_schema": self.argument_schema,
            "tags": self.tags,
            "reliability": self.reliability,
        }


@dataclass
class ToolResult:
    """Structured result from tool execution. Always produced, even on
    failure, so the model can reason about what went wrong."""

    tool_name: str
    output: str
    success: bool = True
    duration_ms: int = 0
    error: str = ""


class BaseTool(ABC):
    """Abstract base for executable tools. Subclasses handle the transport
    (REST, MCP, etc.) while the executor handles timeout and retry."""

    def __init__(self, definition: ToolDefinition):
        self.definition = definition

    @property
    def name(self) -> str:
        return self.definition.name

    @abstractmethod
    async def execute(self, args: dict[str, Any]) -> ToolResult: ...
