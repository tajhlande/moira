import logging

from moira.embeddings.interfaces import EmbeddingProvider

logger = logging.getLogger(__name__)


class LocalEmbeddingProvider(EmbeddingProvider):
    """Local embedding via sentence-transformers. Downloads the model on
    first use and caches it for the process lifetime. Runs on CPU."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model = None
        self._dim: int | None = None

    def _ensure_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model '%s'", self._model_name)
            self._model = SentenceTransformer(self._model_name)
            self._dim = self._model.get_embedding_dimension()
            logger.info("Embedding model loaded, dimension=%d", self._dim)

    async def embed(self, text: str) -> list[float]:
        self._ensure_model()
        result = self._model.encode([text], normalize_embeddings=True)
        return result[0].tolist()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._ensure_model()
        result = self._model.encode(texts, normalize_embeddings=True)
        return [r.tolist() for r in result]

    def dimension(self) -> int:
        self._ensure_model()
        assert self._dim is not None
        return self._dim
