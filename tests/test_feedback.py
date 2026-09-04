"""Tests for P5 feedback: FeedbackRepo upserts + owner-only endpoints + UI gating."""

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
    p = tmp_path / "fb.db"
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


def _seed_deck(db_path, deck_id, owner_id):
    """Seed a commander card + a deck owning one card 'cx'."""
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO cards (id, oracle_id, name, cmc, type_line, color_identity, "
            "keywords, is_legal_commander, is_legal_in_99) "
            "VALUES ('cmd1','o1','Cmd', 3, 'Legendary Creature', '[\"R\"]', '[]', 1, 1)"
        )
        cards_json = json.dumps(
            [
                {
                    "card_id": "cx",
                    "name": "CardX",
                    "type_line": "Creature",
                    "slot_role": "utility",
                    "cvar_score": 5.0,
                }
            ]
        )
        conn.execute(
            "INSERT INTO generated_decks (id, commander_id, owner_id, deck_name, "
            "cards_json, rationale, budget_usd, power_target, estimated_bracket) "
            "VALUES (?, 'cmd1', ?, 'D', ?, '{}', 200, 3, 3)",
            (deck_id, owner_id, cards_json),
        )
        conn.commit()


# --- Repo ---


def test_card_upsert_is_idempotent(db_path) -> None:
    repo = db.FeedbackRepo(db_path)
    repo.upsert_card("u1", "d1", "cx", "CardX", "up", "good")
    repo.upsert_card("u1", "d1", "cx", "CardX", "down", "actually bad")
    m = repo.card_map("u1", "d1")
    assert list(m.keys()) == ["cx"]  # one row, not two
    assert m["cx"] == {"vote": "down", "comment": "actually bad"}


def test_card_upsert_blank_clears(db_path) -> None:
    repo = db.FeedbackRepo(db_path)
    repo.upsert_card("u1", "d1", "cx", "CardX", "up", "note")
    repo.upsert_card("u1", "d1", "cx", "CardX", "", "")
    assert repo.card_map("u1", "d1")["cx"] == {"vote": None, "comment": None}


def test_deck_upsert(db_path) -> None:
    repo = db.FeedbackRepo(db_path)
    repo.upsert_deck("u1", "d1", "good", "solid")
    repo.upsert_deck("u1", "d1", "mixed", "on reflection")
    assert repo.deck("u1", "d1") == {"verdict": "mixed", "comment": "on reflection"}


# --- Endpoints ---


def test_owner_can_submit_card_feedback(app, db_path) -> None:
    a = _user(db_path, "a@local")
    _seed_deck(db_path, "deckA", a)
    client = app.test_client()
    _login(client, a)

    resp = client.post(
        "/deck/deckA/card/cx/feedback",
        data={"vote": "up", "comment": "nice", "card_name": "CardX"},
    )
    assert resp.status_code == 200 and resp.get_json()["vote"] == "up"
    assert db.FeedbackRepo(db_path).card_map(a, "deckA")["cx"]["vote"] == "up"


def test_non_owner_card_feedback_forbidden(app, db_path) -> None:
    a = _user(db_path, "a@local")
    b = _user(db_path, "b@local")
    _seed_deck(db_path, "deckA", a)
    client = app.test_client()
    _login(client, b)
    assert (
        client.post("/deck/deckA/card/cx/feedback", data={"vote": "up"}).status_code
        == 403
    )


def test_invalid_vote_rejected(app, db_path) -> None:
    a = _user(db_path, "a@local")
    _seed_deck(db_path, "deckA", a)
    client = app.test_client()
    _login(client, a)
    assert (
        client.post(
            "/deck/deckA/card/cx/feedback", data={"vote": "sideways"}
        ).status_code
        == 400
    )


def test_deck_feedback_endpoint(app, db_path) -> None:
    a = _user(db_path, "a@local")
    _seed_deck(db_path, "deckA", a)
    client = app.test_client()
    _login(client, a)
    assert (
        client.post(
            "/deck/deckA/feedback", data={"verdict": "good", "comment": "gg"}
        ).status_code
        == 200
    )
    assert db.FeedbackRepo(db_path).deck(a, "deckA")["verdict"] == "good"
    assert (
        client.post("/deck/deckA/feedback", data={"verdict": "nope"}).status_code == 400
    )


# --- UI gating ---


def test_owner_sees_feedback_ui(app, db_path) -> None:
    a = _user(db_path, "a@local")
    _seed_deck(db_path, "deckA", a)
    client = app.test_client()
    _login(client, a)
    body = client.get("/deck/deckA").data
    assert b"Your rating" in body and b"deck-feedback" in body


def test_admin_viewing_others_deck_has_no_feedback_ui(app, db_path) -> None:
    a = _user(db_path, "a@local")
    admin = _user(db_path, "admin@local", role="admin")
    _seed_deck(db_path, "deckA", a)
    client = app.test_client()
    _login(client, admin)
    resp = client.get("/deck/deckA")
    assert resp.status_code == 200  # admin can view
    assert b"Your rating" not in resp.data  # but cannot rate someone else's deck
