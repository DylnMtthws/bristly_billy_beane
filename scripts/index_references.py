"""Index reference documents into the reference_chunks table.

Downloads reference documents, chunks them, computes embeddings,
and stores everything in the database.

Run: python scripts/index_references.py [--db-path ...] [--skip-download]
"""

import argparse
import logging
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
    """Entry point for reference indexing."""
    parser = argparse.ArgumentParser(
        description="Index reference documents for RAG grounding"
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
        help="Skip downloading, use existing files",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    config_dir = project_root / "config"
    data_dir = args.data_dir
    reference_dir = data_dir / "reference"

    ingestion = ReferenceIngestion(data_dir)
    chunker = DocumentChunker()
    indexer = EmbeddingIndexer(args.db_path)

    all_chunks = []

    # Step 1: Download reference documents
    if not args.skip_download:
        logger.info("=== Downloading reference documents ===")

        # Comprehensive Rules
        try:
            rules_path = ingestion.fetch_comprehensive_rules()
            logger.info("Comprehensive Rules: %s", rules_path)
        except Exception as e:
            logger.warning("Failed to download Comprehensive Rules: %s", e)
            rules_path = reference_dir / "comprehensive_rules.txt"

        # Commander Rules
        try:
            cmd_rules_path = ingestion.fetch_commander_rules()
            logger.info("Commander Rules: %s", cmd_rules_path)
        except Exception as e:
            logger.warning("Failed to download Commander Rules: %s", e)
            cmd_rules_path = reference_dir / "commander_rules.txt"

        # Strategic articles
        articles_config = config_dir / "strategic_articles.yaml"
        try:
            article_paths = ingestion.fetch_strategic_articles(articles_config)
            logger.info("Fetched %d articles", len(article_paths))
        except Exception as e:
            logger.warning("Failed to fetch articles: %s", e)
            article_paths = []
    else:
        rules_path = reference_dir / "comprehensive_rules.txt"
        cmd_rules_path = reference_dir / "commander_rules.txt"
        article_paths = list(reference_dir.glob("article_*.txt"))

    # Step 2: Chunk documents
    logger.info("=== Chunking documents ===")

    if rules_path.exists():
        chunks = chunker.chunk_comprehensive_rules(rules_path)
        all_chunks.extend(chunks)
        logger.info("Comprehensive Rules: %d chunks", len(chunks))

    if cmd_rules_path.exists():
        chunks = chunker.chunk_commander_rules(cmd_rules_path)
        all_chunks.extend(chunks)
        logger.info("Commander Rules: %d chunks", len(chunks))

    for article_path in article_paths:
        chunks = chunker.chunk_article(article_path)
        all_chunks.extend(chunks)

    # Game changers as reference chunk
    game_changers_path = config_dir / "game_changers.yaml"
    if game_changers_path.exists():
        chunks = chunker.chunk_game_changers(game_changers_path)
        all_chunks.extend(chunks)
        logger.info("Game changers: %d chunks", len(chunks))

    logger.info("Total chunks to index: %d", len(all_chunks))

    if not all_chunks:
        logger.warning("No chunks to index!")
        return

    # Step 3: Compute embeddings and store
    logger.info("=== Computing embeddings and indexing ===")
    stored = indexer.index_chunks(all_chunks)
    logger.info("Indexed %d chunks with embeddings", stored)

    # Step 4: Verify
    import sqlite3

    conn = sqlite3.connect(str(args.db_path))
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM reference_chunks")
        total = cursor.fetchone()[0]
        cursor = conn.execute(
            "SELECT COUNT(*) FROM reference_chunks WHERE embedding IS NOT NULL"
        )
        with_embeddings = cursor.fetchone()[0]
        cursor = conn.execute(
            "SELECT document, COUNT(*) FROM reference_chunks GROUP BY document"
        )
        by_doc = cursor.fetchall()

        logger.info("=== Verification ===")
        logger.info("Total chunks in DB: %d", total)
        logger.info("Chunks with embeddings: %d", with_embeddings)
        for doc, count in by_doc:
            logger.info("  %s: %d chunks", doc, count)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
