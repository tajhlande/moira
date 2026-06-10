import asyncio
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
    metadata.

    All LanceDB I/O is synchronous, so every public method offloads
    work to a thread executor to keep the asyncio event loop
    responsive during tool discovery streaming.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._db: lancedb.DBConnection | None = None
        self._table_name = "tool_embeddings"

    async def start(self) -> None:
        def _open():
            Path(self._db_path).mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(self._db_path)

        await asyncio.to_thread(_open)
        logger.info("LanceDB opened at %s", self._db_path)

    async def upsert(
        self,
        names: list[str],
        embeddings: list[list[float]],
        descriptions: list[str],
        enabled_flags: list[bool] | None = None,
    ) -> None:
        """Insert or update tool embeddings. Uses overwrite semantics per
        tool name (delete-then-insert). Stores the enabled flag so search
        can filter disabled tools at the index level."""
        assert self._db is not None
        if not names:
            return

        data = []
        for i, (name, emb, desc) in enumerate(zip(names, embeddings, descriptions)):
            entry = {
                "name": name,
                "vector": emb,
                "description": desc,
                "enabled": enabled_flags[i] if enabled_flags else True,
            }
            data.append(entry)

        db = self._db
        table_name = self._table_name

        def _upsert():
            try:
                table = db.open_table(table_name)
                existing = table.to_pandas()
                existing_names = set(existing["name"].tolist())
                new_data = [d for d in data if d["name"] not in existing_names]
                update_names = [d["name"] for d in data if d["name"] in existing_names]

                if update_names:
                    for name in update_names:
                        table.delete(f'name = "{name}"')
                    update_data = [d for d in data if d["name"] in update_names]
                    table.add(update_data)
                    logger.debug("Updated %d existing tool embeddings", len(update_names))

                if new_data:
                    table.add(new_data)
                    logger.debug("Added %d new tool embeddings", len(new_data))
            except (FileNotFoundError, ValueError):
                db.create_table(table_name, data)
                logger.info("Created tool_embeddings table with %d entries", len(data))

        await asyncio.to_thread(_upsert)

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[ToolDefinition]:
        """Search for tools by embedding similarity. Returns ToolDefinition
        objects with name, description, and placeholder fields."""
        assert self._db is not None
        db = self._db
        table_name = self._table_name

        def _search():
            try:
                table = db.open_table(table_name)
            except (FileNotFoundError, ValueError, Exception) as e:
                logger.info("tool_embeddings table not available (%s), returning empty", e)
                return []

            query_array = np.array(query_embedding, dtype=np.float32)
            results = (
                table.search(query_array)
                .where("enabled = true", prefilter=True)
                .limit(top_k)
                .to_list()
            )

            definitions = []
            for row in results:
                definitions.append(
                    ToolDefinition(
                        name=row["name"],
                        description=row["description"],
                    )
                )
            return definitions

        return await asyncio.to_thread(_search)

    async def search_raw(
        self,
        query_embedding: list[float],
        top_k: int = 20,
    ) -> list[dict]:
        """Search for tools by embedding similarity. Returns raw result dicts
        including name, description, enabled flag, and _distance score.
        Used for debug/inspection purposes."""
        assert self._db is not None
        db = self._db
        table_name = self._table_name

        def _search():
            try:
                table = db.open_table(table_name)
            except (FileNotFoundError, ValueError, Exception) as e:
                logger.info("tool_embeddings table not available (%s)", e)
                return []

            query_array = np.array(query_embedding, dtype=np.float32)
            results = table.search(query_array).limit(top_k).to_list()

            out = []
            for row in results:
                out.append({
                    "name": row["name"],
                    "description": row["description"],
                    "enabled": bool(row.get("enabled", True)),
                    "distance": float(row.get("_distance", 0)),
                })
            return out

        return await asyncio.to_thread(_search)

    async def delete(self, names: str | list[str]) -> None:
        """Remove one or more tools from the index by name."""
        assert self._db is not None
        if isinstance(names, str):
            names = [names]
        if not names:
            return

        db = self._db
        table_name = self._table_name

        def _delete():
            try:
                table = db.open_table(table_name)
                for name in names:
                    table.delete(f'name = "{name}"')
                logger.debug("Deleted %d tools from vector index", len(names))
            except (FileNotFoundError, ValueError, Exception):
                pass

        await asyncio.to_thread(_delete)
