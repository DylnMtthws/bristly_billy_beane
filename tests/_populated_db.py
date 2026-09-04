"""Whether the local database actually has data in it.

Five test modules previously gated on ``Path("data/sabermetrics.db").exists()``.
That was fine while the only way to have the file was to have populated it —
and wrong the moment a fresh deployment ran ``setup_db``, which creates every
table and no rows. The gate passed, the tests ran against an empty corpus, and
seven of them failed for a reason unrelated to any change.

Existence is not readiness. This checks for the rows the tests need.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

PROD_DB = Path("data/sabermetrics.db")

#: Minimum cards before the corpus-dependent tests mean anything. A handful of
#: rows would satisfy "not empty" while still failing every assertion about
#: commanders, prices or clustering.
MIN_CARDS = 1000


def has_populated_db(path: Path = PROD_DB, *, min_cards: int = MIN_CARDS) -> bool:
    """Return True if ``path`` exists and holds a usable card corpus."""
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        return conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] >= min_cards
    except sqlite3.Error:
        return False
    finally:
        conn.close()


#: Evaluated once at import, matching how the gates are written.
HAS_POPULATED_DB = has_populated_db()

SKIP_REASON = (
    "needs a populated card corpus in data/sabermetrics.db "
    "(run: sabermetrics sync --source scryfall)"
)
