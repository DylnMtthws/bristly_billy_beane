"""Representative recorded lists, public decks, password change, and schema upgrade."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from sabermetrics import db
from sabermetrics.deck_documents import DeckDocumentRepo, DeckNotFound, InvalidCommand
from sabermetrics.research import ResearchRepo
from sabermetrics.ui.app import create_app
from sabermetrics.ui.auth import LOGIN_FAILURE_THRESHOLD
from scripts.setup_db import setup_database


def _login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True


def _seed_legal_cards(path):
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,is_legal_commander,is_legal_in_99)
            VALUES(?,?,?,?,?,?,?,?)""",
            [
                (
                    "kinnan",
                    "ok",
                    "Kinnan",
                    2,
                    "Legendary Creature — Human",
                    '["G","U"]',
                    1,
                    1,
                ),
                ("ring", "or", "Sol Ring", 1, "Artifact", "[]", 0, 1),
                ("bolt", "ob", "Lightning Bolt", 1, "Instant", '["R"]', 0, 1),
                ("island", "oi", "Island", 0, "Basic Land — Island", '["U"]', 0, 1),
            ],
        )
        conn.commit()


def test_visibility_upgrade_on_existing_deck_documents(tmp_path):
    path = tmp_path / "upgrade.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY, email TEXT, display_name TEXT, role TEXT,
            status TEXT, password_hash TEXT
        );
        CREATE TABLE deck_documents (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            title TEXT NOT NULL,
            format TEXT NOT NULL DEFAULT 'commander',
            source_kind TEXT,
            source_id TEXT,
            revision INTEGER NOT NULL DEFAULT 0,
            favorite INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO users(id,email,display_name,role,status)
        VALUES('u1','a@example.test','A','user','active');
        INSERT INTO deck_documents(id,owner_id,title) VALUES('d1','u1','Old deck');
        """)
    conn.commit()
    conn.close()
    setup_database(path)
    with sqlite3.connect(path) as upgraded:
        columns = {
            row[1] for row in upgraded.execute("PRAGMA table_info(deck_documents)")
        }
        assert "visibility" in columns
        assert (
            upgraded.execute(
                "SELECT visibility FROM deck_documents WHERE id='d1'"
            ).fetchone()[0]
            == "private"
        )
        indexes = " ".join(
            row[0]
            for row in upgraded.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_deck_documents_public'"
            )
        )
        assert "visibility" in indexes
        assert "updated_at" in indexes


def test_representative_prefers_complete_list_and_windowed_provenance(tmp_path):
    path = tmp_path / "rep.db"
    setup_database(path)
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=200)).isoformat()
    with db.connect(path) as conn:
        conn.executemany(
            """INSERT INTO cards
            (id,oracle_id,name,cmc,type_line,color_identity,is_legal_commander,is_legal_in_99)
            VALUES(?,?,?,?,?,?,?,?)""",
            [
                ("kinnan", "ok", "Kinnan", 2, "Legendary Creature", '["G","U"]', 1, 1),
                ("ring", "or", "Sol Ring", 1, "Artifact", "[]", 0, 1),
                ("signet", "os", "Simic Signet", 2, "Artifact", '["G","U"]', 0, 1),
            ],
        )
        conn.executemany(
            "INSERT INTO decks(id,source,source_id,commander_id) VALUES(?,?,?,?)",
            [("partial", "test", "1", "kinnan"), ("complete", "test", "2", "kinnan")],
        )
        conn.execute(
            "INSERT INTO deck_cards(deck_id,card_id,quantity,is_commander) VALUES('partial','kinnan',1,1)"
        )
        conn.execute(
            "INSERT INTO deck_cards(deck_id,card_id,quantity,is_commander) VALUES('partial','ring',1,0)"
        )
        conn.execute(
            "INSERT INTO deck_cards(deck_id,card_id,quantity,is_commander) VALUES('complete','kinnan',1,1)"
        )
        conn.execute(
            "INSERT INTO deck_cards(deck_id,card_id,quantity,is_commander) VALUES('complete','signet',99,0)"
        )
        conn.executemany(
            """INSERT INTO tournament_results
            (id,tournament_id,deck_id,commander_id,standing,tournament_date,player_name)
            VALUES(?,?,?,?,?,?,?)""",
            [
                ("r-partial", "e1", "partial", "kinnan", 1, today, "Partial player"),
                ("r-complete", "e2", "complete", "kinnan", 8, today, "Complete player"),
                ("r-old", "e-old", "complete", "kinnan", 1, old, "Old window player"),
            ],
        )
        conn.commit()

    detail = ResearchRepo(path).commander_detail("kinnan", window_days=90)
    representative = detail["representative_list"]
    assert representative["deck_id"] == "complete"
    assert representative["complete"] is True
    assert representative["library_count"] == 99
    assert representative["commander_count"] == 1
    assert representative["player_name"] == "Complete player"
    assert representative["groups"][0]["cards"][0]["quantity"] == 99
    assert detail["inclusions"] == []


def test_public_decks_default_private_exclude_sideboard_and_stable_pagination(tmp_path):
    path = tmp_path / "public.db"
    setup_database(path)
    _seed_legal_cards(path)
    owner = db.UsersRepo(path).create(
        email="owner@example.test", display_name="Owner", status="active"
    )
    other = db.UsersRepo(path).create(
        email="other@example.test", display_name="Other", status="active"
    )
    repo = DeckDocumentRepo(path)
    first = repo.create(owner, title="Alpha", commander_card_id="kinnan")
    second = repo.create(owner, title="Beta", commander_card_id="kinnan")
    assert repo.get(owner, first)["visibility"] == "private"
    listed = repo.list_public()
    assert listed["results"] == []

    document = repo.get(owner, first)
    unsorted = document["zones"][0]["id"]
    repo.apply_commands(
        owner,
        first,
        expected_revision=0,
        mutation_id="add-main",
        commands=[{"type": "add_card", "card_id": "ring", "zone_id": unsorted}],
    )
    repo.apply_commands(
        owner,
        first,
        expected_revision=1,
        mutation_id="sideboard-zone",
        commands=[{"type": "create_zone", "name": "Sideboard"}],
    )
    sideboard = next(
        z for z in repo.get(owner, first)["zones"] if z["name"] == "Sideboard"
    )
    repo.apply_commands(
        owner,
        first,
        expected_revision=2,
        mutation_id="add-side",
        commands=[
            {"type": "add_card", "card_id": "island", "zone_id": sideboard["id"]}
        ],
    )
    with pytest.raises(InvalidCommand):
        repo.apply_commands(
            owner,
            first,
            expected_revision=3,
            mutation_id="via-command",
            commands=[{"type": "set_visibility", "visibility": "public"}],
        )
    assert repo.get(owner, first)["visibility"] == "private"
    repo.set_visibility(owner, first, "public")
    repo.set_visibility(owner, second, "public")
    with pytest.raises(DeckNotFound):
        repo.get(other, first)
    assert repo.get_public(first)["title"]
    repo.apply_commands(
        owner,
        first,
        expected_revision=3,
        mutation_id="later-edit",
        commands=[{"type": "rename_deck", "title": "Alpha public"}],
    )
    assert repo.get(owner, first)["visibility"] == "public"

    projection = repo.get_public(first)
    names = {card["name"] for group in projection["groups"] for card in group["cards"]}
    assert "Sol Ring" in names
    assert "Island" not in names
    assert "owner_id" not in projection
    assert "email" not in str(projection)
    assert projection["display_name"] == "Owner"

    page = repo.list_public(per_page=1)
    assert page["has_next"] is True
    first_id = page["results"][0]["id"]
    second_page = repo.list_public(per_page=1, page=2)
    assert second_page["results"][0]["id"] != first_id
    assert "email" not in str(page["results"])

    repo.set_visibility(owner, first, "private")
    with pytest.raises(DeckNotFound):
        repo.get_public(first)
    assert repo.get_public(second)["id"] == second


def test_password_change_is_atomic_throttled_and_isolated(tmp_path):
    path = tmp_path / "password.db"
    setup_database(path)
    users = db.UsersRepo(path)
    uid = users.create(
        email="a@example.test",
        display_name="A",
        status="active",
        password_hash=db.hash_password("password123"),
    )
    other = users.create(
        email="b@example.test",
        display_name="B",
        status="active",
        password_hash=db.hash_password("password123"),
    )
    app = create_app(path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        AUTH_MODE="password",
    )
    client = app.test_client()
    _login(client, uid)

    security = client.get("/profile/password")
    assert security.status_code == 200
    assert b"Current password" in security.data
    profile = client.get("/profile")
    assert b'name="current_password"' not in profile.data
    assert b"Account security" in profile.data

    bad = client.post(
        "/profile/password",
        data={
            "current_password": "wrong-password",
            "new_password": "newpassword",
            "confirm_password": "newpassword",
        },
    )
    assert bad.status_code == 400
    assert db.verify_password(users.get(uid)["password_hash"], "password123")

    mismatch = client.post(
        "/profile/password",
        data={
            "current_password": "password123",
            "new_password": "newpassword",
            "confirm_password": "different1",
        },
    )
    assert mismatch.status_code == 400

    reuse = client.post(
        "/profile/password",
        data={
            "current_password": "password123",
            "new_password": "password123",
            "confirm_password": "password123",
        },
    )
    assert reuse.status_code == 400

    row = users.get(uid)
    assert users.change_password_if_current(
        uid,
        expected_hash=row["password_hash"],
        expected_session_version=int(row["session_version"] or 0),
        new_hash=db.hash_password("first-win-password"),
    )
    assert not users.change_password_if_current(
        uid,
        expected_hash=row["password_hash"],
        expected_session_version=int(row["session_version"] or 0),
        new_hash=db.hash_password("stale-overwrite"),
    )
    assert db.verify_password(users.get(uid)["password_hash"], "first-win-password")
    users.set_password(uid, db.hash_password("password123"))
    row = users.get(uid)
    version = int(row["session_version"] or 0)
    _login(client, f"{uid}:{version}" if version else uid)

    success = client.post(
        "/profile/password",
        data={
            "current_password": "password123",
            "new_password": "newpassword",
            "confirm_password": "newpassword",
        },
    )
    assert success.status_code == 302
    assert "/login" in success.headers["Location"]
    assert db.verify_password(users.get(uid)["password_hash"], "newpassword")
    blocked = client.get("/profile")
    assert blocked.status_code == 302

    other_client = app.test_client()
    _login(other_client, other)
    for _ in range(LOGIN_FAILURE_THRESHOLD):
        other_client.post(
            "/profile/password",
            data={
                "current_password": "nope-nope-nope",
                "new_password": "otherpassword",
                "confirm_password": "otherpassword",
            },
        )
    fresh = app.test_client()
    version = int(users.get(uid)["session_version"] or 0)
    _login(fresh, f"{uid}:{version}" if version else uid)
    still = fresh.post(
        "/profile/password",
        data={
            "current_password": "newpassword",
            "new_password": "newerpassword",
            "confirm_password": "newerpassword",
        },
    )
    assert still.status_code == 302
    assert db.verify_password(users.get(uid)["password_hash"], "newerpassword")
    assert db.verify_password(users.get(other)["password_hash"], "password123")


def test_password_change_requires_csrf(tmp_path):
    path = tmp_path / "csrf.db"
    setup_database(path)
    uid = db.UsersRepo(path).create(
        email="a@example.test",
        display_name="A",
        status="active",
        password_hash=db.hash_password("password123"),
    )
    app = create_app(path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=True,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        AUTH_MODE="password",
    )
    client = app.test_client()
    _login(client, uid)
    denied = client.post(
        "/profile/password",
        data={
            "current_password": "password123",
            "new_password": "newpassword",
            "confirm_password": "newpassword",
        },
    )
    assert denied.status_code == 400
    assert db.verify_password(
        db.UsersRepo(path).get(uid)["password_hash"], "password123"
    )


def test_research_public_tab_requires_current_public_visibility(tmp_path, monkeypatch):
    path = tmp_path / "research-public.db"
    setup_database(path)
    _seed_legal_cards(path)
    owner = db.UsersRepo(path).create(
        email="owner@example.test", display_name="Owner Name", status="active"
    )
    viewer = db.UsersRepo(path).create(
        email="viewer@example.test", display_name="Viewer", status="active"
    )
    repo = DeckDocumentRepo(path)
    deck_id = repo.create(owner, title="Public Kinnan", commander_card_id="kinnan")
    repo.set_visibility(owner, deck_id, "public")
    monkeypatch.setenv("SABER_DECK_LAB_REDESIGN", "1")
    app = create_app(path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        DECK_LAB_REDESIGN_ENABLED=True,
        DECK_LAB_RESEARCH_ENABLED=True,
        DECK_LAB_BUILDER_ENABLED=True,
    )
    client = app.test_client()
    _login(client, viewer)
    listing = client.get("/research?tab=decks")
    assert listing.status_code == 200
    assert b"Public Kinnan" in listing.data
    assert b"Owner Name" in listing.data
    assert b"owner@example.test" not in listing.data
    detail = client.get(f"/research/deck/{deck_id}")
    assert detail.status_code == 200
    assert b"Public Kinnan" in detail.data
    repo.set_visibility(owner, deck_id, "private")
    assert client.get(f"/research/deck/{deck_id}").status_code == 404
    assert b"Public Kinnan" not in client.get("/research?tab=decks").data
    anonymous = app.test_client()
    assert anonymous.get("/research?tab=decks").status_code == 302
