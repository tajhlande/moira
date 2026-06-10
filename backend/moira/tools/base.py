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
    original_description: str = ""

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
            "original_description": self.original_description,
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
    # JSON Schema for config values stored in ToolDefinition.config, and
    # for secret values stored in the credential store. The frontend uses
    # these to render configuration and credential forms on the tool detail
    # page. Tools with no config or secret requirements leave these empty.
    tool_config_schema: dict[str, Any] = {}
    tool_secret_schema: dict[str, Any] = {}

    def __init__(self, definition: ToolDefinition):
        self.definition = definition

    @property
    def name(self) -> str:
        return self.definition.name

    @classmethod
    def get_spec(cls) -> dict[str, Any]:
        """Return the tool's config and secret schemas. The frontend uses
        this to render configuration forms on the tool detail page."""
        return {
            "config_schema": cls.tool_config_schema,
            "secret_schema": cls.tool_secret_schema,
        }

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

    def report_call_type(self, args: dict[str, Any], result: "ToolResult") -> str | None:
        """Return a call_type string for metrics bucketing, or None to use
        the default ("default"). Subclasses override this to classify
        calls by pricing tier or usage category (e.g., "search",
        "summarize", "search+extract"). Cost estimation is the
        tool's responsibility, not the metrics layer's."""
        return None

    @abstractmethod
    async def execute(self, args: dict[str, Any]) -> ToolResult: ...
