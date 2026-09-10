"""Materialize the published mtg_v1 corpus for SQLite Research and editing.

Only source facts are refreshed. Account, document, candidate, and feedback
state never participates. Readers see the previous complete snapshot until the
replacement transaction commits. No ingestion service or model is called.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any

from sabermetrics.card_discovery import imported_commander_eligible
from sabermetrics.cedh.adapters_postgres import _query
from sabermetrics.cedh.repositories import assert_v1_only

logger = logging.getLogger(__name__)
REFRESH_SECONDS = 6 * 60 * 60
MIN_EVENT_SIZE = 16

CARD_SQL = """
SELECT c.oracle_id, c.name, c.mana_cost, c.mana_value, c.type_line,
       c.oracle_text, c.color_identity, c.keywords, c.rep_scryfall_card_id,
       c.rep_set_code, c.rep_rarity, c.content_updated_at, c.power, c.toughness,
       l.status AS commander_legality
FROM mtg_v1.card_any_medium c
LEFT JOIN mtg_v1.card_legality l ON l.oracle_id=c.oracle_id AND l.format='commander'
"""
ENTRY_SQL = """
SELECT e.entry_id, e.tournament_id, e.deck_id, e.standing, e.wins, e.losses,
       e.draws, t.event_date, dc.oracle_id AS commander_oracle_id, dc.position
FROM mtg_v1.tournament_entry e
JOIN mtg_v1.tournament t ON t.tournament_id=e.tournament_id
JOIN mtg_v1.deck_commander dc ON dc.deck_id=e.deck_id
WHERE t.player_count >= %(min_size)s AND dc.oracle_id IS NOT NULL
ORDER BY e.entry_id, dc.position
"""
DECK_CARD_SQL = """
SELECT dc.deck_id, dc.oracle_id, SUM(dc.quantity) AS quantity,
       BOOL_OR(dc.board='commander') AS is_commander
FROM mtg_v1.deck_card dc
WHERE dc.oracle_id IS NOT NULL AND dc.board IN ('mainboard','commander')
  AND EXISTS (
    SELECT 1 FROM mtg_v1.tournament_entry e
    JOIN mtg_v1.tournament t ON t.tournament_id=e.tournament_id
    WHERE e.deck_id=dc.deck_id AND t.player_count >= %(min_size)s
  )
GROUP BY dc.deck_id, dc.oracle_id
"""


def apply_snapshot(
    db_path: Path,
    cards: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    deck_cards: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Apply a complete source snapshot atomically, retaining existing card IDs."""
    if not cards:
        raise ValueError("Published card corpus is empty; previous snapshot retained")
    now = datetime.now(UTC).isoformat()
    with closing(sqlite3.connect(db_path, timeout=30)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        card_ids = {
            str(oracle): str(card_id)
            for oracle, card_id in conn.execute(
                "SELECT oracle_id,MIN(id) FROM cards GROUP BY oracle_id"
            )
        }
        values = []
        for card in cards:
            oracle = str(card["oracle_id"])
            card_id = card_ids.setdefault(oracle, oracle)
            type_line = card.get("type_line") or ""
            oracle_text = card.get("oracle_text") or ""
            legal = card.get("commander_legality") == "legal"
            commander = legal and imported_commander_eligible(
                type_line, oracle_text, card.get("power"), card.get("toughness")
            )
            printing = str(card.get("rep_scryfall_card_id") or "")
            image = (
                f"https://cards.scryfall.io/normal/front/{printing[0]}/{printing[1]}/{printing}.jpg"
                if len(printing) == 36
                else None
            )
            values.append(
                (
                    card_id,
                    oracle,
                    card["name"],
                    card.get("mana_cost"),
                    float(card.get("mana_value") or 0),
                    type_line,
                    oracle_text,
                    json.dumps(card.get("color_identity") or []),
                    json.dumps(card.get("keywords") or []),
                    int(commander),
                    int(legal),
                    card.get("rep_set_code"),
                    card.get("rep_rarity"),
                    image,
                    str(card.get("content_updated_at") or now),
                    card.get("power"),
                    card.get("toughness"),
                )
            )
        conn.executemany(
            """INSERT INTO cards(id,oracle_id,name,mana_cost,cmc,type_line,oracle_text,
                color_identity,keywords,is_legal_commander,is_legal_in_99,set_code,
                rarity,image_uri,last_updated,power,toughness) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,mana_cost=excluded.mana_cost,cmc=excluded.cmc,
                type_line=excluded.type_line,oracle_text=excluded.oracle_text,
                color_identity=excluded.color_identity,keywords=excluded.keywords,
                is_legal_commander=excluded.is_legal_commander,
                is_legal_in_99=excluded.is_legal_in_99,set_code=excluded.set_code,
                rarity=excluded.rarity,image_uri=excluded.image_uri,
                last_updated=excluded.last_updated,power=excluded.power,toughness=excluded.toughness""",
            values,
        )
        # Only our namespaced source records are replaced. Documents store their
        # own card facts and never refer to these source deck rows.
        conn.execute("DELETE FROM tournament_results WHERE source_entry_id IS NOT NULL")
        conn.execute(
            "DELETE FROM deck_cards WHERE deck_id IN "
            "(SELECT id FROM decks WHERE source='mtg_v1')"
        )
        decks: dict[str, str] = {}
        result_rows = []
        for entry in entries:
            oracle = str(entry["commander_oracle_id"])
            if oracle not in card_ids:
                raise ValueError("Tournament commander is absent from the card corpus")
            source_deck = str(entry["deck_id"])
            deck_id = "mtg_v1:" + source_deck
            decks.setdefault(source_deck, card_ids[oracle])
            entry_id = "mtg_v1:" + str(entry["entry_id"])
            event_date = entry["event_date"]
            if isinstance(event_date, datetime):
                event_date = event_date.astimezone(UTC).date()
            games = sum(int(entry.get(k) or 0) for k in ("wins", "losses", "draws"))
            result_rows.append(
                (
                    entry_id + ":" + oracle,
                    str(entry["tournament_id"]),
                    deck_id,
                    card_ids[oracle],
                    entry.get("standing"),
                    str(event_date)[:10],
                    entry_id,
                    games,
                    entry.get("wins"),
                ),
            )
        conn.executemany(
            """INSERT INTO decks(id,source,source_id,commander_id)
               VALUES(?,'mtg_v1',?,?) ON CONFLICT(id) DO UPDATE SET
               commander_id=excluded.commander_id,fetched_at=CURRENT_TIMESTAMP""",
            [("mtg_v1:" + key, key, commander) for key, commander in decks.items()],
        )
        conn.executemany(
            """INSERT INTO tournament_results(id,tournament_id,deck_id,commander_id,
               standing,tournament_date,source_entry_id,games_played,games_won)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            result_rows,
        )
        card_rows = []
        card_count = 0
        commander_pairs = {
            (str(e["deck_id"]), str(e["commander_oracle_id"])) for e in entries
        }
        for card in deck_cards:
            source_deck, oracle = str(card["deck_id"]), str(card["oracle_id"])
            if source_deck not in decks:
                continue
            if oracle not in card_ids:
                raise ValueError("Resolved deck card is absent from the card corpus")
            card_rows.append(
                (
                    "mtg_v1:" + source_deck,
                    card_ids[oracle],
                    int(card["quantity"]),
                    int(
                        bool(card["is_commander"])
                        or (source_deck, oracle) in commander_pairs
                    ),
                )
            )
            card_count += 1
            if len(card_rows) >= 2000:
                conn.executemany(
                    "INSERT INTO deck_cards(deck_id,card_id,quantity,is_commander) VALUES(?,?,?,?)",
                    card_rows,
                )
                card_rows.clear()
        conn.executemany(
            "INSERT INTO deck_cards(deck_id,card_id,quantity,is_commander) VALUES(?,?,?,?)",
            card_rows,
        )
        from sabermetrics.research_identities import refresh_identities

        refresh_identities(conn)
        dates = [row[5] for row in result_rows]
        state = {
            "refreshed_at": now,
            "cards": len(cards),
            "decks": len(decks),
            "entries": len({row[6] for row in result_rows}),
            "deck_cards": card_count,
            "min_event_size": MIN_EVENT_SIZE,
            "latest_event": max(dates) if dates else None,
        }
        conn.execute(
            "INSERT INTO research_source_state(id,state_json) VALUES(1,?) "
            "ON CONFLICT(id) DO UPDATE SET state_json=excluded.state_json",
            (json.dumps(state),),
        )
    return state


def source_state(db_path: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(db_path)) as conn, conn:
        row = conn.execute(
            "SELECT state_json FROM research_source_state WHERE id=1"
        ).fetchone()
    return json.loads(row[0]) if row else {}


def refresh(db_path: Path, *, force: bool = False) -> dict[str, Any]:
    previous = source_state(db_path)
    if not force and previous:
        age = datetime.now(UTC) - datetime.fromisoformat(previous["refreshed_at"])
        if age < timedelta(seconds=REFRESH_SECONDS):
            return previous
    import psycopg
    from psycopg.rows import dict_row

    # Never log DSNs or provider errors containing connection details.
    with psycopg.connect(
        os.environ["MTG_V1_DSN"], row_factory=dict_row, connect_timeout=15
    ) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='90s'")
        cards = _query(conn, CARD_SQL)
        entries = _query(conn, ENTRY_SQL, {"min_size": MIN_EVENT_SIZE})
        assert_v1_only(DECK_CARD_SQL)
        with (
            conn.cursor(name="research_deck_cards") as cursor,
            TemporaryFile(mode="w+t") as spool,
        ):
            cursor.itersize = 2000
            cursor.execute(DECK_CARD_SQL, {"min_size": MIN_EVENT_SIZE})
            # Finish network I/O before taking the SQLite write lock. Spooling
            # bounds RAM independently of the number of tournament deck cards.
            for row in cursor:
                spool.write(json.dumps(row, default=str) + "\n")
            spool.seek(0)
            return apply_snapshot(
                db_path, cards, entries, (json.loads(line) for line in spool)
            )


def start_refresh_worker(db_path: Path) -> None:
    """Refresh before serving initially, then every six hours in one worker."""
    try:
        refresh(db_path)
    except Exception as exc:
        if not source_state(db_path):
            raise RuntimeError("Initial Research corpus refresh failed") from None
        logger.warning(
            "Research refresh failed (%s); retaining snapshot", type(exc).__name__
        )

    def work() -> None:
        delay = threading.Event()
        while not delay.wait(REFRESH_SECONDS):
            try:
                refresh(db_path)
            except Exception as exc:
                logger.warning(
                    "Research refresh failed (%s); retaining snapshot",
                    type(exc).__name__,
                )

    threading.Thread(target=work, name="research-refresh", daemon=True).start()
