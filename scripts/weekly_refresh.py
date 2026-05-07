#!/usr/bin/env python3
"""Weekly refresh script (D8.2).

Runs Sunday 3am via launchd.
Actions:
  1. TopDeck.gg tournament sync
  2. Decklist sources sync (Moxfield, Archidekt, Deckstats)
  3. EDHREC sync for tracked commanders
  4. Spellbook combo sync
  5. Co-occurrence matrix rebuild
  6. Card Win Equity recomputation
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sabermetrics.utils.logging import setup_job_logging

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sabermetrics.db"


def _sync_source(name: str, source_cls, logger) -> list[str]:
    """Sync a single ingestion source, returning errors."""
    errors = []
    try:
        source = source_cls(DB_PATH)
        result = source.sync(full=False)
        logger.info(
            "%s: ingested=%d, updated=%d, failed=%d, success=%s",
            name, result.items_ingested, result.items_updated,
            result.items_failed, result.success,
        )
        if result.errors:
            for err in result.errors[:3]:
                logger.warning("%s error: %s", name, err)
                errors.append(f"{name}: {err}")
    except Exception as e:
        logger.error("%s sync failed: %s", name, e)
        errors.append(f"{name}: {e}")
    return errors


def main() -> int:
    logger = setup_job_logging("weekly")
    start = time.time()
    all_errors: list[str] = []

    # --- Step 1: TopDeck.gg ---
    logger.info("Step 1: TopDeck.gg sync")
    from sabermetrics.ingestion.topdeck import TopDeckIngestion
    all_errors.extend(_sync_source("topdeck", TopDeckIngestion, logger))

    # --- Step 2: Decklist sources ---
    logger.info("Step 2: Decklist sources sync")
    from sabermetrics.ingestion.moxfield import MoxfieldIngestion
    from sabermetrics.ingestion.archidekt import ArchidektIngestion
    from sabermetrics.ingestion.deckstats import DeckstatsIngestion

    for name, cls in [
        ("moxfield", MoxfieldIngestion),
        ("archidekt", ArchidektIngestion),
        ("deckstats", DeckstatsIngestion),
    ]:
        all_errors.extend(_sync_source(name, cls, logger))

    # --- Step 3: EDHREC ---
    logger.info("Step 3: EDHREC sync")
    from sabermetrics.ingestion.edhrec import EDHRECIngestion
    all_errors.extend(_sync_source("edhrec", EDHRECIngestion, logger))

    # --- Step 4: Spellbook ---
    logger.info("Step 4: Spellbook combo sync")
    from sabermetrics.ingestion.spellbook import SpellbookIngestion
    all_errors.extend(_sync_source("spellbook", SpellbookIngestion, logger))

    # --- Step 5: Co-occurrence rebuild ---
    logger.info("Step 5: Co-occurrence matrix rebuild")
    try:
        from sabermetrics.analytics.cooccurrence import build_cooccurrence

        entries = build_cooccurrence(DB_PATH, min_decks=3)
        logger.info("Co-occurrence: %d entries built", entries)
    except Exception as e:
        logger.error("Co-occurrence rebuild failed: %s", e)
        all_errors.append(f"cooccurrence: {e}")

    # --- Step 6: CWE recomputation ---
    logger.info("Step 6: Card Win Equity recomputation")
    try:
        from sabermetrics.analytics.card_win_equity import compute_card_win_equity

        cwe_count = compute_card_win_equity(DB_PATH, min_sample_size=5)
        logger.info("CWE: %d entries computed", cwe_count)
    except Exception as e:
        logger.error("CWE recomputation failed: %s", e)
        all_errors.append(f"cwe: {e}")

    elapsed = time.time() - start
    logger.info(
        "Weekly refresh complete in %.1fs with %d errors",
        elapsed, len(all_errors),
    )

    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
