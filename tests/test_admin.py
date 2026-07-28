"""Tests for P2 admin portal: gating + user management.

Uses isolated temp databases so no real data is touched.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.setup_db import setup_database  # noqa: E402

from sabermetrics import db  # noqa: E402
from sabermetrics.ui.app import create_app  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "admin_test.db"
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


def _seed(db_path, email, role, status="active"):
    return db.UsersRepo(db_path).create(
        email=email,
        display_name=email.split("@")[0],
        role=role,
        status=status,
        password_hash=db.hash_password("password123"),
    )


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = user_id
        sess["_fresh"] = True


# --- Gating ---


def test_admin_requires_login(app) -> None:
    resp = app.test_client().get("/admin/users")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_non_admin_is_forbidden(app, db_path) -> None:
    uid = _seed(db_path, "user@local", role="user")
    client = app.test_client()
    _login(client, uid)
    assert client.get("/admin/users").status_code == 403
    assert client.get("/admin/").status_code == 403


def test_admin_can_view_users(app, db_path) -> None:
    uid = _seed(db_path, "boss@local", role="admin")
    client = app.test_client()
    _login(client, uid)
    assert client.get("/admin/").status_code == 200
    resp = client.get("/admin/users")
    assert resp.status_code == 200
    assert b"boss@local" in resp.data


# --- User management ---


def _admin_client(app, db_path):
    uid = _seed(db_path, "boss@local", role="admin")
    client = app.test_client()
    _login(client, uid)
    return client, uid


def test_create_user_generates_invite(app, db_path) -> None:
    client, _ = _admin_client(app, db_path)
    resp = client.post(
        "/admin/users/create",
        data={"email": "friend@local", "role": "user"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"/invite/" in resp.data  # copyable link surfaced

    row = db.UsersRepo(db_path).get_by_email("friend@local")
    assert row is not None and row["status"] == "invited"


def test_create_user_requires_email(app, db_path) -> None:
    client, _ = _admin_client(app, db_path)
    resp = client.post("/admin/users/create", data={"email": ""}, follow_redirects=True)
    assert b"Email is required" in resp.data


def test_create_user_rejects_duplicate(app, db_path) -> None:
    client, _ = _admin_client(app, db_path)
    _seed(db_path, "dupe@local", role="user")
    resp = client.post(
        "/admin/users/create", data={"email": "dupe@local"}, follow_redirects=True
    )
    assert b"already exists" in resp.data


def test_create_admin_role_and_quota(app, db_path) -> None:
    client, _ = _admin_client(app, db_path)
    client.post(
        "/admin/users/create",
        data={"email": "coadmin@local", "role": "admin", "monthly_deck_quota": "50"},
    )
    row = db.UsersRepo(db_path).get_by_email("coadmin@local")
    assert row["role"] == "admin"
    assert row["monthly_deck_quota"] == 50


def test_disable_and_enable_user(app, db_path) -> None:
    client, _ = _admin_client(app, db_path)
    target = _seed(db_path, "target@local", role="user")

    client.post(f"/admin/users/{target}/status", data={"status": "disabled"})
    assert db.UsersRepo(db_path).get(target)["status"] == "disabled"

    client.post(f"/admin/users/{target}/status", data={"status": "active"})
    assert db.UsersRepo(db_path).get(target)["status"] == "active"


def test_admin_cannot_disable_self(app, db_path) -> None:
    client, admin_id = _admin_client(app, db_path)
    client.post(f"/admin/users/{admin_id}/status", data={"status": "disabled"})
    assert db.UsersRepo(db_path).get(admin_id)["status"] == "active"


def test_set_and_clear_quota(app, db_path) -> None:
    client, _ = _admin_client(app, db_path)
    target = _seed(db_path, "q@local", role="user")

    client.post(f"/admin/users/{target}/quota", data={"monthly_deck_quota": "5"})
    assert db.UsersRepo(db_path).get(target)["monthly_deck_quota"] == 5

    client.post(f"/admin/users/{target}/quota", data={"monthly_deck_quota": ""})
    assert db.UsersRepo(db_path).get(target)["monthly_deck_quota"] is None


def test_reinvite_issues_new_token(app, db_path) -> None:
    client, _ = _admin_client(app, db_path)
    target = _seed(db_path, "reinv@local", role="user", status="invited")
    resp = client.post(
        f"/admin/users/{target}/reinvite", follow_redirects=True
    )
    assert resp.status_code == 200
    assert b"/invite/" in resp.data


def test_reinvite_rejected_for_active_user(app, db_path) -> None:
    client, _ = _admin_client(app, db_path)
    target = _seed(db_path, "act@local", role="user", status="active")
    resp = client.post(
        f"/admin/users/{target}/reinvite", follow_redirects=True
    )
    assert b"already active" in resp.data
