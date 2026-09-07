"""Tests for retired build quotas, global cost ceiling, and cost
attribution of cost_log rows to a user + deck.

Builds use a stub pipeline; cost-ceiling checks return before it starts.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sabermetrics import db
from sabermetrics.config import settings
from sabermetrics.reasoning.client import (
    AnthropicClient,
    CallResult,
    cost_attribution,
)
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "cq.db"
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


def _user(db_path, email="a@local"):
    return db.UsersRepo(db_path).create(
        email=email,
        display_name="A",
        role="user",
        status="active",
        password_hash=db.hash_password("password123"),
    )


def _login(client, uid):
    with client.session_transaction() as sess:
        sess["_user_id"] = uid
        sess["_fresh"] = True


def _seed_deck_this_month(db_path, deck_id, owner_id):
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO generated_decks (id, commander_id, owner_id, cards_json, "
            "rationale) VALUES (?, 'cmdx', ?, '[]', '{}')",
            (deck_id, owner_id),
        )
        conn.commit()


def _seed_cost(db_path, amount):
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO cost_log (call_type, model, cost_usd) VALUES ('fit', 'm', ?)",
            (amount,),
        )
        conn.commit()


def _post_generate(client):
    return client.post(
        "/generate-deck",
        data={"commander_id": "cmdx", "budget": "200", "power": "3"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )


# --- Cost attribution ---


def test_log_cost_attribution(db_path) -> None:
    client = AnthropicClient.__new__(AnthropicClient)  # bypass API-key __init__
    client.db_path = db_path
    result = CallResult(
        content="",
        model="claude-haiku-4-5",
        input_tokens=10,
        cached_input_tokens=0,
        output_tokens=5,
        cost_usd=0.01,
        request_id="r1",
    )
    with cost_attribution("user-1", "deck-1"):
        client._log_cost(result, "fit")
    client._log_cost(result, "profile")  # outside context -> unattributed

    with db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT call_type, user_id, deck_id FROM cost_log ORDER BY id"
        ).fetchall()
    assert (rows[0]["call_type"], rows[0]["user_id"], rows[0]["deck_id"]) == (
        "fit",
        "user-1",
        "deck-1",
    )
    assert rows[1]["user_id"] is None and rows[1]["deck_id"] is None


@pytest.mark.parametrize("retired_quota", [None, 0, 1, 20])
def test_legacy_builds_ignore_retired_limits(app, db_path, monkeypatch, retired_quota):
    from types import SimpleNamespace

    from sabermetrics.pipeline.deck_builder import DeckBuilder

    uid = _user(db_path)
    with db.connect(db_path) as conn:
        conn.execute(
            "UPDATE users SET monthly_deck_quota = ? WHERE id = ?", (retired_quota, uid)
        )
        conn.commit()
    for index in range(21):
        _seed_deck_this_month(db_path, f"old-{index}", uid)

    def build(self, request):
        _seed_deck_this_month(db_path, request.deck_id, uid)
        return SimpleNamespace(deck=SimpleNamespace(id=request.deck_id))

    monkeypatch.setattr(DeckBuilder, "build", build)
    client = app.test_client()
    _login(client, uid)
    response = _post_generate(client)
    assert response.status_code == 200
    assert db.DecksRepo(db_path).count_this_month(uid) == 22


def test_global_ceiling_blocks(app, db_path) -> None:
    uid = _user(db_path)
    _seed_cost(db_path, settings.llm.monthly_cost_ceiling_usd + 1)
    client = app.test_client()
    _login(client, uid)

    resp = _post_generate(client)
    assert resp.status_code == 503
    assert b"cost ceiling" in resp.data
