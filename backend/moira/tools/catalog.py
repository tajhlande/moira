import logging
from typing import Any

from moira.tools.base import ToolDefinition

logger = logging.getLogger(__name__)


class ToolCatalog:
    """Registry of known tools. Loaded from the database at startup and
    queried by the Tool Discovery node for semantic search."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def load_from_config(self, tool_configs: list[Any]) -> None:
        """Load tool definitions from the config's tool list (YAML tools)."""
        for tc in tool_configs:
            defn = ToolDefinition(
                name=tc.name,
                description=tc.description,
                argument_schema=tc.argument_schema,
                config={
                    "endpoint": tc.endpoint,
                    "method": tc.method,
                },
                tags=tc.tags,
                reliability=tc.reliability,
            )
            self._tools[defn.name] = defn
            logger.info("Loaded tool '%s' from config", defn.name)
        logger.info("Tool catalog loaded from config: %d tools", len(self._tools))

    def load_from_db(self, db_tools: list[ToolDefinition]) -> None:
        """Load tool definitions from the database. DB tools take precedence
        over config tools with the same name (user may have edited them)."""
        for defn in db_tools:
            self._tools[defn.name] = defn
        logger.info("Tool catalog loaded from DB: %d tools", len(db_tools))

    def add(self, defn: ToolDefinition) -> None:
        self._tools[defn.name] = defn

    def get_all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def get_by_names(self, names: list[str]) -> list[ToolDefinition]:
        return [self._tools[n] for n in names if n in self._tools]

    def remove(self, name: str) -> None:
        """Remove a tool from the catalog by name."""
        self._tools.pop(name, None)

    def get_default_tools(self) -> list[ToolDefinition]:
        """Return tools marked as is_default and enabled. These are always
        available to the research workflow regardless of semantic discovery."""
        return [t for t in self._tools.values() if t.is_default and t.enabled]
