"""Build the deckbuilding knowledge base.

Orchestrates four phases:
  A. Ingest Game Knights decklists from Archidekt
  B. Analyze deckbuilding patterns across those decks
  C. Fetch EDHREC deckbuilding articles
  D. Build, chunk, embed, and store the knowledge base

Run: python scripts/build_deckbuilding_kb.py [--db-path ...] [--skip-ingest] [--skip-fetch]
"""

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sabermetrics.analytics.deck_patterns import (
    GameKnightsAnalyzer,
    KnowledgeBaseBuilder,
)
from sabermetrics.config import load_settings
from sabermetrics.ingestion.game_knights import GameKnightsIngestion
from sabermetrics.ingestion.reference import ReferenceIngestion
from sabermetrics.reference_layer.chunker import DocumentChunker
from sabermetrics.reference_layer.indexer import EmbeddingIndexer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Entry point for knowledge base building."""
    parser = argparse.ArgumentParser(
        description="Build the deckbuilding knowledge base"
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
        "--skip-ingest",
        action="store_true",
        help="Skip Phase A (Game Knights deck ingestion)",
    )
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip Phase C (EDHREC article fetching)",
    )
    args = parser.parse_args()

    settings = load_settings()
    reference_dir = args.data_dir / "reference"
    reference_dir.mkdir(parents=True, exist_ok=True)

    # === Phase A: Ingest Game Knights decklists ===
    if not args.skip_ingest:
        logger.info("=== Phase A: Ingesting Game Knights decklists ===")
        ingestion = GameKnightsIngestion(args.db_path)
        result = ingestion.sync()
        logger.info(
            "Ingestion complete: %d ingested, %d failed",
            result.items_ingested,
            result.items_failed,
        )
        if result.errors:
            for err in result.errors[:5]:
                logger.warning("  %s", err)
    else:
        logger.info("=== Phase A: Skipped (--skip-ingest) ===")

    # === Phase B: Analyze patterns ===
    logger.info("=== Phase B: Analyzing deckbuilding patterns ===")
    analyzer = GameKnightsAnalyzer(args.db_path)
    patterns = analyzer.analyze()
    logger.info("Analyzed %d decks", patterns.deck_count)
    logger.info(
        "  Lands: mean=%.1f, Ramp: mean=%.1f, Draw: mean=%.1f",
        patterns.land_counts.mean,
        patterns.ramp_counts.mean,
        patterns.draw_counts.mean,
    )
    logger.info(
        "  Removal: mean=%.1f, Wipes: mean=%.1f, Avg CMC: mean=%.2f",
        patterns.removal_counts.mean,
        patterns.wipe_counts.mean,
        patterns.avg_cmc.mean,
    )
    logger.info("  Most played cards: %d", len(patterns.most_played_cards))

    # === Phase C: Fetch EDHREC articles ===
    edhrec_texts: list[str] = []
    edhrec_articles = settings.knowledge_base.edhrec_articles

    if not args.skip_fetch and edhrec_articles:
        logger.info("=== Phase C: Fetching %d EDHREC articles ===", len(edhrec_articles))
        ref_ingestion = ReferenceIngestion(args.data_dir)

        for article in edhrec_articles:
            slug = article.get("slug", "")
            url = article.get("url", "")
            if not slug or not url:
                continue

            output_path = reference_dir / f"edhrec_{slug}.txt"
            if output_path.exists():
                logger.info("Article '%s' already cached", slug)
                edhrec_texts.append(output_path.read_text(encoding="utf-8"))
                continue

            try:
                import httpx

                ref_ingestion._rate_limiter.wait()
                resp = httpx.get(
                    url,
                    timeout=30,
                    follow_redirects=True,
                    headers={"User-Agent": "Sabermetrics/1.0 (personal research)"},
                )
                resp.raise_for_status()
                text = ReferenceIngestion._extract_text_from_html(resp.text)
                output_path.write_text(text, encoding="utf-8")
                edhrec_texts.append(text)
                logger.info("Saved article '%s' (%d bytes)", slug, len(text))
            except Exception as e:
                logger.warning("Failed to fetch article '%s': %s", slug, e)
    elif args.skip_fetch:
        logger.info("=== Phase C: Skipped (--skip-fetch) ===")
        # Load any cached articles
        for article in edhrec_articles:
            slug = article.get("slug", "")
            cached = reference_dir / f"edhrec_{slug}.txt"
            if cached.exists():
                edhrec_texts.append(cached.read_text(encoding="utf-8"))
    else:
        logger.info("=== Phase C: No EDHREC articles configured ===")

    # === Phase D: Build knowledge base, chunk, embed ===
    logger.info("=== Phase D: Building knowledge base document ===")
    builder = KnowledgeBaseBuilder()
    kb_text = builder.build(patterns, edhrec_texts)

    kb_path = reference_dir / "deckbuilding_knowledge_base.txt"
    kb_path.write_text(kb_text, encoding="utf-8")
    logger.info("Knowledge base saved to %s (%d bytes)", kb_path, len(kb_text))

    # Chunk the knowledge base document
    chunker = DocumentChunker()
    chunks = chunker.chunk_article(kb_path, tier=2)
    logger.info("Chunked into %d pieces", len(chunks))

    # Override document name for all chunks
    for chunk in chunks:
        chunk.document = "deckbuilding_knowledge_base"

    if not chunks:
        logger.warning("No chunks produced — skipping indexing")
        return

    # Delete old KB chunks before inserting new ones
    conn = sqlite3.connect(str(args.db_path))
    try:
        deleted = conn.execute(
            "DELETE FROM reference_chunks WHERE document = ?",
            ("deckbuilding_knowledge_base",),
        ).rowcount
        conn.commit()
        if deleted:
            logger.info("Deleted %d old knowledge base chunks", deleted)
    finally:
        conn.close()

    # Embed and store
    indexer = EmbeddingIndexer(args.db_path)
    stored = indexer.index_chunks(chunks)
    logger.info("Indexed %d knowledge base chunks with embeddings", stored)

    # Verify
    conn = sqlite3.connect(str(args.db_path))
    try:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM reference_chunks WHERE document = ?",
            ("deckbuilding_knowledge_base",),
        )
        total = cursor.fetchone()[0]
        logger.info("=== Verification: %d KB chunks in database ===", total)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
