"""Production corpus refresh preserves app state and exact partner cohorts."""

from datetime import UTC, datetime

import pytest

from sabermetrics import db
from sabermetrics.research import ResearchRepo
from sabermetrics.research_sync import apply_snapshot, source_state
from scripts.setup_db import setup_database


def test_refresh_partners_repeatability_and_atomic_failure(tmp_path):
    path = tmp_path / "app.db"
    setup_database(path)
    user_id = db.UsersRepo(path).create(email="owner@example.test")
    cards = [
        {
            "oracle_id": key,
            "name": key,
            "type_line": "Legendary Creature",
            "commander_legality": "legal",
            "mana_value": 2,
        }
        for key in ("Tymna", "Thrasios", "Kinnan")
    ] + [
        {
            "oracle_id": "ring",
            "name": "Sol Ring",
            "type_line": "Artifact",
            "commander_legality": "legal",
            "mana_value": 1,
        }
    ]
    entries = [
        {
            "entry_id": entry,
            "deck_id": deck,
            "tournament_id": "event",
            "commander_oracle_id": commander,
            "event_date": datetime.now(UTC),
            "standing": standing,
        }
        for entry, deck, commander, standing in (
            ("pair", "d1", "Tymna", 1),
            ("pair", "d1", "Thrasios", 1),
            ("solo", "d2", "Kinnan", 20),
        )
    ]
    deck_cards = [
        {"deck_id": "d1", "oracle_id": "ring", "quantity": 1, "is_commander": False}
    ]
    for _ in range(2):
        state = apply_snapshot(path, cards, entries, deck_cards)
        assert state["entries"] == 2
        assert state["deck_cards"] == 1
        data = ResearchRepo(path).commanders(query="Tymna")
        assert data["recorded_entries"] == 2
        assert data["results"][0]["meta_share"] == 0.5
        detail = ResearchRepo(path).commander_detail("Thrasios")
        assert detail["inclusion_denominator"] == 1
        assert detail["inclusions"][0]["name"] == "Sol Ring"
        assert db.UsersRepo(path).get(user_id) is not None

    previous = source_state(path)

    def interrupted_stream():
        yield deck_cards[0]
        raise RuntimeError("source interrupted")

    with pytest.raises(RuntimeError, match="source interrupted"):
        apply_snapshot(path, cards, entries, interrupted_stream())
    assert source_state(path) == previous
    assert ResearchRepo(path).commander_detail("Thrasios")["inclusion_denominator"] == 1
    with pytest.raises(ValueError, match="empty"):
        apply_snapshot(path, [], [], [])


def test_refresh_retains_existing_card_ids_and_user_decks(tmp_path):
    path = tmp_path / "app.db"
    setup_database(path)
    with db.connect(path) as conn:
        conn.execute(
            "INSERT INTO cards(id,oracle_id,name) VALUES('printing','oracle','Old name')"
        )
        conn.execute(
            "INSERT INTO decks(id,source,source_id,commander_id) VALUES('legacy','test','1','printing')"
        )
        conn.commit()
    apply_snapshot(path, [{"oracle_id": "oracle", "name": "New name"}], [], [])
    with db.connect(path) as conn:
        assert conn.execute("SELECT id,name FROM cards").fetchone()[:] == (
            "printing",
            "New name",
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM decks WHERE id='legacy'").fetchone()[0]
            == 1
        )
