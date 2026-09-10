"""Coordinator acceptance contracts for Research filters and public Decks search."""

import pytest

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


@pytest.fixture
def discovery(tmp_path, monkeypatch):
    path = tmp_path / "discovery.db"
    setup_database(path)
    cards = [
        ("tymna", "Tymna Review", '["W","B"]', "Partner"),
        ("thrasios", "Thrasios Review", '["U","G"]', "Partner"),
        ("kinnan", "Kinnan Review", '["U","G"]', ""),
        ("red", "Red Review", '["R"]', ""),
        ("void", "Void Review", "[]", ""),
    ]
    with db.connect(path) as conn:
        conn.executemany(
            'INSERT INTO cards(id,oracle_id,name,color_identity,oracle_text,type_line,is_legal_commander,is_legal_in_99,cmc) VALUES(?,?,?,?,?,"Legendary Creature — Human",1,1,2)',
            [
                (key, "oracle-" + key, name, colors, text)
                for key, name, colors, text in cards
            ],
        )
        conn.commit()
    users = db.UsersRepo(path)
    owner = users.create(
        email="owner-review@example.test", display_name="Owner", status="active"
    )
    viewer = users.create(
        email="viewer-review@example.test", display_name="Viewer", status="active"
    )
    repo = DeckDocumentRepo(path)
    for title, ids in [
        ("PAIR_PUBLIC", ["tymna", "thrasios"]),
        ("SIMIC_PUBLIC", ["kinnan"]),
        ("RED_PUBLIC", ["red"]),
        ("VOID_PUBLIC", ["void"]),
        ("PAIR_PRIVATE", ["tymna", "thrasios"]),
    ]:
        deck = repo.create(owner, title=title, commander_card_ids=ids)
        if title != "PAIR_PRIVATE":
            repo.set_visibility(owner, deck, "public")
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = viewer
        session["_fresh"] = True
    return client


def matches(client, **query):
    response = client.get("/research/", query_string={"tab": "decks", **query})
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "PAIR_PRIVATE" not in body
    return {
        name
        for name in ["PAIR_PUBLIC", "SIMIC_PUBLIC", "RED_PUBLIC", "VOID_PUBLIC"]
        if name in body
    }


def test_deck_color_filters_use_combined_commander_identity(discovery):
    assert matches(discovery, deck_color="U", deck_color_mode="include") == {
        "PAIR_PUBLIC",
        "SIMIC_PUBLIC",
    }
    assert matches(discovery, deck_color="B", deck_color_mode="exclude") == {
        "SIMIC_PUBLIC",
        "RED_PUBLIC",
        "VOID_PUBLIC",
    }
    assert matches(discovery, deck_color=["U", "G"], deck_color_mode="exactly") == {
        "SIMIC_PUBLIC"
    }
    assert matches(
        discovery, deck_color=["W", "U", "B", "G"], deck_color_mode="exactly"
    ) == {"PAIR_PUBLIC"}
    assert matches(discovery, deck_color="C", deck_color_mode="exactly") == {
        "VOID_PUBLIC"
    }


def test_deck_primary_partial_and_partner_order(discovery):
    assert matches(discovery, commander="Tym") == {"PAIR_PUBLIC"}
    assert matches(discovery, commander="Tymna Review", partner="Thrasios Review") == {
        "PAIR_PUBLIC"
    }
    assert matches(discovery, commander="Thrasios Review", partner="Tymna Review") == {
        "PAIR_PUBLIC"
    }
    assert matches(discovery, commander="Tymna Review", partner="Tymna Review") == set()
    assert (
        matches(discovery, commander="Kinnan Review", partner="Tymna Review") == set()
    )
    assert matches(discovery, commander="Not a commander") == set()


def test_commander_color_dropdown_semantics_are_routed(discovery):
    response = discovery.get("/research/?tab=commanders&color=R&color_mode=exclude")
    assert response.status_code == 200
    assert b"Red Review" not in response.data
    assert b"Kinnan Review" in response.data
    response = discovery.get(
        "/research/?tab=commanders&color=U&color=G&color_mode=exactly"
    )
    assert b"Kinnan Review" in response.data and b"Thrasios Review" in response.data
    assert b"Tymna Review" not in response.data


def test_unknown_color_identity_is_not_colorless(discovery):
    with db.connect(discovery.application.config["DB_PATH"]) as conn:
        conn.execute(
            "UPDATE deck_entries SET color_identity=NULL WHERE deck_id IN (SELECT id FROM deck_documents WHERE title='VOID_PUBLIC')"
        )
        conn.commit()
    assert matches(discovery, deck_color="C", deck_color_mode="exactly") == set()
    with db.connect(discovery.application.config["DB_PATH"]) as conn:
        conn.execute(
            "UPDATE deck_entries SET color_identity='not-json' WHERE deck_id IN (SELECT id FROM deck_documents WHERE title='VOID_PUBLIC')"
        )
        conn.commit()
    assert matches(discovery, deck_color="C", deck_color_mode="exactly") == set()


def test_pair_matching_survives_alternate_printing_identity(discovery):
    with db.connect(discovery.application.config["DB_PATH"]) as conn:
        conn.execute(
            "UPDATE deck_entries SET card_id=NULL WHERE deck_id IN (SELECT id FROM deck_documents WHERE title='PAIR_PUBLIC')"
        )
        conn.commit()
    assert matches(discovery, commander="Tymna Review", partner="Thrasios Review") == {
        "PAIR_PUBLIC"
    }
