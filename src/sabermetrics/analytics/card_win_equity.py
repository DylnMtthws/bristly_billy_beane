"""Card Win Equity computation (D4.3).

Computes lift in win rate when a card is present vs absent.
Uses Wilson confidence interval for statistical rigor.
Populates card_win_equity table.
"""

import logging
import math
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def wilson_lower_bound(
    successes: int, total: int, z: float = 1.96
) -> float:
    """Wilson score confidence interval lower bound.

    Args:
        successes: Number of successes (wins).
        total: Total trials (games).
        z: Z-score for confidence level (1.96 = 95%).

    Returns:
        Lower bound of Wilson confidence interval.
    """
    if total == 0:
        return 0.0

    p_hat = successes / total
    denominator = 1 + z * z / total
    centre = p_hat + z * z / (2 * total)
    spread = z * math.sqrt((p_hat * (1 - p_hat) + z * z / (4 * total)) / total)

    return (centre - spread) / denominator


def compute_card_win_equity(
    db_path: Path, min_sample_size: int = 5
) -> int:
    """Compute Card Win Equity for all cards with sufficient data.

    CWE = win_rate_with_card - win_rate_without_card

    This measures how much a card contributes to winning relative to
    decks that don't include it, per commander.

    Args:
        db_path: Path to the SQLite database.
        min_sample_size: Minimum appearances to compute CWE.

    Returns:
        Number of CWE entries stored.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = OFF")

    try:
        # Get all tournament results with deck mappings
        cursor = conn.execute(
            "SELECT tr.deck_id, tr.games_won, tr.games_played, "
            "tr.commander_id "
            "FROM tournament_results tr "
            "WHERE tr.games_won IS NOT NULL AND tr.games_played IS NOT NULL "
            "AND tr.deck_id IS NOT NULL"
        )
        results = cursor.fetchall()

        if not results:
            logger.info("No tournament results with win/loss data")
            return 0

        # Build per-commander per-deck win rates
        # commander_id -> deck_id -> (wins, games)
        cmdr_decks: dict[str, dict[str, tuple[int, int]]] = {}
        for deck_id, wins, games, cmdr_id in results:
            if cmdr_id not in cmdr_decks:
                cmdr_decks[cmdr_id] = {}
            losses = (games or 0) - (wins or 0)
            cmdr_decks[cmdr_id][deck_id] = (wins or 0, max(0, losses))

        now = datetime.now().isoformat()
        total_entries = 0

        for cmdr_id, decks in cmdr_decks.items():
            # Get card membership for each deck
            deck_ids = list(decks.keys())
            if len(deck_ids) < min_sample_size:
                continue

            # Overall stats for this commander
            total_wins = sum(w for w, l in decks.values())
            total_losses = sum(l for w, l in decks.values())
            total_games = total_wins + total_losses
            if total_games == 0:
                continue
            overall_wr = total_wins / total_games

            # Get all cards in these decks
            placeholders = ",".join("?" for _ in deck_ids)
            cursor = conn.execute(
                f"SELECT card_id, deck_id FROM deck_cards "
                f"WHERE deck_id IN ({placeholders})",
                deck_ids,
            )

            # card_id -> set of deck_ids containing it
            card_decks: dict[str, set[str]] = {}
            for card_id, deck_id in cursor:
                if card_id not in card_decks:
                    card_decks[card_id] = set()
                card_decks[card_id].add(deck_id)

            batch = []
            for card_id, containing_decks in card_decks.items():
                n_with = len(containing_decks)
                if n_with < min_sample_size:
                    continue

                # Win rate when card is present
                wins_with = sum(
                    decks[d][0] for d in containing_decks if d in decks
                )
                games_with = sum(
                    decks[d][0] + decks[d][1]
                    for d in containing_decks if d in decks
                )
                if games_with == 0:
                    continue
                wr_with = wins_with / games_with

                # Win rate when card is absent
                absent_decks = set(deck_ids) - containing_decks
                if not absent_decks:
                    wr_without = overall_wr
                else:
                    wins_without = sum(
                        decks[d][0] for d in absent_decks if d in decks
                    )
                    games_without = sum(
                        decks[d][0] + decks[d][1]
                        for d in absent_decks if d in decks
                    )
                    wr_without = (
                        wins_without / games_without if games_without > 0
                        else overall_wr
                    )

                cwe = wr_with - wr_without
                confidence = wilson_lower_bound(wins_with, games_with)

                batch.append((
                    card_id, cmdr_id, wr_with, wr_without,
                    cwe, n_with, confidence, now,
                ))

            if batch:
                conn.execute(
                    "DELETE FROM card_win_equity WHERE commander_id = ?",
                    (cmdr_id,),
                )
                conn.executemany(
                    "INSERT INTO card_win_equity "
                    "(card_id, commander_id, win_rate_when_present, "
                    "win_rate_when_absent, cwe_score, sample_size, "
                    "confidence, last_computed) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    batch,
                )
                total_entries += len(batch)

            logger.info(
                "Commander %s: %d CWE entries from %d decks",
                cmdr_id, len(batch), len(deck_ids),
            )

        conn.commit()
        logger.info("Total CWE entries stored: %d", total_entries)
        return total_entries

    finally:
        conn.close()
