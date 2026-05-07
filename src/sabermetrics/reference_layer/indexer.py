"""Embedding indexer for reference chunks (D3.5).

Computes embeddings for reference chunks using sentence-transformers
and stores them as numpy array bytes in the reference_chunks table.
"""

import logging
import sqlite3
from pathlib import Path

import numpy as np

from sabermetrics.reference_layer.chunker import Chunk

logger = logging.getLogger(__name__)


class EmbeddingIndexer:
    """Computes and stores embeddings for reference chunks.

    Uses sentence-transformers all-MiniLM-L6-v2 model for embeddings.
    Embeddings are stored as numpy array bytes in SQLite BLOB column.
    """

    def __init__(
        self,
        db_path: Path,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        device: str = "cpu",
    ) -> None:
        self.db_path = db_path
        self.model_name = model_name
        self.device = device
        self._model = None

    def _get_model(self):  # type: ignore[no-untyped-def]
        """Lazy-load the sentence-transformers model."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model: %s", self.model_name)
            self._model = SentenceTransformer(
                self.model_name, device=self.device
            )
        return self._model

    def index_chunks(self, chunks: list[Chunk], batch_size: int = 64) -> int:
        """Compute embeddings and store chunks in the database.

        Args:
            chunks: List of Chunks to embed and store.
            batch_size: Number of chunks to embed at once.

        Returns:
            Number of chunks stored.
        """
        if not chunks:
            return 0

        model = self._get_model()
        conn = sqlite3.connect(str(self.db_path))
        stored = 0

        try:
            # Process in batches for memory efficiency
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i : i + batch_size]
                texts = [c.content for c in batch]

                # Compute embeddings
                embeddings = model.encode(
                    texts,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )

                # Store each chunk with its embedding
                for chunk, embedding in zip(batch, embeddings):
                    embedding_bytes = np.array(embedding, dtype=np.float32).tobytes()
                    conn.execute(
                        """INSERT OR REPLACE INTO reference_chunks
                        (id, document, section, tier, content, embedding,
                         last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                        (
                            chunk.id,
                            chunk.document,
                            chunk.section,
                            chunk.tier,
                            chunk.content,
                            embedding_bytes,
                        ),
                    )
                    stored += 1

                conn.commit()
                if stored % 200 == 0:
                    logger.info("Indexed %d / %d chunks", stored, len(chunks))

            logger.info("Indexing complete: %d chunks stored", stored)
        finally:
            conn.close()

        return stored

    def compute_embedding(self, text: str) -> np.ndarray:
        """Compute embedding for a single text query.

        Args:
            text: Query text to embed.

        Returns:
            Numpy array of the embedding vector.
        """
        model = self._get_model()
        embedding = model.encode(text, convert_to_numpy=True)
        return np.array(embedding, dtype=np.float32)
