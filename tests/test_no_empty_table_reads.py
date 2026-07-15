"""No live scoring weight reads an empty table (Option A DoD criterion 3).

The two signals backed by empty tables were removed from scoring:
  * card_cooccurrence  -> see test_synergy_matrix.test_cooccurrence_data_is_ignored
  * card_win_equity    -> verified here

Each test inserts data into the (formerly empty) table and proves the scoring
path ignores it, which is a stronger guarantee than "the table happens to be
empty."
"""

import sqlite3
from pathlib import Path

from sabermetrics.analytics.cvar import ScoringContext, compute_cvar


def _ctx() -> ScoringContext:
    return ScoringContext(
        commander_id="cmdr-1",
        commander_name="Test Commander",
        commander_colors=["G"],
    )


def _card() -> dict:
    return {
        "id": "card-1",
        "name": "Test Card",
        "type_line": "Creature",
        "oracle_text": "Draw a card.",
        "color_identity": ["G"],
        "keywords": "[]",
        "cmc": 2.0,
        "rarity": "rare",
        "price_usd": 1.0,
    }


def test_card_win_equity_is_not_read(tmp_path) -> None:
    """A populated card_win_equity row must not change the composite score."""
    db = tmp_path / "cwe.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE card_win_equity (card_id TEXT, commander_id TEXT, "
        "win_rate_when_present REAL, win_rate_when_absent REAL, cwe_score REAL, "
        "sample_size INTEGER, confidence REAL, last_computed TIMESTAMP, "
        "PRIMARY KEY (card_id, commander_id))"
    )
    conn.execute(
        "INSERT INTO card_win_equity (card_id, commander_id, cwe_score) "
        "VALUES ('card-1', 'cmdr-1', 1.0)"  # strong CWE — would have boosted +0.1
    )
    conn.commit()
    conn.close()

    ctx, card = _ctx(), _card()
    with_table = compute_cvar(card, ctx, db)
    without_table = compute_cvar(card, ctx, None)

    # Scoring ignores the table: identical composite, and no CWE on the result.
    assert with_table.composite_score == without_table.composite_score
    assert with_table.card_win_equity is None
    assert without_table.card_win_equity is None


def test_no_scoring_source_references_the_empty_tables() -> None:
    """Guard: the scoring modules no longer name the empty tables in SQL."""
    root = Path(__file__).resolve().parent.parent / "src" / "sabermetrics"
    cvar_src = (root / "analytics" / "cvar.py").read_text()
    synergy_src = (root / "analytics" / "synergy_matrix.py").read_text()
    # cvar must not SELECT from card_win_equity; synergy must not read cooccurrence.
    assert "FROM card_win_equity" not in cvar_src
    assert "FROM card_cooccurrence" not in synergy_src
