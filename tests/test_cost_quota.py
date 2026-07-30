"""Tests for P4: per-user quota enforcement, global cost ceiling, and cost
attribution of cost_log rows to a user + deck.

Route enforcement is checked without running a real build: the quota/ceiling
gates return before the pipeline starts.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.setup_db import setup_database  # noqa: E402

from sabermetrics import db  # noqa: E402
from sabermetrics.config import settings  # noqa: E402
from sabermetrics.reasoning.client import (  # noqa: E402
    AnthropicClient,
    CallResult,
    cost_attribution,
)
from sabermetrics.ui.app import create_app  # noqa: E402


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


def _user(db_path, email="a@local", quota=None):
    return db.UsersRepo(db_path).create(
        email=email,
        display_name="A",
        role="user",
        status="active",
        password_hash=db.hash_password("password123"),
        monthly_deck_quota=quota,
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
        content="", model="claude-haiku-4-5", input_tokens=10,
        cached_input_tokens=0, output_tokens=5, cost_usd=0.01, request_id="r1",
    )
    with cost_attribution("user-1", "deck-1"):
        client._log_cost(result, "fit")
    client._log_cost(result, "profile")  # outside context -> unattributed

    with db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT call_type, user_id, deck_id FROM cost_log ORDER BY id"
        ).fetchall()
    assert (rows[0]["call_type"], rows[0]["user_id"], rows[0]["deck_id"]) == (
        "fit", "user-1", "deck-1",
    )
    assert rows[1]["user_id"] is None and rows[1]["deck_id"] is None


# --- Quota enforcement ---


def test_quota_blocks_at_limit(app, db_path) -> None:
    uid = _user(db_path, quota=1)
    _seed_deck_this_month(db_path, "d1", uid)
    client = app.test_client()
    _login(client, uid)

    resp = _post_generate(client)
    assert resp.status_code == 429
    assert b"Monthly limit reached" in resp.data


def test_quota_allows_under_limit(app, db_path) -> None:
    uid = _user(db_path, quota=5)
    _seed_deck_this_month(db_path, "d1", uid)
    client = app.test_client()
    _login(client, uid)

    # Under quota: the gate opens; the build then fails on the bogus commander
    # (500), which still proves we got past the 429 quota check.
    resp = _post_generate(client)
    assert resp.status_code != 429


def test_admin_quota_override_raises_limit(app, db_path) -> None:
    uid = _user(db_path, quota=1)
    _seed_deck_this_month(db_path, "d1", uid)
    client = app.test_client()
    _login(client, uid)
    assert _post_generate(client).status_code == 429  # blocked at 1

    db.UsersRepo(db_path).set_quota(uid, 3)  # admin bumps the cap
    assert _post_generate(client).status_code != 429  # now allowed through


def test_global_ceiling_blocks(app, db_path) -> None:
    uid = _user(db_path, quota=20)
    _seed_cost(db_path, settings.llm.monthly_cost_ceiling_usd + 1)
    client = app.test_client()
    _login(client, uid)

    resp = _post_generate(client)
    assert resp.status_code == 503
    assert b"cost ceiling" in resp.data
