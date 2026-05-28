import logging

from moira.embeddings.interfaces import EmbeddingProvider
from moira.persistence.lancedb.tool_embeddings import ToolEmbeddingRepository
from moira.tools.base import ToolDefinition

logger = logging.getLogger(__name__)


class ToolDiscovery:
    """Semantic search over the tool catalog. Embeds the query using the
    configured embedding provider and searches LanceDB for the top-K
    most relevant tools. Returns raw ToolDefinition objects for the
    Tool Selection node to choose from."""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        embedding_repo: ToolEmbeddingRepository,
    ):
        self._embedding_provider = embedding_provider
        self._embedding_repo = embedding_repo

    async def ingest_tools(self, tools: list[ToolDefinition]) -> None:
        """Embed tool descriptions and store in LanceDB. Called at startup
        after the catalog is loaded."""
        if not tools:
            logger.info("No tools to ingest")
            return
        logger.info("Ingesting %d tools into vector store", len(tools))
        descriptions = [t.description for t in tools]
        embeddings = await self._embedding_provider.embed_batch(descriptions)
        await self._embedding_repo.upsert(
            names=[t.name for t in tools],
            embeddings=embeddings,
            descriptions=descriptions,
        )
        logger.info("Ingested %d tools", len(tools))

    async def discover(self, query: str, top_k: int = 5) -> list[ToolDefinition]:
        """Embed the query and search for the top-K most relevant tools."""
        logger.debug("Discovering tools for query: %s", query[:100])
        query_embedding = await self._embedding_provider.embed(query)
        results = await self._embedding_repo.search(query_embedding, top_k=top_k)
        logger.info("Discovered %d tools for query", len(results))
        return results
