"""Seed one account-owned Night Ritual playmat from a local image.

Operator-only. Pass an explicit owner id and image path; this does not look up
accounts, grant admins, or talk to production.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sabermetrics.account_playmats import AccountPlaymatRepo


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Attach Night Ritual to one owner account (idempotent)."
    )
    parser.add_argument("--owner-id", required=True, help="Owning user id")
    parser.add_argument(
        "--image-path",
        required=True,
        type=Path,
        help="Local Night Ritual image (PNG, JPEG, or WebP)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("data/sabermetrics.db"),
    )
    parser.add_argument(
        "--asset-dir",
        type=Path,
        default=None,
        help="Deck Lab asset directory (defaults beside the database)",
    )
    args = parser.parse_args()
    db_path = args.db_path
    asset_dir = args.asset_dir or (db_path.parent / "deck-lab-assets")
    result = AccountPlaymatRepo(db_path).seed_night_ritual(
        args.owner_id, args.image_path, asset_dir
    )
    print(
        f"Night Ritual playmat {result['id']} for owner {result['owner_id']}; "
        f"migrated {result['migrated']} deck(s)."
    )


if __name__ == "__main__":
    main()
