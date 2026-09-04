"""Tests for P1 auth core: login, invite flow, route protection, hashing.

Uses isolated temp databases (built via scripts/setup_db.setup_database) so no
real data is touched and each test starts from a clean schema.
"""

import sys
from pathlib import Path

import pytest

# Make the repo-root `scripts` package importable (namespace package).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "auth_test.db"
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


@pytest.fixture
def client(app):
    return app.test_client()


# --- Password hashing ---


def test_password_hash_roundtrip() -> None:
    h = db.hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert db.verify_password(h, "correct horse battery staple") is True
    assert db.verify_password(h, "wrong") is False
    assert db.verify_password(None, "anything") is False


# --- Route protection ---


def test_home_redirects_when_anonymous(client) -> None:
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_login_page_is_public(client) -> None:
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Sign In" in resp.data


def test_xhr_unauthorized_returns_401_json(client) -> None:
    resp = client.post("/generate-deck", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 401


# --- Login flow ---


def _make_active_user(
    db_path, email="tester@local", password="password123", role="user"
):
    repo = db.UsersRepo(db_path)
    return repo.create(
        email=email,
        display_name="Tester",
        role=role,
        status="active",
        password_hash=db.hash_password(password),
    )


def test_login_success_grants_access(client, db_path) -> None:
    _make_active_user(db_path)
    resp = client.post(
        "/login",
        data={"email": "tester@local", "password": "password123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Session now authenticated: home returns 200.
    assert client.get("/").status_code == 200


def test_login_wrong_password_rejected(client, db_path) -> None:
    _make_active_user(db_path)
    resp = client.post("/login", data={"email": "tester@local", "password": "nope"})
    assert resp.status_code == 200
    assert b"Invalid email or password" in resp.data
    # Still gated.
    assert client.get("/").status_code == 302


def test_invited_user_cannot_login_until_activated(client, db_path) -> None:
    repo = db.UsersRepo(db_path)
    repo.create(
        email="invited@local",
        role="user",
        status="invited",
        password_hash=db.hash_password("password123"),
    )
    resp = client.post(
        "/login", data={"email": "invited@local", "password": "password123"}
    )
    assert resp.status_code == 200
    assert b"not active" in resp.data


def test_logout_clears_session(client, db_path) -> None:
    _make_active_user(db_path)
    client.post("/login", data={"email": "tester@local", "password": "password123"})
    assert client.get("/").status_code == 200
    client.get("/logout")
    assert client.get("/").status_code == 302


# --- Invite flow ---


def test_invite_accept_sets_password_and_activates(client, db_path) -> None:
    users = db.UsersRepo(db_path)
    invites = db.InviteRepo(db_path)
    uid = users.create(email="new@local", status="invited")
    token = invites.create(uid)

    # Landing page renders the setup form.
    assert client.get(f"/invite/{token}").status_code == 200

    resp = client.post(
        f"/invite/{token}",
        data={
            "display_name": "New Player",
            "avatar_emoji": "🧙",
            "password": "brandnewpass",
            "confirm": "brandnewpass",
        },
    )
    assert resp.status_code == 302  # logged in, redirected home

    row = users.get(uid)
    assert row["status"] == "active"
    assert db.verify_password(row["password_hash"], "brandnewpass")
    assert row["display_name"] == "New Player"


def test_invite_is_single_use(client, db_path) -> None:
    users = db.UsersRepo(db_path)
    invites = db.InviteRepo(db_path)
    uid = users.create(email="once@local", status="invited")
    token = invites.create(uid)

    client.post(
        f"/invite/{token}",
        data={
            "display_name": "Once",
            "password": "password123",
            "confirm": "password123",
        },
    )
    # Fresh (unauthenticated) client: token already consumed → invalid.
    fresh = client.application.test_client()
    resp = fresh.get(f"/invite/{token}")
    assert resp.status_code == 400
    assert b"Invalid" in resp.data


def test_expired_invite_is_invalid(client, db_path) -> None:
    users = db.UsersRepo(db_path)
    invites = db.InviteRepo(db_path)
    uid = users.create(email="expired@local", status="invited")
    token = invites.create(uid, ttl_days=-1)  # already expired

    assert invites.get_valid(token) is None
    resp = client.get(f"/invite/{token}")
    assert resp.status_code == 400


def test_password_mismatch_rejected(client, db_path) -> None:
    users = db.UsersRepo(db_path)
    invites = db.InviteRepo(db_path)
    uid = users.create(email="mismatch@local", status="invited")
    token = invites.create(uid)

    resp = client.post(
        f"/invite/{token}",
        data={
            "display_name": "Mismatch",
            "password": "password123",
            "confirm": "different1",
        },
    )
    assert resp.status_code == 200
    assert b"match" in resp.data.lower()
    assert users.get(uid)["status"] == "invited"  # not activated


# --- CLI end-to-end ---


def _testing_client(db_path):
    app = create_app(db_path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
    )
    return app.test_client()


def test_cli_create_admin_then_login(db_path, monkeypatch) -> None:
    from click.testing import CliRunner

    from sabermetrics import main as cli_main

    monkeypatch.setattr(cli_main, "_default_db_path", lambda: db_path)
    result = CliRunner().invoke(
        cli_main.cli,
        ["create-admin", "--email", "boss@local", "--password", "supersecret"],
    )
    assert result.exit_code == 0, result.output

    row = db.UsersRepo(db_path).get_by_email("boss@local")
    assert row and row["role"] == "admin" and row["status"] == "active"

    client = _testing_client(db_path)
    resp = client.post(
        "/login", data={"email": "boss@local", "password": "supersecret"}
    )
    assert resp.status_code == 302
    assert client.get("/").status_code == 200


def test_cli_invite_user_link_is_acceptable(db_path, monkeypatch) -> None:
    from click.testing import CliRunner

    from sabermetrics import main as cli_main

    monkeypatch.setattr(cli_main, "_default_db_path", lambda: db_path)
    result = CliRunner().invoke(
        cli_main.cli,
        ["invite-user", "--email", "friend@local", "--base-url", "http://x"],
    )
    assert result.exit_code == 0, result.output
    assert "/invite/" in result.output

    token = result.output.split("/invite/")[1].strip().split()[0]
    client = _testing_client(db_path)
    resp = client.post(
        f"/invite/{token}",
        data={
            "display_name": "Friend",
            "password": "password123",
            "confirm": "password123",
        },
    )
    assert resp.status_code == 302
    assert db.UsersRepo(db_path).get_by_email("friend@local")["status"] == "active"
