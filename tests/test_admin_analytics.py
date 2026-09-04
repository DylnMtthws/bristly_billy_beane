"""Tests for P6 admin analytics: AdminAnalyticsRepo + analytics routes."""

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
    p = tmp_path / "aa.db"
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


def _login(client, uid):
    with client.session_transaction() as sess:
        sess["_user_id"] = uid
        sess["_fresh"] = True


def _seed(db_path):
    """Two users, a commander, a deck, feedback, a favorite, and a cost row."""
    u1 = _user(db_path, "u1@local")
    u2 = _user(db_path, "u2@local")
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO cards (id, oracle_id, name, cmc, type_line, color_identity, "
            "keywords, is_legal_commander, is_legal_in_99) "
            "VALUES ('cmd1','o1','Cmdr', 3, 'Legendary Creature', '[\"R\"]', '[]', 1, 1)"
        )
        conn.execute(
            "INSERT INTO generated_decks (id, commander_id, owner_id, deck_name, "
            "cards_json, rationale) VALUES ('D', 'cmd1', ?, 'D', '[]', '{}')",
            (u1,),
        )
        conn.execute(
            "INSERT INTO favorite_commanders (user_id, commander_id) VALUES (?, 'cmd1')",
            (u1,),
        )
        conn.execute(
            "INSERT INTO cost_log (call_type, model, cost_usd, user_id, deck_id) "
            "VALUES ('fit', 'm', 0.05, ?, 'D')",
            (u1,),
        )
        conn.commit()
    fb = db.FeedbackRepo(db_path)
    fb.upsert_card(u1, "D", "cu", "CardUp", "up", "great")
    fb.upsert_card(u2, "D", "cu", "CardUp", "up", None)
    fb.upsert_card(u1, "D", "cd", "CardDown", "down", "bad")
    fb.upsert_card(u2, "D", "cd", "CardDown", "down", "weak")
    fb.upsert_deck(u1, "D", "good", "nice")
    return u1, u2


# --- Repo ---


def test_card_aggregate(db_path) -> None:
    _seed(db_path)
    agg = {
        r["card_name"]: r
        for r in db.AdminAnalyticsRepo(db_path).card_feedback_aggregate()
    }
    assert agg["CardUp"]["up"] == 2 and agg["CardUp"]["net"] == 2
    assert agg["CardDown"]["down"] == 2 and agg["CardDown"]["net"] == -2
    assert agg["CardUp"]["comments"] == 1  # only one had a comment


def test_aggregate_sort_net_asc_worst_first(db_path) -> None:
    _seed(db_path)
    rows = db.AdminAnalyticsRepo(db_path).card_feedback_aggregate(sort="net_asc")
    assert rows[0]["card_name"] == "CardDown"


def test_per_user_stats(db_path) -> None:
    u1, _ = _seed(db_path)
    stats = {s["id"]: s for s in db.AdminAnalyticsRepo(db_path).per_user_stats()}
    assert stats[u1]["decks"] == 1
    assert abs(stats[u1]["spend"] - 0.05) < 1e-9
    assert stats[u1]["card_fb"] == 2 and stats[u1]["deck_fb"] == 1


def test_popular(db_path) -> None:
    _seed(db_path)
    repo = db.AdminAnalyticsRepo(db_path)
    assert repo.popular_generated()[0] == {"commander": "Cmdr", "decks": 1}
    assert repo.popular_favorited()[0] == {"commander": "Cmdr", "favorites": 1}


def test_export_rows(db_path) -> None:
    _seed(db_path)
    repo = db.AdminAnalyticsRepo(db_path)
    card_rows = repo.export_card_rows()
    assert len(card_rows) == 4
    assert any(
        r["card_name"] == "CardUp" and r["commander"] == "Cmdr" for r in card_rows
    )
    assert len(repo.export_deck_rows()) == 1


# --- Routes / gating ---


def test_analytics_require_admin(app, db_path) -> None:
    uid = _user(db_path, "plain@local", role="user")
    client = app.test_client()
    _login(client, uid)
    for path in ("/admin/feedback", "/admin/costs", "/admin/commanders"):
        assert client.get(path).status_code == 403


def test_feedback_page_and_drilldown(app, db_path) -> None:
    _seed(db_path)
    admin = _user(db_path, "admin@local", role="admin")
    client = app.test_client()
    _login(client, admin)
    body = client.get("/admin/feedback").data
    assert b"CardUp" in body and b"CardDown" in body
    assert b"great" in client.get("/admin/feedback/card/CardUp").data


def test_export_csv_and_json(app, db_path) -> None:
    _seed(db_path)
    admin = _user(db_path, "admin@local", role="admin")
    client = app.test_client()
    _login(client, admin)

    csv_resp = client.get("/admin/feedback/export?format=csv")
    assert csv_resp.status_code == 200 and csv_resp.mimetype == "text/csv"
    assert b"card_name" in csv_resp.data and b"CardUp" in csv_resp.data

    json_resp = client.get("/admin/feedback/export?format=json")
    payload = json.loads(json_resp.data)
    assert len(payload["card_feedback"]) == 4 and len(payload["deck_feedback"]) == 1


def test_user_detail_route(app, db_path) -> None:
    u1, _ = _seed(db_path)
    admin = _user(db_path, "admin@local", role="admin")
    client = app.test_client()
    _login(client, admin)
    assert client.get(f"/admin/users/{u1}").status_code == 200
    assert client.get("/admin/users/nonexistent").status_code == 404
