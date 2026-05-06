"""Initial data ingestion script.

Orchestrates first-time data load for the Sabermetrics database.
Run: python scripts/initial_ingestion.py [--scryfall-only] [--skip-prices] [--db-path ...]
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path so we can import sabermetrics
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sabermetrics.errors import DegradableError, FatalError
from sabermetrics.ingestion.scryfall import ScryfallIngestion

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def ensure_db_exists(db_path: Path) -> None:
    """Run setup_db if the database doesn't exist."""
    if not db_path.exists():
        logger.info("Database not found at %s, creating...", db_path)
        # Import here to avoid circular issues
        from setup_db import setup_database

        setup_database(db_path)
        logger.info("Database created successfully")
    else:
        logger.info("Database found at %s", db_path)


def run_scryfall_ingestion(db_path: Path) -> bool:
    """Run Scryfall bulk data ingestion.

    Args:
        db_path: Path to the SQLite database.

    Returns:
        True if ingestion succeeded.
    """
    logger.info("Starting Scryfall ingestion...")
    source = ScryfallIngestion(db_path=db_path)

    if not source.is_available():
        logger.error("Scryfall API is not reachable")
        return False

    result = source.sync(full=True)

    logger.info("Scryfall ingestion complete:")
    logger.info("  Cards ingested: %d", result.items_ingested)
    logger.info("  Prices recorded: %d", result.items_updated)
    logger.info("  Failed: %d", result.items_failed)
    logger.info("  Success: %s", result.success)

    if result.errors:
        logger.warning("  Errors (%d):", len(result.errors))
        for err in result.errors[:10]:
            logger.warning("    - %s", err)
        if len(result.errors) > 10:
            logger.warning("    ... and %d more", len(result.errors) - 10)

    return result.success


def main() -> None:
    """Entry point for initial ingestion."""
    parser = argparse.ArgumentParser(
        description="Initial data ingestion for Sabermetrics"
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("data/sabermetrics.db"),
        help="Path to the SQLite database file",
    )
    parser.add_argument(
        "--scryfall-only",
        action="store_true",
        help="Only ingest Scryfall data (skip other sources)",
    )
    parser.add_argument(
        "--skip-prices",
        action="store_true",
        help="Skip price data ingestion",
    )
    args = parser.parse_args()

    try:
        # Ensure database exists
        ensure_db_exists(args.db_path)

        # Scryfall ingestion
        scryfall_ok = run_scryfall_ingestion(args.db_path)
        if not scryfall_ok:
            logger.error("Scryfall ingestion failed")
            sys.exit(1)

        if args.scryfall_only:
            logger.info("--scryfall-only flag set, skipping other sources")
            logger.info("Initial ingestion complete!")
            return

        # Future: add other sources here
        logger.info("Initial ingestion complete!")

    except FatalError as e:
        logger.error("Fatal error during ingestion: %s", e)
        sys.exit(1)
    except DegradableError as e:
        logger.warning("Degradable error (continuing): %s", e)
    except KeyboardInterrupt:
        logger.info("Ingestion interrupted by user")
        sys.exit(130)


if __name__ == "__main__":
    main()
