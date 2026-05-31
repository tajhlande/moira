import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """Canonical representation of a tool. Persisted in the database and
    loaded at startup. Each tool has an implementation class (fully
    qualified Python class name) that handles invocation."""

    name: str
    description: str
    argument_schema: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    reliability: str = "unknown"
    is_default: bool = False
    enabled: bool = True
    built_in: bool = False
    implementation: str = ""
    group_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "argument_schema": self.argument_schema,
            "config": self.config,
            "tags": self.tags,
            "reliability": self.reliability,
            "is_default": self.is_default,
            "enabled": self.enabled,
            "built_in": self.built_in,
            "implementation": self.implementation,
            "group_name": self.group_name,
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
    """Standardized tool invocation interface. Each tool implementation
    subclasses this and implements execute(). The implementation class is
    the authoritative source for the tool's specification: name, description,
    argument schema, config schema, and secret schema.

    The executor discovers the implementation class via the
    ToolDefinition.implementation field (fully qualified Python class name)
    and instantiates it with the tool's definition and config."""

    # Subclasses override these class attributes to declare their spec.
    tool_name: str = ""
    tool_description: str = ""
    tool_argument_schema: dict[str, Any] = {}
    tool_group: str = ""

    def __init__(self, definition: ToolDefinition):
        self.definition = definition

    @property
    def name(self) -> str:
        return self.definition.name

    @classmethod
    def make_definition(cls, **overrides: Any) -> ToolDefinition:
        """Build a ToolDefinition from the class-level spec. Useful for
        seeding built-in tools at startup and for tests."""
        defaults = {
            "name": cls.tool_name,
            "description": cls.tool_description,
            "argument_schema": cls.tool_argument_schema,
            "implementation": f"{cls.__module__}.{cls.__qualname__}",
            "group_name": cls.tool_group,
            "built_in": True,
            "is_default": True,
            "enabled": True,
        }
        defaults.update(overrides)
        return ToolDefinition(**defaults)

    @abstractmethod
    async def execute(self, args: dict[str, Any]) -> ToolResult: ...
