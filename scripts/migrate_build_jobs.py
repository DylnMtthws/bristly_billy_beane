"""Apply the additive W5 build-jobs migration.

Idempotent: safe for both an existing deployment database and a fresh file.
Run: python scripts/migrate_build_jobs.py [--db-path data/sabermetrics.db]
"""

import argparse
import sqlite3
from pathlib import Path

try:
    from scripts.setup_db import ensure_cedh_schema
except ModuleNotFoundError:  # direct ``python scripts/migrate_build_jobs.py``
    from setup_db import ensure_cedh_schema


def migrate(db_path: Path) -> None:
    """Create the build-jobs table and indexes without changing existing data."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        ensure_cedh_schema(conn)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=Path("data/sabermetrics.db"))
    args = parser.parse_args()
    migrate(args.db_path)
    print(f"Build-jobs migration applied to {args.db_path}")
