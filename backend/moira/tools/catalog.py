import logging
from typing import Any

from moira.tools.base import ToolDefinition

logger = logging.getLogger(__name__)


class ToolCatalog:
    """Registry of known tools. Loaded from config at startup and queried
    by the Tool Discovery node for semantic search. Catalog tools are
    embedded and discoverable; system tools are injected by the graph
    and bypass discovery entirely."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def load_from_config(self, tool_configs: list[Any]) -> None:
        """Load tool definitions from the config's tool list."""
        for tc in tool_configs:
            # tc is a ToolConfig pydantic model from moira.config
            defn = ToolDefinition(
                name=tc.name,
                description=tc.description,
                tool_type=tc.type,
                argument_schema=tc.argument_schema,
                endpoint=tc.endpoint,
                method=tc.method,
                tags=tc.tags,
                reliability=tc.reliability,
            )
            self._tools[defn.name] = defn
            logger.info("Loaded tool '%s' (type=%s)", defn.name, defn.tool_type)
        logger.info("Tool catalog loaded: %d tools", len(self._tools))

    def get_all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def get_by_names(self, names: list[str]) -> list[ToolDefinition]:
        return [self._tools[n] for n in names if n in self._tools]
