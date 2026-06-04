"""Tests for the central DB access layer (sabermetrics.db)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from sabermetrics.db import SourceHealthRepo, connect, row_to_card


def _make_db(tmp_path: Path) -> Path:
    """Create a DB with source_health and cards tables."""
    db_path = tmp_path / "t.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE source_health (
            source TEXT PRIMARY KEY,
            last_successful_sync TIMESTAMP,
            last_failed_sync TIMESTAMP,
            last_error TEXT,
            consecutive_failures INTEGER DEFAULT 0
        );
        CREATE TABLE cards (
            id TEXT PRIMARY KEY,
            oracle_id TEXT,
            name TEXT,
            mana_cost TEXT,
            cmc REAL,
            type_line TEXT,
            oracle_text TEXT,
            color_identity TEXT,
            keywords TEXT,
            is_legal_commander BOOLEAN,
            is_legal_in_99 BOOLEAN,
            set_code TEXT,
            rarity TEXT,
            image_uri TEXT,
            last_updated TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()
    return db_path


# --- connect() -----------------------------------------------------------


def test_connect_sets_row_factory(tmp_path: Path) -> None:
    """Default connect() yields Row objects supporting keyed access."""
    db_path = _make_db(tmp_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO source_health (source, consecutive_failures) VALUES (?, ?)",
            ("x", 3),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM source_health").fetchone()
    assert row["source"] == "x"  # keyed access
    assert row[0] == "x"  # positional access still works


def test_connect_row_factory_disabled(tmp_path: Path) -> None:
    """row_factory=False yields plain tuples."""
    db_path = _make_db(tmp_path)
    with connect(db_path, row_factory=False) as conn:
        row = conn.execute("SELECT 1, 2").fetchone()
    assert row == (1, 2)


def test_connect_closes_connection(tmp_path: Path) -> None:
    """The connection is closed once the context exits."""
    db_path = _make_db(tmp_path)
    with connect(db_path) as conn:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_connect_foreign_keys_opt_in(tmp_path: Path) -> None:
    """foreign_keys defaults off, can be enabled."""
    db_path = _make_db(tmp_path)
    with connect(db_path) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    with connect(db_path, foreign_keys=True) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# --- SourceHealthRepo ----------------------------------------------------


def test_record_success_then_last_successful_sync(tmp_path: Path) -> None:
    repo = SourceHealthRepo(_make_db(tmp_path))
    assert repo.last_successful_sync("scryfall") is None
    repo.record("scryfall", success=True)
    ts = repo.last_successful_sync("scryfall")
    assert isinstance(ts, datetime)
    rec = repo.get("scryfall")
    assert rec["consecutive_failures"] == 0
    assert rec["last_error"] is None


def test_record_failure_increments_consecutive(tmp_path: Path) -> None:
    repo = SourceHealthRepo(_make_db(tmp_path))
    repo.record("edhrec", success=False, error="boom")
    repo.record("edhrec", success=False, error="boom again")
    rec = repo.get("edhrec")
    assert rec["consecutive_failures"] == 2
    assert rec["last_error"] == "boom again"
    assert rec["last_successful_sync"] is None
    assert repo.last_successful_sync("edhrec") is None


def test_record_success_resets_failures(tmp_path: Path) -> None:
    repo = SourceHealthRepo(_make_db(tmp_path))
    repo.record("topdeck", success=False, error="boom")
    repo.record("topdeck", success=True)
    rec = repo.get("topdeck")
    assert rec["consecutive_failures"] == 0
    # INSERT OR REPLACE wipes the prior failure fields on success.
    assert rec["last_error"] is None
    assert rec["last_failed_sync"] is None


def test_get_missing_returns_none(tmp_path: Path) -> None:
    repo = SourceHealthRepo(_make_db(tmp_path))
    assert repo.get("nope") is None


def test_get_all_ordered(tmp_path: Path) -> None:
    repo = SourceHealthRepo(_make_db(tmp_path))
    repo.record("zeta", success=True)
    repo.record("alpha", success=True)
    names = [r["source"] for r in repo.get_all()]
    assert names == ["alpha", "zeta"]


# --- row_to_card ---------------------------------------------------------


def _insert_card(db_path: Path, **overrides) -> None:
    fields = {
        "id": "c1",
        "oracle_id": "o1",
        "name": "Test Card",
        "mana_cost": "{1}{G}",
        "cmc": 2.0,
        "type_line": "Creature — Elf",
        "oracle_text": "Some text.",
        "color_identity": json.dumps(["G"]),
        "keywords": json.dumps(["Trample"]),
        "is_legal_commander": 1,
        "is_legal_in_99": 1,
        "set_code": "TST",
        "rarity": "rare",
        "image_uri": None,
        "last_updated": "2024-01-01T00:00:00",
    }
    fields.update(overrides)
    cols = ", ".join(fields)
    placeholders = ", ".join(["?"] * len(fields))
    with connect(db_path) as conn:
        conn.execute(
            f"INSERT INTO cards ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        conn.commit()


def test_row_to_card_parses_json_fields(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_card(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM cards WHERE id = 'c1'").fetchone()
    card = row_to_card(row)
    assert card.color_identity == ["G"]
    assert card.keywords == ["Trample"]
    assert card.is_legal_commander is True
    assert card.cmc == 2.0
    assert card.current_price_usd is None


def test_row_to_card_price_kwarg(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_card(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM cards WHERE id = 'c1'").fetchone()
    card = row_to_card(row, price_usd=12.5)
    assert card.current_price_usd == 12.5


def test_row_to_card_handles_null_json(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    _insert_card(db_path, id="c2", color_identity=None, keywords=None)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM cards WHERE id = 'c2'").fetchone()
    card = row_to_card(row)
    assert card.color_identity == []
    assert card.keywords == []


def test_row_to_card_accepts_plain_dict() -> None:
    card = row_to_card(
        {
            "id": "c3",
            "oracle_id": "o3",
            "name": "Dict Card",
            "cmc": 0.0,
            "type_line": "Land",
            "color_identity": "[]",
            "keywords": "[]",
            "is_legal_commander": 0,
            "is_legal_in_99": 1,
            "set_code": "TST",
            "rarity": "common",
        }
    )
    assert card.id == "c3"
    assert card.is_legal_commander is False
    assert isinstance(card.last_updated, datetime)
