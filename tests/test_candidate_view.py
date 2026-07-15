"""Canonical candidate source (Option A DoD criterion 2b).

Hermetic tests on a synthetic DB (real schema, synthetic rows) — they never
touch the production database. Verifies the `card_candidates` view returns one
row per name at the cheapest legal printing, and that apply_hard_filters (which
now reads the view) cannot emit duplicate card names.
"""

import sqlite3
from pathlib import Path

import pytest

from sabermetrics.analytics.filters import (
    apply_hard_filters,
    ensure_candidate_view,
)

_CARDS_DDL = """
CREATE TABLE cards (
    id TEXT PRIMARY KEY, oracle_id TEXT, name TEXT, mana_cost TEXT, cmc REAL,
    type_line TEXT, oracle_text TEXT, color_identity TEXT, keywords TEXT,
    is_legal_commander BOOLEAN, is_legal_in_99 BOOLEAN, set_code TEXT,
    rarity TEXT, image_uri TEXT, last_updated TIMESTAMP,
    role_tags TEXT, functional_categories TEXT
)
"""
_PRICES_DDL = """
CREATE TABLE card_prices (
    card_id TEXT, price_usd REAL, price_usd_foil REAL, snapshot_date DATE,
    source TEXT, PRIMARY KEY (card_id, snapshot_date)
)
"""
_SNAP = "2026-01-01"


def _card(conn, cid, name, *, ci="[]", legal99=1, cmdr=0, type_line="Artifact"):
    conn.execute(
        "INSERT INTO cards (id, oracle_id, name, cmc, type_line, color_identity, "
        "keywords, is_legal_commander, is_legal_in_99, set_code, rarity) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid, f"o-{name}", name, 1.0, type_line, ci, "[]", cmdr, legal99,
         "tst", "rare"),
    )


def _price(conn, cid, usd):
    conn.execute(
        "INSERT INTO card_prices (card_id, price_usd, snapshot_date) VALUES (?,?,?)",
        (cid, usd, _SNAP),
    )


@pytest.fixture()
def db(tmp_path) -> Path:
    p = tmp_path / "syn.db"
    conn = sqlite3.connect(str(p))
    conn.execute(_CARDS_DDL)
    conn.execute(_PRICES_DDL)
    # Sol Ring: 3 printings, prices 5.00 / 1.00 / (none) -> cheapest 1.00
    _card(conn, "sr1", "Sol Ring")
    _price(conn, "sr1", 5.00)
    _card(conn, "sr2", "Sol Ring")
    _price(conn, "sr2", 1.00)
    _card(conn, "sr3", "Sol Ring")  # no price row -> NULL
    # Rare Gem: single printing, no price -> kept with NULL
    _card(conn, "rg1", "Rare Gem")
    # Plains: 2 basic printings -> collapses to one candidate
    _card(conn, "pl1", "Plains", type_line="Basic Land — Plains")
    _price(conn, "pl1", 0.10)
    _card(conn, "pl2", "Plains", type_line="Basic Land — Plains")
    _price(conn, "pl2", 0.15)
    # Banned: illegal in 99 -> excluded from the view
    _card(conn, "bn1", "Banned", legal99=0)
    _price(conn, "bn1", 0.50)
    conn.commit()
    conn.close()
    return p


def test_view_one_row_per_name_cheapest_printing(db) -> None:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    ensure_candidate_view(conn)
    all_rows = list(conn.execute("SELECT * FROM card_candidates"))
    dupes = conn.execute(
        "SELECT name FROM card_candidates GROUP BY name HAVING COUNT(*) > 1"
    ).fetchall()
    rows = {r["name"]: r for r in all_rows}
    conn.close()

    # exactly one row per legal name; Banned excluded
    assert len(all_rows) == 3 and not dupes
    assert set(rows) == {"Sol Ring", "Rare Gem", "Plains"}
    # cheapest priced printing wins; NULLs rank last
    assert rows["Sol Ring"]["price_usd"] == 1.00
    assert rows["Sol Ring"]["id"] == "sr2"
    # a name with only NULL prices is kept, price NULL
    assert rows["Rare Gem"]["price_usd"] is None
    # basics collapse to a single candidate row too
    assert rows["Plains"]["price_usd"] == 0.10


def test_view_matches_min_price_for_every_name(db) -> None:
    conn = sqlite3.connect(str(db))
    ensure_candidate_view(conn)
    q = """
    SELECT cc.name, cc.price_usd,
      (SELECT MIN(cp.price_usd) FROM cards c2 JOIN card_prices cp ON cp.card_id=c2.id
         WHERE c2.name=cc.name) AS true_min
    FROM card_candidates cc
    """
    for name, kept, true_min in conn.execute(q):
        if true_min is None:
            assert kept is None, name
        else:
            assert kept == true_min, name
    conn.close()


def test_apply_hard_filters_emits_no_duplicate_names(db) -> None:
    # Add a green commander and multi-printing green cards.
    conn = sqlite3.connect(str(db))
    _card(conn, "cmd", "Green Cmdr", ci='["G"]', cmdr=1, type_line="Legendary Creature")
    for i in range(3):
        _card(conn, f"gc-a{i}", "Green A", ci='["G"]', type_line="Creature")
        _price(conn, f"gc-a{i}", 1.0 + i)
    _card(conn, "gc-b", "Green B", ci='["G"]', type_line="Creature")
    _price(conn, "gc-b", 2.0)
    conn.commit()
    conn.close()

    candidates = apply_hard_filters(db, "cmd", max_budget_usd=1000.0)
    names = [c["name"] for c in candidates]
    assert len(names) == len(set(names)), f"duplicate candidate names: {names}"
    assert "Green A" in names and "Green B" in names
    assert "Green Cmdr" not in names  # commander excluded from the 99
