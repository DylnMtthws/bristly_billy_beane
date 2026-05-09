#!/usr/bin/env python3
"""Nightly refresh script (D8.1).

Runs daily at 2am via launchd.
Actions:
  1. Scryfall card sync (bulk download, ~10 min)
  2. Update health monitoring for all sources
"""

import sys
import time
from pathlib import Path

# Ensure src/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sabermetrics.utils.logging import setup_job_logging

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sabermetrics.db"


def main() -> int:
    logger = setup_job_logging("nightly")
    start = time.time()
    errors: list[str] = []

    # --- Step 1: Scryfall sync ---
    logger.info("Step 1: Scryfall sync")
    try:
        from sabermetrics.ingestion.scryfall import ScryfallIngestion

        scryfall = ScryfallIngestion(DB_PATH)
        result = scryfall.sync(full=False)
        logger.info(
            "Scryfall: ingested=%d, updated=%d, failed=%d, success=%s",
            result.items_ingested, result.items_updated,
            result.items_failed, result.success,
        )
        if result.errors:
            for err in result.errors[:5]:
                logger.warning("Scryfall error: %s", err)
                errors.append(f"scryfall: {err}")
    except Exception as e:
        logger.error("Scryfall sync failed: %s", e)
        errors.append(f"scryfall: {e}")

    # --- Step 2: Populate ramp_candidates ---
    logger.info("Step 2: Populate ramp_candidates table")
    try:
        from sabermetrics.analytics.ramp_detector import populate_ramp_candidates

        result = populate_ramp_candidates(DB_PATH)
        logger.info(
            "Ramp candidates: rows=%d, skipped=%s, version=%s",
            result["rows"], result["skipped"], result["version"],
        )
    except Exception as e:
        logger.error("Ramp candidates population failed: %s", e)
        errors.append(f"ramp_candidates: {e}")

    # --- Step 3: Populate removal_candidates ---
    logger.info("Step 3: Populate removal_candidates table")
    try:
        from sabermetrics.analytics.removal_detector import populate_removal_candidates

        result = populate_removal_candidates(DB_PATH)
        logger.info(
            "Removal candidates: rows=%d, skipped=%s, version=%s",
            result["rows"], result["skipped"], result["version"],
        )
    except Exception as e:
        logger.error("Removal candidates population failed: %s", e)
        errors.append(f"removal_candidates: {e}")

    # --- Step 4: Populate protection_candidates ---
    logger.info("Step 4: Populate protection_candidates table")
    try:
        from sabermetrics.analytics.protection_detector import populate_protection_candidates

        result = populate_protection_candidates(DB_PATH)
        logger.info(
            "Protection candidates: rows=%d, skipped=%s, version=%s",
            result["rows"], result["skipped"], result["version"],
        )
    except Exception as e:
        logger.error("Protection candidates population failed: %s", e)
        errors.append(f"protection_candidates: {e}")

    # --- Step 5: Health monitoring ---
    logger.info("Step 5: Health monitoring update")
    try:
        from sabermetrics.ingestion.health import SourceHealthMonitor

        monitor = SourceHealthMonitor(DB_PATH)
        report = monitor.check_all()
        for source_name, status in report.items():
            logger.info("Health: %s = %s", source_name, status)
    except Exception as e:
        logger.warning("Health check failed: %s", e)
        errors.append(f"health: {e}")

    elapsed = time.time() - start
    logger.info(
        "Nightly refresh complete in %.1fs with %d errors",
        elapsed, len(errors),
    )

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
