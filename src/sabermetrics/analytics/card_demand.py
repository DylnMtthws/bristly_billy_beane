"""Card demand index computation (D4.5).

Computes price * inclusion_rate as demand proxy,
per edhpowerlevel methodology.
"""

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def compute_card_demand(db_path: Path) -> list[dict]:
    """Compute card demand index for all cards with price and inclusion data.

    Demand = price_usd * inclusion_rate, where inclusion_rate is
    the fraction of tracked decks that include this card.

    Args:
        db_path: Path to the SQLite database.

    Returns:
        List of dicts with card_id, name, price, inclusion_rate, demand_index,
        sorted by demand_index descending.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        # Total number of tracked decks
        cursor = conn.execute("SELECT COUNT(*) FROM decks")
        total_decks = cursor.fetchone()[0]

        if total_decks == 0:
            logger.info("No decks tracked; using EDHREC data as fallback")
            return _compute_demand_from_edhrec(conn)

        # Count how many decks each card appears in
        cursor = conn.execute(
            "SELECT dc.card_id, c.name, COUNT(DISTINCT dc.deck_id) as deck_count, "
            "cp.price_usd "
            "FROM deck_cards dc "
            "JOIN cards c ON dc.card_id = c.id "
            "LEFT JOIN card_prices cp ON dc.card_id = cp.card_id "
            "AND cp.snapshot_date = (SELECT MAX(snapshot_date) FROM card_prices) "
            "GROUP BY dc.card_id "
            "HAVING cp.price_usd IS NOT NULL"
        )

        results = []
        for row in cursor:
            inclusion_rate = row["deck_count"] / total_decks
            price = float(row["price_usd"])
            demand = price * inclusion_rate

            results.append({
                "card_id": row["card_id"],
                "name": row["name"],
                "price_usd": price,
                "inclusion_rate": round(inclusion_rate, 4),
                "demand_index": round(demand, 4),
            })

        results.sort(key=lambda x: x["demand_index"], reverse=True)
        logger.info("Computed demand index for %d cards", len(results))
        return results

    finally:
        conn.close()


def _compute_demand_from_edhrec(conn: sqlite3.Connection) -> list[dict]:
    """Fallback: estimate demand from EDHREC top_cards data.

    Uses the top_cards JSON from edhrec_commander_data to estimate
    inclusion rates across commanders.
    """
    cursor = conn.execute(
        "SELECT commander_id, top_cards, deck_count FROM edhrec_commander_data"
    )
    rows = cursor.fetchall()

    if not rows:
        return []

    # Aggregate card appearances across all commanders
    card_appearances: dict[str, int] = {}
    total_weighted_decks = 0

    for row in rows:
        top_cards_json = row["top_cards"] or "[]"
        deck_count = row["deck_count"] or 0
        total_weighted_decks += deck_count

        top_cards = json.loads(top_cards_json) if isinstance(top_cards_json, str) else top_cards_json
        for tc in top_cards:
            name = tc.get("card_name") or tc.get("name", "")
            if name:
                card_appearances[name] = card_appearances.get(name, 0) + deck_count

    if total_weighted_decks == 0:
        return []

    # Look up prices for these cards
    results = []
    for name, appearances in card_appearances.items():
        cursor2 = conn.execute(
            "SELECT c.id, cp.price_usd FROM cards c "
            "LEFT JOIN card_prices cp ON c.id = cp.card_id "
            "AND cp.snapshot_date = (SELECT MAX(snapshot_date) FROM card_prices) "
            "WHERE c.name = ? LIMIT 1",
            (name,),
        )
        price_row = cursor2.fetchone()
        if price_row and price_row[1]:
            inclusion_rate = appearances / total_weighted_decks
            price = float(price_row[1])
            results.append({
                "card_id": price_row[0],
                "name": name,
                "price_usd": price,
                "inclusion_rate": round(inclusion_rate, 4),
                "demand_index": round(price * inclusion_rate, 4),
            })

    results.sort(key=lambda x: x["demand_index"], reverse=True)
    logger.info("Computed demand index for %d cards (EDHREC fallback)", len(results))
    return results
