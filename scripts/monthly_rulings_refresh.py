#!/usr/bin/env python3
"""Monthly rulings refresh script (D8.3).

Runs 1st Sunday 4am via launchd.
Actions:
  1. magicthegathering.io rulings sync
  2. Iterates by oracle_id; rate-limited 1 req/sec
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sabermetrics.utils.logging import setup_job_logging

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sabermetrics.db"


def main() -> int:
    logger = setup_job_logging("monthly_rulings")
    start = time.time()
    errors: list[str] = []

    # --- Rulings sync via mtgapi ---
    logger.info("Step 1: mtgapi rulings sync")
    try:
        from sabermetrics.ingestion.mtgapi import MtgApiIngestion

        mtgapi = MtgApiIngestion(DB_PATH)
        result = mtgapi.sync(full=False)
        logger.info(
            "Rulings: ingested=%d, updated=%d, failed=%d, success=%s",
            result.items_ingested, result.items_updated,
            result.items_failed, result.success,
        )
        if result.errors:
            for err in result.errors[:5]:
                logger.warning("Rulings error: %s", err)
                errors.append(f"rulings: {err}")
    except Exception as e:
        logger.error("Rulings sync failed: %s", e)
        errors.append(f"rulings: {e}")

    elapsed = time.time() - start
    logger.info(
        "Monthly rulings refresh complete in %.1fs with %d errors",
        elapsed, len(errors),
    )

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
