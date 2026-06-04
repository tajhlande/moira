import asyncio
import logging

from moira.embeddings.interfaces import EmbeddingProvider

logger = logging.getLogger(__name__)


class LocalEmbeddingProvider(EmbeddingProvider):
    """Local embedding via sentence-transformers. Downloads the model on
    first use and caches it for the process lifetime. Runs on CPU.

    All sentence-transformers calls (model loading, encoding) are
    synchronous CPU-bound work.  We offload them to the default thread
    executor so they never block the asyncio event loop — which is
    critical for keeping SSE streams responsive during tool discovery.
    """

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

    def _encode(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model()
        result = self._model.encode(texts, normalize_embeddings=True)
        return [r.tolist() for r in result]

    async def embed(self, text: str) -> list[float]:
        return (await asyncio.to_thread(self._encode, [text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._encode, texts)

    def dimension(self) -> int:
        self._ensure_model()
        assert self._dim is not None
        return self._dim
