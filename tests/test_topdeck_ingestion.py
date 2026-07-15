"""TopDeck.gg ingestion (real v2 API contract).

Hermetic: a synthetic DB (real schema-ish, a handful of real card names) and a
mocked httpx, using a captured-shape fixture. No live API, no prod DB.
"""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sabermetrics.ingestion.topdeck import TopDeckIngestion, _parse_decklist

# A decklist in TopDeck's text format with escaped \n separators (as the API
# returns them), an apostrophe-escaped card, a DFC "//" card, and a miss.
_DECKLIST = (
    "~~Commanders~~\\n1 Sisay, Weatherlight Captain\\n\\n"
    "~~Mainboard~~\\n1 Sol Ring\\n1 Swords to Plowshares\\n"
    "1 Agatha\\'s Soul Cauldron\\n1 Malakir Rebirth // Malakir Mire\\n"
    "1 Totally Not A Real Card 9000\\n"
    "~~Sideboard~~\\n1 Should Be Ignored\\n"
)

_FIXTURE = [{
    "TID": "test-tourney-1",
    "startDate": "2026-06-01",
    "standings": [
        {"name": "Alice", "wins": 4, "losses": 1, "draws": 0,
         "winRate": 0.8, "standing": 1, "decklist": _DECKLIST},
        {"name": "Bob", "wins": 2, "losses": 2, "draws": 1,
         "winRate": 0.5, "standing": 5, "decklist": None},  # no deck
    ],
}]

# Real card names present in the synthetic DB (map to fake ids).
_CARDS = {
    "Sisay, Weatherlight Captain": "id-sisay",
    "Sol Ring": "id-sol",
    "Swords to Plowshares": "id-swords",
    "Agatha's Soul Cauldron": "id-agatha",   # note: no backslash in real name
    "Malakir Rebirth": "id-malakir",          # front face only
}


@pytest.fixture()
def db(tmp_path) -> Path:
    p = tmp_path / "td.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(
        """
        CREATE TABLE cards (id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE decks (id TEXT PRIMARY KEY, source TEXT, source_id TEXT,
            commander_id TEXT, deck_name TEXT, creator TEXT);
        CREATE TABLE deck_cards (deck_id TEXT, card_id TEXT, quantity INTEGER,
            is_commander BOOLEAN, PRIMARY KEY (deck_id, card_id));
        CREATE TABLE tournament_results (id TEXT PRIMARY KEY, tournament_id TEXT,
            player_name TEXT, deck_id TEXT, commander_id TEXT, standing INTEGER,
            win_rate REAL, games_played INTEGER, games_won INTEGER,
            tournament_date TEXT);
        CREATE TABLE source_health (source TEXT PRIMARY KEY,
            last_successful_sync TIMESTAMP, last_failed_sync TIMESTAMP,
            last_error TEXT, consecutive_failures INTEGER DEFAULT 0);
        """
    )
    for name, cid in _CARDS.items():
        conn.execute("INSERT INTO cards VALUES (?, ?)", (cid, name))
    conn.commit()
    conn.close()
    return p


# --- _parse_decklist ---

def test_parse_decklist_sections_and_quantities() -> None:
    cmds, main = _parse_decklist(_DECKLIST)
    assert cmds == ["Sisay, Weatherlight Captain"]
    assert "Sol Ring" in main and "Swords to Plowshares" in main
    assert "Malakir Rebirth // Malakir Mire" in main
    assert "Should Be Ignored" not in main  # sideboard ignored
    assert len(main) == 5


def test_parse_decklist_handles_real_newlines_and_empty() -> None:
    cmds, main = _parse_decklist("~~Commanders~~\n1 Krenko, Mob Boss\n~~Mainboard~~\n2 Goblin")
    assert cmds == ["Krenko, Mob Boss"] and main == ["Goblin"]
    assert _parse_decklist(None) == ([], [])
    assert _parse_decklist("") == ([], [])


# --- _resolve_card_id normalization ---

def test_resolve_card_id_variants(db) -> None:
    ing = TopDeckIngestion(db)
    conn = sqlite3.connect(str(db))
    assert ing._resolve_card_id(conn, "Sol Ring") == "id-sol"           # exact
    assert ing._resolve_card_id(conn, "sol ring") == "id-sol"           # case
    assert ing._resolve_card_id(conn, "Agatha\\'s Soul Cauldron") == "id-agatha"  # escaped '
    assert ing._resolve_card_id(conn, "Malakir Rebirth // Malakir Mire") == "id-malakir"  # DFC front
    assert ing._resolve_card_id(conn, "Totally Not A Real Card 9000") is None
    conn.close()


# --- _process_tournament ---

def test_process_tournament_writes_results_and_deck_cards(db) -> None:
    ing = TopDeckIngestion(db)
    # Only commander-attributed standings are recorded; Bob (no decklist -> no
    # commander) is skipped, since tournament_results feeds per-commander CWE.
    n = ing._process_tournament(_FIXTURE[0])
    assert n == 1

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = {r["player_name"]: r for r in conn.execute("SELECT * FROM tournament_results")}
    assert set(rows) == {"Alice"}
    alice = rows["Alice"]
    assert alice["commander_id"] == "id-sisay"
    assert alice["games_won"] == 4 and alice["games_played"] == 5  # 4+1+0
    assert alice["standing"] == 1 and alice["deck_id"] is not None
    # 4 of 5 mainboard cards resolve (the fake one is dropped) + commander
    ncards = conn.execute(
        "SELECT COUNT(*) FROM deck_cards WHERE deck_id = ?", (alice["deck_id"],)
    ).fetchone()[0]
    assert ncards == 5  # commander + Sol Ring + Swords + Agatha + Malakir
    conn.close()


# --- full sync() with mocked httpx ---

def test_sync_populates_from_mocked_api(db) -> None:
    ing = TopDeckIngestion(db)
    ing._api_key = "test-key"

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = _FIXTURE
    resp.raise_for_status.return_value = None

    with (
        patch("sabermetrics.ingestion.topdeck.httpx.post", return_value=resp),
        patch.object(ing._rate_limiter, "wait", return_value=None),
        patch.object(ing, "last_updated", return_value=None),
    ):
        result = ing.sync(full=False)

    assert result.success and result.items_ingested == 1  # Alice only
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM tournament_results").fetchone()[0] == 1
    conn.close()
