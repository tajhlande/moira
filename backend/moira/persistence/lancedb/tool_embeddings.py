import logging
from pathlib import Path

import lancedb
import numpy as np

from moira.tools.base import ToolDefinition

logger = logging.getLogger(__name__)


class ToolEmbeddingRepository:
    """LanceDB-backed storage for tool description embeddings. Supports
    upsert (for startup ingestion) and similarity search (for Tool
    Discovery). This is the vector store counterpart to the SQLite tool
    metadata."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._db: lancedb.DBConnection | None = None
        self._table_name = "tool_embeddings"

    async def start(self) -> None:
        Path(self._db_path).mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(self._db_path)
        logger.info("LanceDB opened at %s", self._db_path)

    async def upsert(
        self,
        names: list[str],
        embeddings: list[list[float]],
        descriptions: list[str],
    ) -> None:
        """Insert or update tool embeddings. Uses overwrite semantics per
        tool name (delete-then-insert)."""
        assert self._db is not None
        if not names:
            return

        data = []
        for name, emb, desc in zip(names, embeddings, descriptions):
            data.append(
                {
                    "name": name,
                    "vector": emb,
                    "description": desc,
                }
            )

        try:
            table = self._db.open_table(self._table_name)
            existing = table.to_pandas()
            existing_names = set(existing["name"].tolist())
            new_data = [d for d in data if d["name"] not in existing_names]
            update_names = [d["name"] for d in data if d["name"] in existing_names]

            if update_names:
                # Delete old entries for updated tools, then add them back
                for name in update_names:
                    table.delete(f'name = "{name}"')
                update_data = [d for d in data if d["name"] in update_names]
                table.add(update_data)
                logger.debug("Updated %d existing tool embeddings", len(update_names))

            if new_data:
                table.add(new_data)
                logger.debug("Added %d new tool embeddings", len(new_data))
        except FileNotFoundError:
            # First time: create the table
            self._db.create_table(self._table_name, data)
            logger.info("Created tool_embeddings table with %d entries", len(data))

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[ToolDefinition]:
        """Search for tools by embedding similarity. Returns ToolDefinition
        objects with name, description, and placeholder fields."""
        assert self._db is not None
        try:
            table = self._db.open_table(self._table_name)
        except (FileNotFoundError, ValueError, Exception) as e:
            logger.info("tool_embeddings table not available (%s), returning empty", e)
            return []

        query_array = np.array(query_embedding, dtype=np.float32)
        results = table.search(query_array).limit(top_k).to_pandas()

        definitions = []
        for _, row in results.iterrows():
            definitions.append(
                ToolDefinition(
                    name=row["name"],
                    description=row["description"],
                    tool_type="rest",
                )
            )
        return definitions
