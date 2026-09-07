"""Exact commander cohorts layered over legacy per-card source records."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from sabermetrics.commander_pairs import pair_id


def refresh_identities(conn: sqlite3.Connection) -> None:
    """Backfill existing snapshots and refresh new ones in the caller's transaction.

    The source importer retains one row per commander for legacy consumers.
    Research groups those rows into one entry for the complete commander set.
    No card, user document, or historical source record is removed.
    """
    members: dict[str, set[str]] = defaultdict(set)
    for deck_id, card_id in conn.execute(
        "SELECT DISTINCT deck_id,commander_id FROM tournament_results "
        "WHERE source_entry_id IS NOT NULL AND deck_id IS NOT NULL"
    ):
        members[deck_id].add(card_id)
    cards = {
        row[0]: dict(
            zip(
                (
                    "id",
                    "oracle_id",
                    "name",
                    "type_line",
                    "mana_cost",
                    "cmc",
                    "oracle_text",
                    "color_identity",
                    "image_uri",
                ),
                row,
            )
        )
        for row in conn.execute(
            "SELECT id,oracle_id,name,type_line,mana_cost,cmc,oracle_text,color_identity,image_uri FROM cards"
        )
    }
    for deck_id, ids in members.items():
        if len(ids) == 1:
            identity = next(iter(ids))
        else:
            if len(ids) != 2:
                raise ValueError("A recorded deck must have one or two commanders")
            pair = sorted((cards[key] for key in ids), key=lambda c: c["name"])
            identity = pair_id([str(c["oracle_id"]) for c in pair])
            colors = {
                color for c in pair for color in json.loads(c["color_identity"] or "[]")
            }
            conn.execute(
                "INSERT INTO research_commander_pairs(id,name,color_identity,cmc,card_ids) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,color_identity=excluded.color_identity,cmc=excluded.cmc,card_ids=excluded.card_ids",
                (
                    identity,
                    " + ".join(c["name"] for c in pair),
                    json.dumps([c for c in "WUBRG" if c in colors]),
                    sum(c["cmc"] or 0 for c in pair),
                    json.dumps([c["id"] for c in pair]),
                ),
            )
        conn.execute(
            "UPDATE tournament_results SET commander_identity_id=? WHERE deck_id=? AND source_entry_id IS NOT NULL",
            (identity, deck_id),
        )


def ensure_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tournament_results)")}
    migrate = "commander_identity_id" not in columns
    if migrate:
        conn.execute(
            "ALTER TABLE tournament_results ADD COLUMN commander_identity_id TEXT"
        )
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS research_commander_pairs (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, color_identity TEXT NOT NULL,
            cmc REAL NOT NULL, card_ids TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS favorite_commander_pairs (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            commander_id TEXT NOT NULL REFERENCES research_commander_pairs(id),
            created_at TEXT NOT NULL, PRIMARY KEY(user_id,commander_id)
        );
        CREATE INDEX IF NOT EXISTS idx_tourney_identity ON tournament_results(commander_identity_id);
        CREATE INDEX IF NOT EXISTS idx_tourney_deck ON tournament_results(deck_id);
        CREATE INDEX IF NOT EXISTS idx_tourney_cohort ON tournament_results(COALESCE(commander_identity_id,commander_id));
        CREATE INDEX IF NOT EXISTS idx_tourney_source_entry ON tournament_results(source_entry_id,id);
        CREATE VIEW IF NOT EXISTS research_results AS
            SELECT id,tournament_id,deck_id,CAST(COALESCE(commander_identity_id,commander_id) AS TEXT) AS commander_id,
                   standing,tournament_date,source_entry_id,player_name
            FROM tournament_results t
            WHERE source_entry_id IS NULL OR id=(
                SELECT MIN(r.id) FROM tournament_results r WHERE r.source_entry_id=t.source_entry_id
            );
        CREATE VIEW IF NOT EXISTS research_commanders AS
            SELECT c.id,c.oracle_id,c.name,c.type_line,c.mana_cost,c.cmc,c.oracle_text,c.color_identity,c.image_uri,
                   json_array(c.id) AS card_ids
            FROM commander_candidates c
            WHERE NOT EXISTS (SELECT 1 FROM research_commander_pairs p,json_each(p.card_ids) m WHERE m.value=c.id)
               OR EXISTS (SELECT 1 FROM tournament_results r WHERE COALESCE(r.commander_identity_id,r.commander_id)=c.id)
            UNION ALL
            SELECT p.id,NULL,p.name,'Commander pair',NULL,p.cmc,NULL,p.color_identity,NULL,p.card_ids
            FROM research_commander_pairs p
            WHERE EXISTS (SELECT 1 FROM tournament_results r WHERE r.commander_identity_id=p.id);
    """)
    if (
        migrate
        or conn.execute(
            "SELECT 1 FROM tournament_results WHERE source_entry_id IS NOT NULL AND commander_identity_id IS NULL LIMIT 1"
        ).fetchone()
    ):
        with conn:
            refresh_identities(conn)


def attach_members(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    ids = json.loads(row.pop("card_ids"))
    row["commanders"] = [
        dict(
            zip(
                ("id", "name", "image_uri", "oracle_text"),
                conn.execute(
                    "SELECT id,name,image_uri,oracle_text FROM cards WHERE id=?", (key,)
                ).fetchone(),
            )
        )
        for key in ids
    ]
    row["commander_card_ids"] = ids
