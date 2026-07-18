"""Create/refresh the canonical `card_candidates` view (Option A criterion 2b).

One row per card name (cheapest legal printing). Additive and idempotent — it
touches no card rows, only (re)defines a view. Run once against the production
DB; the deck pipeline also ensures the view exists at query time.

Usage:
    python scripts/create_candidate_view.py [path/to/sabermetrics.db]
"""

import sqlite3
import sys
from pathlib import Path

from sabermetrics.analytics.filters import CANDIDATE_VIEW_SQL


def main() -> None:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/sabermetrics.db")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DROP VIEW IF EXISTS card_candidates")
        conn.execute(CANDIDATE_VIEW_SQL)
        conn.commit()
        rows = conn.execute("SELECT COUNT(*) FROM card_candidates").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        print(f"card_candidates view ready: {rows} candidates from {total} printings")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
