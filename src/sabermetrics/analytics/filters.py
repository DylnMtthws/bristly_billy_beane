"""Hard-rule filters for candidate card reduction (D4.1).

Deterministic filters that narrow the card pool before scoring.
Each filter is a pure function: cards in → filtered cards out.
"""

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Cached banned list (Commander format)
_BANNED_CARDS: set[str] | None = None

# Canonical candidate source: one row per card NAME (Commander singleton is by
# English name), choosing the cheapest legal printing. NULL-priced printings
# rank last but are kept if a name has no priced printing. This makes "one
# candidate per card" a property of the source rather than a Python filter
# callers must remember — mirrors the old filter_singleton_legal semantics in
# SQL. The `cards` table keeps every printing; only this view collapses them.
CANDIDATE_VIEW_SQL = """
CREATE VIEW IF NOT EXISTS card_candidates AS
WITH latest AS (
    SELECT MAX(snapshot_date) AS d FROM card_prices
),
latest_prices AS (
    SELECT cp.card_id, cp.price_usd
    FROM card_prices cp, latest
    WHERE cp.snapshot_date = latest.d
),
ranked AS (
    SELECT
        c.*,
        lp.price_usd AS price_usd,
        ROW_NUMBER() OVER (
            PARTITION BY c.name
            ORDER BY (lp.price_usd IS NULL) ASC, lp.price_usd ASC, c.id ASC
        ) AS _rn
    FROM cards c
    LEFT JOIN latest_prices lp ON lp.card_id = c.id
    WHERE c.is_legal_in_99 = 1
)
SELECT * FROM ranked WHERE _rn = 1
"""


def ensure_candidate_view(conn: sqlite3.Connection) -> None:
    """Create the canonical `card_candidates` view if it does not exist.

    Idempotent and additive (metadata only — no card rows are touched). Safe to
    call on every query; after first creation it is a no-op.
    """
    conn.execute(CANDIDATE_VIEW_SQL)
    conn.commit()


def _load_banned_cards(db_path: Path) -> set[str]:
    """Load cards banned in Commander from the database."""
    global _BANNED_CARDS
    if _BANNED_CARDS is not None:
        return _BANNED_CARDS

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(
            "SELECT name FROM cards WHERE is_legal_in_99 = 0 "
            "AND is_legal_commander = 0"
        )
        _BANNED_CARDS = {row[0] for row in cursor}
    finally:
        conn.close()
    return _BANNED_CARDS


def filter_by_color_identity(
    rows: list[dict], commander_colors: list[str]
) -> list[dict]:
    """Keep only cards whose color identity is a subset of the commander's.

    Args:
        rows: Card dicts with 'color_identity' as JSON string or list.
        commander_colors: Commander's color identity (e.g. ['B', 'R', 'G']).

    Returns:
        Filtered list of card dicts.
    """
    commander_set = set(commander_colors)
    result = []
    for row in rows:
        ci = row.get("color_identity", "[]")
        if isinstance(ci, str):
            ci = json.loads(ci)
        if set(ci) <= commander_set:
            result.append(row)
    return result


def filter_by_legality(
    rows: list[dict], format_key: str = "commander"
) -> list[dict]:
    """Keep only cards legal in the specified format.

    Args:
        rows: Card dicts with 'is_legal_in_99' field.
        format_key: Format name (only 'commander' supported).

    Returns:
        Filtered list of card dicts.
    """
    return [r for r in rows if r.get("is_legal_in_99", False)]


def filter_by_budget(rows: list[dict], max_price: float) -> list[dict]:
    """Remove cards that exceed the per-card budget threshold.

    Uses a per-card ceiling of max_price / 10 (no single card should
    consume more than 10% of total budget). Cards without prices are
    treated as floor-priced ($0.05).

    Args:
        rows: Card dicts; price looked up from 'price_usd' key.
        max_price: Total deck budget in USD.

    Returns:
        Filtered list of card dicts.
    """
    from sabermetrics.analytics.cvar import PRICE_FLOOR_USD

    per_card_ceiling = max_price / 10.0
    result = []
    for row in rows:
        price = row.get("price_usd")
        if price is None:
            price = PRICE_FLOOR_USD
        if float(price) <= per_card_ceiling:
            result.append(row)
    return result


def filter_singleton_legal(rows: list[dict]) -> list[dict]:
    """Remove cards not legal as singleton (basic lands excepted).

    In Commander, each card can only appear once except basic lands.
    This filter removes duplicates by name, keeping the cheapest printing.

    When a card has multiple printings, some with NULL prices:
    - If ANY printing has a real price, the cheapest priced printing is kept
      and its price is propagated so budget filtering works correctly.
    - If ALL printings have NULL prices, the card is kept with NULL price
      (downstream treats as $0.05, which is conservative).

    Args:
        rows: Card dicts with 'name' field.

    Returns:
        Deduplicated list of card dicts.
    """
    basic_lands = {"Plains", "Island", "Swamp", "Mountain", "Forest",
                   "Wastes", "Snow-Covered Plains", "Snow-Covered Island",
                   "Snow-Covered Swamp", "Snow-Covered Mountain",
                   "Snow-Covered Forest"}

    # Group by name, collecting the cheapest known price per card name
    cheapest_price: dict[str, float | None] = {}
    for row in rows:
        name = row.get("name", "")
        if name in basic_lands:
            continue
        price = row.get("price_usd")
        if price is not None:
            existing = cheapest_price.get(name)
            if existing is None or price < existing:
                cheapest_price[name] = price

    # Sort: prefer printings with real prices (cheapest first), NULLs last
    rows = sorted(rows, key=lambda r: r.get("price_usd") or float("inf"))

    seen: set[str] = set()
    result = []
    for row in rows:
        name = row.get("name", "")
        if name in basic_lands:
            result.append(row)
            continue
        if name not in seen:
            seen.add(name)
            # Propagate the cheapest known price to whichever printing we keep.
            # This ensures that if we keep a NULL-priced printing (because
            # it sorted first for some reason), it still gets the real price.
            known_price = cheapest_price.get(name)
            if row.get("price_usd") is None and known_price is not None:
                row["price_usd"] = known_price
            result.append(row)
    return result


def filter_by_banned_list(
    rows: list[dict], db_path: Path | None = None
) -> list[dict]:
    """Remove banned cards.

    Args:
        rows: Card dicts with 'is_legal_in_99' field.
        db_path: Optional path to DB for loading banned list.

    Returns:
        Filtered list of card dicts.
    """
    return [r for r in rows if r.get("is_legal_in_99", False)]


def apply_hard_filters(
    db_path: Path,
    commander_id: str,
    max_budget_usd: float | None = None,
) -> list[dict]:
    """Apply all hard-rule filters to produce candidate card pool.

    Queries the DB for all cards, then applies filters in order:
    1. Color identity
    2. Legality
    3. Singleton
    4. Budget (if specified)

    Args:
        db_path: Path to the SQLite database.
        commander_id: Scryfall ID of the commander card.
        max_budget_usd: Optional total deck budget.

    Returns:
        List of card dicts that pass all filters.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Get commander data
        cursor = conn.execute(
            "SELECT * FROM cards WHERE id = ?", (commander_id,)
        )
        cmdr_row = cursor.fetchone()
        if cmdr_row is None:
            raise ValueError(f"Commander not found: {commander_id}")
        cmdr = dict(cmdr_row)
        cmdr_colors = json.loads(cmdr["color_identity"])

        # Read from the canonical candidate view: exactly one row per card name
        # (cheapest legal printing). Deduplication is guaranteed by the source,
        # so no Python singleton pass is needed downstream.
        ensure_candidate_view(conn)
        cursor = conn.execute("SELECT * FROM card_candidates")
        all_cards = [dict(row) for row in cursor]
    finally:
        conn.close()

    logger.info("Starting with %d canonical candidates", len(all_cards))

    # Exclude the commander itself from the 99. Match by NAME, not printing id:
    # the canonical view keeps one (cheapest) printing per name, which may be a
    # different id than the requested commander printing, so an id comparison
    # would let the commander slip into its own candidate pool.
    cmdr_name = cmdr.get("name")
    all_cards = [c for c in all_cards if c.get("name") != cmdr_name]

    # Apply filters in sequence (singleton is already guaranteed by the view)
    filtered = filter_by_color_identity(all_cards, cmdr_colors)
    logger.info("After color identity filter: %d", len(filtered))

    if max_budget_usd is not None:
        filtered = filter_by_budget(filtered, max_budget_usd)
        logger.info("After budget filter ($%.0f): %d", max_budget_usd, len(filtered))

    return filtered
