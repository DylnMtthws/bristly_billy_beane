"""Embedding wrapper for application-wide use (D4.8).

Wraps sentence-transformers for computing text embeddings with
in-memory caching of recently embedded texts.
"""

import hashlib
import logging
from collections import OrderedDict

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingCache:
    """LRU cache for text embeddings."""

    def __init__(self, max_size: int = 10000) -> None:
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> np.ndarray | None:
        """Get cached embedding, moving it to end (most recent)."""
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: str, embedding: np.ndarray) -> None:
        """Cache an embedding, evicting oldest if at capacity."""
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            self._cache[key] = embedding

    def clear(self) -> None:
        """Clear the cache."""
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


class EmbeddingService:
    """Application-wide embedding service with caching.

    Lazy-loads the sentence-transformers model on first use.
    Caches recent embeddings in memory for fast repeated lookups.
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        cache_size: int = 10000,
    ) -> None:
        self._model_name = model_name
        self._model = None
        self._cache = EmbeddingCache(max_size=cache_size)

    def _load_model(self):  # type: ignore[no-untyped-def]
        """Lazy-load the embedding model."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model: %s", self._model_name)
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def _cache_key(self, text: str) -> str:
        """Generate a cache key for text."""
        return hashlib.md5(text.encode()).hexdigest()

    def embed(self, text: str) -> np.ndarray:
        """Compute embedding for a single text.

        Args:
            text: Text to embed.

        Returns:
            Embedding vector as numpy array.
        """
        key = self._cache_key(text)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        model = self._load_model()
        embedding = model.encode(text, show_progress_bar=False)
        embedding = np.array(embedding, dtype=np.float32)
        self._cache.put(key, embedding)
        return embedding

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Compute embeddings for a batch of texts.

        Checks cache first, only computes uncached embeddings.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.
        """
        results: list[np.ndarray | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for i, text in enumerate(texts):
            key = self._cache_key(text)
            cached = self._cache.get(key)
            if cached is not None:
                results[i] = cached
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        if uncached_texts:
            model = self._load_model()
            embeddings = model.encode(uncached_texts, show_progress_bar=False)

            for i, idx in enumerate(uncached_indices):
                emb = np.array(embeddings[i], dtype=np.float32)
                key = self._cache_key(uncached_texts[i])
                self._cache.put(key, emb)
                results[idx] = emb

        return results  # type: ignore[return-value]

    def similarity(self, text_a: str, text_b: str) -> float:
        """Compute cosine similarity between two texts.

        Args:
            text_a: First text.
            text_b: Second text.

        Returns:
            Cosine similarity score (0.0 to 1.0).
        """
        emb_a = self.embed(text_a)
        emb_b = self.embed(text_b)
        dot = np.dot(emb_a, emb_b)
        norm = np.linalg.norm(emb_a) * np.linalg.norm(emb_b)
        if norm == 0:
            return 0.0
        return float(dot / norm)

    @property
    def cache_size(self) -> int:
        """Current cache size."""
        return self._cache.size

    def clear_cache(self) -> None:
        """Clear the embedding cache."""
        self._cache.clear()


# Module-level singleton
_embedding_service: EmbeddingService | None = None


def get_embedding_service(
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
) -> EmbeddingService:
    """Get the singleton EmbeddingService instance."""
    global _embedding_service
    if _embedding_service is None or _embedding_service._model_name != model_name:
        _embedding_service = EmbeddingService(model_name=model_name)
    return _embedding_service
