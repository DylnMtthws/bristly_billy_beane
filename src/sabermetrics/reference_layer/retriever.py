"""Reference chunk retrieval via cosine similarity (D3.6).

Implements the ReferenceRetriever contract from api_contracts.md Section 1.3.
Queries reference_chunks using embedding similarity with in-memory caching.
"""

import logging
import sqlite3
import time
from pathlib import Path

import numpy as np
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ReferenceQuery(BaseModel):
    """Query specification for reference retrieval."""

    query_text: str
    tier_filter: list[int] | None = None
    document_filter: list[str] | None = None
    top_k: int = 10


class RetrievedChunk(BaseModel):
    """A retrieved reference chunk with similarity score."""

    id: str
    document: str
    section: str | None
    tier: int
    content: str
    similarity_score: float


class ReferenceRetriever:
    """Query reference chunks via cosine similarity.

    Caches embeddings in memory with a 1-hour TTL for fast retrieval.
    Falls back to text search if embedding model is unavailable.
    """

    CACHE_TTL_SECONDS: int = 3600  # 1 hour

    def __init__(self, db_path: Path, indexer=None) -> None:  # type: ignore[no-untyped-def]
        """Initialize the retriever.

        Args:
            db_path: Path to the SQLite database.
            indexer: Optional EmbeddingIndexer for computing query embeddings.
                     If None, one will be created on first use.
        """
        self.db_path = db_path
        self._indexer = indexer
        self._cache: dict[str, tuple[np.ndarray, dict]] | None = None
        self._cache_time: float = 0.0

    def _get_indexer(self):  # type: ignore[no-untyped-def]
        """Lazy-load the embedding indexer."""
        if self._indexer is None:
            from sabermetrics.reference_layer.indexer import EmbeddingIndexer

            self._indexer = EmbeddingIndexer(self.db_path)
        return self._indexer

    def retrieve(self, query: ReferenceQuery) -> list[RetrievedChunk]:
        """Retrieve top-K relevant reference chunks.

        Args:
            query: Query specification with filters.

        Returns:
            Up to top_k chunks, sorted by similarity descending.
            Empty list if no chunks meet relevance threshold.
        """
        try:
            return self._retrieve_by_embedding(query)
        except Exception as e:
            logger.warning(
                "Embedding retrieval failed, falling back to text search: %s", e
            )
            return self._retrieve_by_text(query)

    def _retrieve_by_embedding(
        self, query: ReferenceQuery
    ) -> list[RetrievedChunk]:
        """Retrieve chunks using cosine similarity over embeddings."""
        # Compute query embedding
        indexer = self._get_indexer()
        query_embedding = indexer.compute_embedding(query.query_text)

        # Load chunk embeddings (with caching)
        chunks_data = self._load_chunks_cache()

        if not chunks_data:
            return []

        # Compute similarities
        results: list[tuple[float, dict]] = []
        for chunk_id, data in chunks_data.items():
            embedding = data["embedding"]
            meta = data["meta"]

            # Apply filters
            if query.tier_filter and meta["tier"] not in query.tier_filter:
                continue
            if (
                query.document_filter
                and meta["document"] not in query.document_filter
            ):
                continue

            similarity = self._cosine_similarity(query_embedding, embedding)
            results.append((similarity, meta))

        # Sort by similarity descending
        results.sort(key=lambda x: x[0], reverse=True)

        # Return top-k
        retrieved: list[RetrievedChunk] = []
        for similarity, meta in results[: query.top_k]:
            retrieved.append(
                RetrievedChunk(
                    id=meta["id"],
                    document=meta["document"],
                    section=meta["section"],
                    tier=meta["tier"],
                    content=meta["content"],
                    similarity_score=round(float(similarity), 4),
                )
            )

        return retrieved

    def _retrieve_by_text(
        self, query: ReferenceQuery
    ) -> list[RetrievedChunk]:
        """Fallback: retrieve chunks using simple text matching."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row

        try:
            sql = "SELECT * FROM reference_chunks WHERE 1=1"
            params: list = []

            if query.tier_filter:
                placeholders = ",".join("?" for _ in query.tier_filter)
                sql += f" AND tier IN ({placeholders})"
                params.extend(query.tier_filter)

            if query.document_filter:
                placeholders = ",".join("?" for _ in query.document_filter)
                sql += f" AND document IN ({placeholders})"
                params.extend(query.document_filter)

            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()

            # Simple text matching: count query word occurrences
            query_words = set(query.query_text.lower().split())
            scored: list[tuple[float, dict]] = []

            for row in rows:
                content_lower = row["content"].lower()
                matches = sum(
                    1 for w in query_words if w in content_lower
                )
                if matches > 0:
                    score = matches / len(query_words)
                    scored.append(
                        (
                            score,
                            {
                                "id": row["id"],
                                "document": row["document"],
                                "section": row["section"],
                                "tier": row["tier"],
                                "content": row["content"],
                            },
                        )
                    )

            scored.sort(key=lambda x: x[0], reverse=True)

            return [
                RetrievedChunk(
                    id=meta["id"],
                    document=meta["document"],
                    section=meta["section"],
                    tier=meta["tier"],
                    content=meta["content"],
                    similarity_score=round(score, 4),
                )
                for score, meta in scored[: query.top_k]
            ]
        finally:
            conn.close()

    def _load_chunks_cache(self) -> dict[str, dict]:
        """Load all chunk embeddings into memory, with TTL caching.

        Returns:
            Dict mapping chunk_id to {embedding, meta} dicts.
        """
        now = time.monotonic()
        if (
            self._cache is not None
            and (now - self._cache_time) < self.CACHE_TTL_SECONDS
        ):
            return self._cache  # type: ignore[return-value]

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row

        try:
            cursor = conn.execute(
                "SELECT id, document, section, tier, content, embedding "
                "FROM reference_chunks WHERE embedding IS NOT NULL"
            )

            cache: dict[str, dict] = {}
            for row in cursor:
                embedding = np.frombuffer(
                    row["embedding"], dtype=np.float32
                )
                cache[row["id"]] = {
                    "embedding": embedding,
                    "meta": {
                        "id": row["id"],
                        "document": row["document"],
                        "section": row["section"],
                        "tier": row["tier"],
                        "content": row["content"],
                    },
                }

            self._cache = cache  # type: ignore[assignment]
            self._cache_time = now
            logger.info("Loaded %d chunk embeddings into cache", len(cache))
            return cache
        finally:
            conn.close()

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))
