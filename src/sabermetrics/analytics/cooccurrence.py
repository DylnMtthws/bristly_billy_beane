"""Co-occurrence matrix builder (D4.2).

Builds a sparse co-occurrence matrix from decks + deck_cards.
Run weekly after decklist sync. Populates card_cooccurrence table.
"""

import logging
import sqlite3
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


def build_cooccurrence(db_path: Path, min_decks: int = 3) -> int:
    """Build co-occurrence matrix from decklists grouped by commander.

    For each commander, counts how often pairs of cards appear together
    in tracked decks. Stores results in card_cooccurrence table.

    Args:
        db_path: Path to the SQLite database.
        min_decks: Minimum decks a commander must have to process.

    Returns:
        Number of co-occurrence pairs stored.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = OFF")

    try:
        # Get commanders with enough decks
        cursor = conn.execute(
            "SELECT commander_id, COUNT(*) as deck_count "
            "FROM decks "
            "GROUP BY commander_id "
            "HAVING deck_count >= ?",
            (min_decks,),
        )
        commanders = cursor.fetchall()

        if not commanders:
            logger.info("No commanders with >= %d decks found", min_decks)
            return 0

        logger.info(
            "Building co-occurrence for %d commanders", len(commanders)
        )

        total_pairs = 0

        for cmdr_id, deck_count in commanders:
            # Get all decks for this commander
            cursor = conn.execute(
                "SELECT id FROM decks WHERE commander_id = ?", (cmdr_id,)
            )
            deck_ids = [row[0] for row in cursor]

            # For each deck, get the card list
            deck_cards: list[list[str]] = []
            for deck_id in deck_ids:
                cursor = conn.execute(
                    "SELECT card_id FROM deck_cards WHERE deck_id = ?",
                    (deck_id,),
                )
                cards = sorted(row[0] for row in cursor)
                if cards:
                    deck_cards.append(cards)

            if len(deck_cards) < min_decks:
                continue

            # Count pair co-occurrences
            pair_counts: dict[tuple[str, str], int] = defaultdict(int)
            for cards in deck_cards:
                for i, card_a in enumerate(cards):
                    for card_b in cards[i + 1:]:
                        pair_counts[(card_a, card_b)] += 1

            # Store results
            n_decks = len(deck_cards)
            batch = []
            for (card_a, card_b), count in pair_counts.items():
                rate = count / n_decks
                batch.append((card_a, card_b, cmdr_id, count, rate))

            if batch:
                conn.execute(
                    "DELETE FROM card_cooccurrence WHERE commander_id = ?",
                    (cmdr_id,),
                )
                conn.executemany(
                    "INSERT INTO card_cooccurrence "
                    "(card_a_id, card_b_id, commander_id, "
                    "cooccurrence_count, cooccurrence_rate) "
                    "VALUES (?, ?, ?, ?, ?)",
                    batch,
                )
                total_pairs += len(batch)

            logger.info(
                "Commander %s: %d pairs from %d decks",
                cmdr_id, len(batch), n_decks,
            )

        conn.commit()
        logger.info("Total co-occurrence pairs stored: %d", total_pairs)
        return total_pairs

    finally:
        conn.close()


def get_cooccurrence(
    db_path: Path,
    commander_id: str,
    card_id: str,
    top_k: int = 20,
) -> list[dict]:
    """Get top co-occurring cards for a given card under a commander.

    Args:
        db_path: Path to the SQLite database.
        commander_id: Scryfall ID of the commander.
        card_id: Scryfall ID of the card to find partners for.
        top_k: Number of results to return.

    Returns:
        List of dicts with card_id, cooccurrence_count, cooccurrence_rate.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT card_b_id AS partner_id, "
            "cooccurrence_count, cooccurrence_rate "
            "FROM card_cooccurrence "
            "WHERE commander_id = ? AND card_a_id = ? "
            "UNION ALL "
            "SELECT card_a_id AS partner_id, "
            "cooccurrence_count, cooccurrence_rate "
            "FROM card_cooccurrence "
            "WHERE commander_id = ? AND card_b_id = ? "
            "ORDER BY cooccurrence_rate DESC "
            "LIMIT ?",
            (commander_id, card_id, commander_id, card_id, top_k),
        )
        return [dict(row) for row in cursor]
    finally:
        conn.close()
