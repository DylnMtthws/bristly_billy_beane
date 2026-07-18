"""Prune orphaned commander_profiles rows (follow-up to criterion 4).

The seed rows in commander_profiles use synthetic UUIDs that match no card, so
they never serve a real cache hit — they only make the table look populated.
This deletes any profile whose commander_id is not a real card. Harmless
(orphans can never match a real build's lookup) but removes the misleading
clutter. Idempotent; validate on a copy before running on prod.

Usage:
    python scripts/prune_orphan_profiles.py [path/to/sabermetrics.db] [--apply]
"""

import argparse
import sqlite3
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", nargs="?", default="data/sabermetrics.db")
    ap.add_argument(
        "--apply", action="store_true",
        help="actually delete (default is a dry run that only reports counts)",
    )
    args = ap.parse_args()

    conn = sqlite3.connect(str(Path(args.db)))
    try:
        total = conn.execute("SELECT COUNT(*) FROM commander_profiles").fetchone()[0]
        orphans = conn.execute(
            "SELECT COUNT(*) FROM commander_profiles "
            "WHERE commander_id NOT IN (SELECT id FROM cards)"
        ).fetchone()[0]
        print(f"commander_profiles: {total} total, {orphans} orphaned")
        if args.apply and orphans:
            conn.execute(
                "DELETE FROM commander_profiles "
                "WHERE commander_id NOT IN (SELECT id FROM cards)"
            )
            conn.commit()
            print(f"deleted {orphans} orphaned profiles")
        elif orphans:
            print("dry run — re-run with --apply to delete")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
