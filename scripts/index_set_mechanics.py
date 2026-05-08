"""Scrape and index WotC set mechanics articles into the reference layer.

Downloads mechanics articles from magic.wizards.com, chunks them with
mechanic-aware boundaries, computes embeddings, and stores them in the
reference_chunks table at tier 2.

Run: python scripts/index_set_mechanics.py [--db-path ...] [--skip-download]
"""

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sabermetrics.ingestion.reference import ReferenceIngestion
from sabermetrics.reference_layer.chunker import DocumentChunker
from sabermetrics.reference_layer.indexer import EmbeddingIndexer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Entry point for set mechanics article indexing."""
    parser = argparse.ArgumentParser(
        description="Scrape and index WotC set mechanics articles"
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("data/sabermetrics.db"),
        help="Path to the SQLite database",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Path to the data directory",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip downloading, use existing cached files",
    )
    parser.add_argument(
        "--force-reindex",
        action="store_true",
        help="Delete existing mechanics chunks and re-index all",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "config" / "set_mechanics_articles.yaml"

    if not config_path.exists():
        logger.error("Config not found: %s", config_path)
        sys.exit(1)

    ingestion = ReferenceIngestion(args.data_dir)
    chunker = DocumentChunker()
    indexer = EmbeddingIndexer(args.db_path)

    # Step 0: Optionally clear existing mechanics chunks
    if args.force_reindex:
        conn = sqlite3.connect(str(args.db_path))
        deleted = conn.execute(
            "DELETE FROM reference_chunks WHERE document LIKE 'mechanics_%'"
        ).rowcount
        conn.commit()
        conn.close()
        logger.info("Deleted %d existing mechanics chunks", deleted)

    # Step 1: Download mechanics articles
    if not args.skip_download:
        logger.info("=== Downloading set mechanics articles ===")
        article_paths = ingestion.fetch_set_mechanics_articles(config_path)
    else:
        reference_dir = args.data_dir / "reference"
        article_paths = sorted(reference_dir.glob("mechanics_*.txt"))
        logger.info(
            "Using %d cached mechanics articles", len(article_paths)
        )

    if not article_paths:
        logger.warning("No mechanics articles to index!")
        sys.exit(0)

    # Step 2: Chunk all articles
    logger.info("=== Chunking %d mechanics articles ===", len(article_paths))
    all_chunks = []
    for article_path in article_paths:
        chunks = chunker.chunk_mechanics_article(article_path)
        all_chunks.extend(chunks)
        logger.info(
            "  %s: %d chunks", article_path.stem, len(chunks)
        )

    logger.info("Total mechanics chunks to index: %d", len(all_chunks))

    if not all_chunks:
        logger.warning("No chunks produced!")
        sys.exit(0)

    # Step 3: Compute embeddings and store
    logger.info("=== Computing embeddings and indexing ===")
    stored = indexer.index_chunks(all_chunks)
    logger.info("Indexed %d mechanics chunks with embeddings", stored)

    # Step 4: Verify
    conn = sqlite3.connect(str(args.db_path))
    try:
        cursor = conn.execute(
            "SELECT document, COUNT(*) FROM reference_chunks "
            "WHERE document LIKE 'mechanics_%' GROUP BY document "
            "ORDER BY document"
        )
        by_doc = cursor.fetchall()

        cursor = conn.execute(
            "SELECT COUNT(*) FROM reference_chunks "
            "WHERE document LIKE 'mechanics_%'"
        )
        total = cursor.fetchone()[0]

        cursor = conn.execute("SELECT COUNT(*) FROM reference_chunks")
        grand_total = cursor.fetchone()[0]

        logger.info("=== Verification ===")
        logger.info("Mechanics chunks indexed: %d", total)
        logger.info("Total reference chunks in DB: %d", grand_total)
        logger.info("By article:")
        for doc, count in by_doc:
            logger.info("  %s: %d chunks", doc, count)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
