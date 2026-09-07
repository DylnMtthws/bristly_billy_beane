"""Production corpus refresh preserves app state and exact partner cohorts."""

from datetime import UTC, datetime

import pytest

from sabermetrics import db
from sabermetrics.commander_pairs import pair_id
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
        detail = ResearchRepo(path).commander_detail(pair_id(["Thrasios", "Tymna"]))
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
    assert (
        ResearchRepo(path).commander_detail(pair_id(["Thrasios", "Tymna"]))[
            "inclusion_denominator"
        ]
        == 1
    )
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


def test_exact_pair_cohorts_migration_colors_favorites_and_build(tmp_path, monkeypatch):
    from sabermetrics.deck_documents import DeckDocumentRepo
    from sabermetrics.research_identities import ensure_schema
    from sabermetrics.ui.app import create_app

    path = tmp_path / "pairs.db"
    setup_database(path)
    user = db.UsersRepo(path).create(email="pairs@example.test", status="active")
    colors = {"Rograkh": ["R"], "Tymna": ["W", "B"], "Silas": ["U", "B"]}
    cards = [
        {
            "oracle_id": name,
            "name": name,
            "type_line": "Legendary Creature",
            "oracle_text": "Partner",
            "commander_legality": "legal",
            "color_identity": value,
        }
        for name, value in colors.items()
    ]
    cards += [
        {
            "oracle_id": "white",
            "name": "White spell",
            "type_line": "Instant",
            "commander_legality": "legal",
            "color_identity": ["W"],
        }
    ]
    entries = [
        {
            "entry_id": key,
            "deck_id": key,
            "tournament_id": "event",
            "commander_oracle_id": commander,
            "event_date": datetime.now(UTC),
            "standing": standing,
        }
        for key, pair, standing in [
            ("a", ["Rograkh", "Tymna"], 1),
            ("b", ["Silas", "Rograkh"], None),
            ("c", ["Tymna", "Rograkh"], 20),
        ]
        for commander in pair
    ]
    apply_snapshot(
        path,
        cards,
        entries,
        [{"deck_id": "a", "oracle_id": "white", "quantity": 1, "is_commander": False}],
    )
    # Simulate the previous release's schema and duplicated partner rows.
    with db.connect(path) as conn:
        conn.execute("DROP VIEW research_commanders")
        conn.execute("DROP VIEW research_results")
        conn.execute("DROP INDEX idx_tourney_identity")
        conn.execute("DROP INDEX idx_tourney_cohort")
        conn.execute("ALTER TABLE tournament_results DROP COLUMN commander_identity_id")
        conn.execute("DELETE FROM research_commander_pairs")
        conn.commit()
        ensure_schema(conn)
        ensure_schema(conn)
    repo = ResearchRepo(path)
    rows = repo.commanders(query="Rograkh")
    assert rows["recorded_entries"] == 3
    assert rows["total"] == 2
    assert sum(r["meta_share"] for r in rows["results"]) == 1
    tymna = pair_id(["Rograkh", "Tymna"])
    silas = pair_id(["Rograkh", "Silas"])
    a, b = repo.commander_detail(tymna), repo.commander_detail(silas)
    assert a["color_identity"] == ["W", "B", "R"]
    assert b["color_identity"] == ["U", "B", "R"]
    assert a["metrics"]["entries"] == 2
    assert a["metrics"]["top16_rate"] == 0.5
    assert b["metrics"]["top16_rate"] is None
    assert [c["name"] for c in a["inclusions"]] == ["White spell"]
    assert b["inclusions"] == []
    assert repo.commander_detail("Rograkh") is None
    assert [
        r["id"]
        for r in repo.commanders(colors=["W", "B", "R"], color_mode="exact")["results"]
    ] == [tymna]
    fav = db.FavoritesRepo(path)
    assert fav.toggle_commander(user, tymna)
    assert [
        r["id"]
        for r in repo.commanders(favorites=fav.commander_ids(user), favorite_only=True)[
            "results"
        ]
    ] == [tymna]
    assert not fav.toggle_commander(user, tymna)
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = user
        session["_fresh"] = True
    assert (
        client.get(f"/research/compare?left={tymna}&right={silas}").status_code == 200
    )
    response = client.post(f"/research/commander/{tymna}/build", data={"top": "40"})
    assert response.status_code == 302
    deck = DeckDocumentRepo(path).get(user, response.location.rsplit("/", 1)[-1])
    assert {e["name"] for e in deck["entries"] if e["is_commander"]} == {
        "Rograkh",
        "Tymna",
    }
    assert deck["validation"]["library_target"] == 98
    assert deck["validation"]["library_count"] == 1
    assert (
        client.get("/api/commanders/partners?commander_id=Rograkh&q=Tymna").json[
            "results"
        ][0]["id"]
        == "Tymna"
    )
    assert (
        client.post(
            "/build/new", json={"commander_card_ids": ["Rograkh", "Silas"]}
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/build/new", json={"commander_card_ids": ["Rograkh", "Rograkh"]}
        ).status_code
        == 400
    )
    # Reversed source ordering and repeat sync preserve cohort IDs/favorites.
    fav.toggle_commander(user, tymna)
    apply_snapshot(path, cards, entries[::-1], [])
    assert fav.commander_ids(user) == {tymna}
    assert repo.commander_detail(tymna)["metrics"]["entries"] == 2
