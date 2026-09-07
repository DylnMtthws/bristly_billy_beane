"""Tests for the P3 user portal: owner-scoping, favorites, profile.

Uses temp databases with a few seeded cards/decks so ownership and favorites
can be exercised without the full corpus.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "portal.db"
    setup_database(p)
    return p


@pytest.fixture
def app(db_path):
    app = create_app(db_path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
    )
    return app


def _user(db_path, email, role="user"):
    return db.UsersRepo(db_path).create(
        email=email,
        display_name=email.split("@")[0],
        role=role,
        status="active",
        password_hash=db.hash_password("password123"),
    )


def _seed_commander(db_path, cid="cmd1", name="Test Commander", colors=("R",)):
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO cards (id, oracle_id, name, cmc, type_line, color_identity, "
            "keywords, is_legal_commander, is_legal_in_99, set_code, rarity) "
            "VALUES (?, ?, ?, 3, 'Legendary Creature', ?, '[\"Flying\"]', 1, 1, 'tst', 'rare')",
            (cid, cid + "-oid", name, json.dumps(list(colors))),
        )
        conn.execute(
            "INSERT INTO card_prices (card_id, price_usd, snapshot_date) VALUES (?, 1.5, '2026-01-01')",
            (cid,),
        )
        conn.commit()
    return cid


def _seed_deck(db_path, deck_id, owner_id, commander_id="cmd1", name="A Deck"):
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO generated_decks (id, commander_id, owner_id, deck_name, "
            "budget_usd, power_target, estimated_bracket, cards_json, rationale) "
            "VALUES (?, ?, ?, ?, 200, 3, 3, '[]', '{}')",
            (deck_id, commander_id, owner_id, name),
        )
        conn.commit()
    return deck_id


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = user_id
        sess["_fresh"] = True


# --- Explore ---


def test_explore_lists_seeded_commander(app, db_path) -> None:
    _seed_commander(db_path)
    uid = _user(db_path, "a@local")
    client = app.test_client()
    _login(client, uid)
    resp = client.get("/explore")
    assert resp.status_code == 200
    assert b"Test Commander" in resp.data


def test_explore_color_filter_excludes(app, db_path) -> None:
    _seed_commander(db_path, colors=("R",))
    uid = _user(db_path, "a@local")
    client = app.test_client()
    _login(client, uid)
    # A mono-red commander should not appear under an exactly-blue filter.
    resp = client.get("/explore?color=U&color_mode=exactly")
    assert b"Test Commander" not in resp.data


# --- Favorites: commanders ---


def test_favorite_commander_toggle_and_list(app, db_path) -> None:
    cid = _seed_commander(db_path)
    uid = _user(db_path, "a@local")
    client = app.test_client()
    _login(client, uid)

    on = client.post(f"/favorites/commander/{cid}/toggle")
    assert on.get_json() == {"favorited": True}
    assert b"Test Commander" in client.get("/favorites/commanders").data

    off = client.post(f"/favorites/commander/{cid}/toggle")
    assert off.get_json() == {"favorited": False}
    assert b"Test Commander" not in client.get("/favorites/commanders").data


# --- Deck ownership / privacy ---


def test_decks_are_owner_scoped(app, db_path) -> None:
    _seed_commander(db_path)
    a = _user(db_path, "a@local")
    b = _user(db_path, "b@local")
    _seed_deck(db_path, "deckA", a, name="Alice Deck")

    ca = app.test_client()
    _login(ca, a)
    assert b"Alice Deck" in ca.get("/decks").data
    assert ca.get("/deck/deckA").status_code == 200

    cb = app.test_client()
    _login(cb, b)
    assert b"Alice Deck" not in cb.get("/decks").data
    assert cb.get("/deck/deckA").status_code == 403


def test_admin_can_view_any_deck(app, db_path) -> None:
    _seed_commander(db_path)
    a = _user(db_path, "a@local")
    admin = _user(db_path, "admin@local", role="admin")
    _seed_deck(db_path, "deckA", a)
    c = app.test_client()
    _login(c, admin)
    assert c.get("/deck/deckA").status_code == 200


def test_cannot_delete_others_deck(app, db_path) -> None:
    _seed_commander(db_path)
    a = _user(db_path, "a@local")
    b = _user(db_path, "b@local")
    _seed_deck(db_path, "deckA", a)
    cb = app.test_client()
    _login(cb, b)
    assert cb.post("/deck/deckA/delete").status_code == 403
    # still there
    assert db.DecksRepo(db_path).owner_of("deckA") == a


def test_owner_can_delete_deck(app, db_path) -> None:
    _seed_commander(db_path)
    a = _user(db_path, "a@local")
    _seed_deck(db_path, "deckA", a)
    ca = app.test_client()
    _login(ca, a)
    resp = ca.post("/deck/deckA/delete")
    assert resp.status_code == 302
    assert db.DecksRepo(db_path).owner_of("deckA") is None


# --- Favorites: decks ---


def test_favorite_deck_requires_access(app, db_path) -> None:
    _seed_commander(db_path)
    a = _user(db_path, "a@local")
    b = _user(db_path, "b@local")
    _seed_deck(db_path, "deckA", a)

    cb = app.test_client()
    _login(cb, b)
    # B cannot favorite A's deck
    assert cb.post("/favorites/deck/deckA/toggle").status_code == 403

    ca = app.test_client()
    _login(ca, a)
    assert ca.post("/favorites/deck/deckA/toggle").get_json() == {"favorited": True}
    assert b"A Deck" in ca.get("/favorites/decks").data


# --- Profile ---


def test_profile_update(app, db_path) -> None:
    uid = _user(db_path, "a@local")
    client = app.test_client()
    _login(client, uid)
    client.post("/profile", data={"display_name": "Renamed", "avatar_emoji": "🔥"})
    row = db.UsersRepo(db_path).get(uid)
    assert row["display_name"] == "Renamed"
    assert row["avatar_emoji"] == "🔥"


def test_change_password_flow(app, db_path) -> None:
    uid = _user(db_path, "a@local")
    client = app.test_client()
    _login(client, uid)

    # wrong current password -> unchanged
    client.post(
        "/profile/password",
        data={
            "current_password": "nope",
            "new_password": "newpassword",
            "confirm_password": "newpassword",
        },
    )
    assert db.verify_password(
        db.UsersRepo(db_path).get(uid)["password_hash"], "password123"
    )

    # correct -> changed
    client.post(
        "/profile/password",
        data={
            "current_password": "password123",
            "new_password": "newpassword",
            "confirm_password": "newpassword",
        },
    )
    assert db.verify_password(
        db.UsersRepo(db_path).get(uid)["password_hash"], "newpassword"
    )


def test_home_has_no_quota_meter(app, db_path) -> None:
    _seed_commander(db_path)
    uid = _user(db_path, "a@local")
    _seed_deck(db_path, "d1", uid)
    _seed_deck(db_path, "d2", uid)
    assert db.DecksRepo(db_path).count_this_month(uid) == 2
    client = app.test_client()
    _login(client, uid)
    assert b"2 / 20 decks" not in client.get("/").data
    assert b"Monthly quota" not in client.get("/").data
