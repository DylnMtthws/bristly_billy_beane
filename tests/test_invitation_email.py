"""Admin invitation delivery through the real mail adapter with mock HTTP."""

import json
from urllib.parse import urlsplit

import httpx
import pytest

from sabermetrics import db
from sabermetrics.ui.app import create_app
from scripts.setup_db import setup_database


@pytest.fixture
def app(tmp_path, monkeypatch):
    path = tmp_path / "invitations.db"
    setup_database(path)
    monkeypatch.setenv("RESEND_API_KEY", "test-invite-key")
    monkeypatch.setenv("SABER_EMAIL_FROM", "Deck Lab <accounts@example.com>")
    monkeypatch.setenv("SABER_PUBLIC_URL", "https://decklab.example.com")
    monkeypatch.setenv("SABER_AUTH_MODE", "password")
    app = create_app(path)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    mailer = app.extensions["recovery_mailer"]
    yield app
    mailer.executor.shutdown(wait=True)


@pytest.fixture
def admin_client(app):
    uid = db.UsersRepo(app.config["DB_PATH"]).create(
        email="admin@example.com",
        role="admin",
        status="active",
        password_hash=db.hash_password("admin-test-password"),
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = uid
        session["_fresh"] = True
    return client


@pytest.fixture
def delivery(monkeypatch):
    state = {"messages": [], "outcome": 200}
    real_client = httpx.Client

    def handler(request):
        assert str(request.url) == "https://api.resend.com/emails"
        assert request.headers["Authorization"] == "Bearer test-invite-key"
        payload = json.loads(request.content)
        state["messages"].append(payload)
        if state["outcome"] == "timeout":
            # Simulate a provider error carrying sensitive details; they must
            # never be logged or reflected into the admin page.
            raise httpx.ReadTimeout(payload["text"] + " test-invite-key")
        return httpx.Response(state["outcome"], json={"id": "test-message-id"})

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return state


def invite_path(message):
    link = next(
        line for line in message["text"].splitlines() if line.startswith("https://")
    )
    parsed = urlsplit(link)
    assert parsed.netloc == "decklab.example.com" and not parsed.query
    assert parsed.path.startswith("/invite/")
    return parsed.path


def test_create_user_sends_email_and_recipient_can_accept(app, admin_client, delivery):
    response = admin_client.post(
        "/admin/users/create",
        data={
            "email": "player@example.com",
            "display_name": "Player",
            "role": "admin",
            "monthly_deck_quota": "12",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert response.data.count(b"Invitation email sent to player@example.com.") == 1
    assert b"Create &amp; send invite" in response.data
    assert b"Resend invite" in response.data
    assert len(delivery["messages"]) == 1
    message = delivery["messages"][0]
    assert message["from"] == "Deck Lab <accounts@example.com>"
    assert message["to"] == ["player@example.com"]
    assert message["subject"] == "You're invited to Deck Lab"
    assert "7 days" in message["text"]
    path = invite_path(message)
    token = path.rsplit("/", 1)[1]
    assert token.encode() not in response.data
    with admin_client.session_transaction() as session:
        assert token not in str(session)
    users = db.UsersRepo(app.config["DB_PATH"])
    user = users.get_by_email("player@example.com")
    assert user["status"] == "invited" and user["password_hash"] is None
    assert user["role"] == "admin" and user["monthly_deck_quota"] == 12
    recipient = app.test_client()
    assert recipient.get(path).status_code == 200
    accepted = recipient.post(
        path,
        data={
            "display_name": "Player",
            "password": "player-password",
            "confirm": "player-password",
        },
    )
    assert accepted.status_code == 302
    user = users.get(user["id"])
    assert user["status"] == "active" and user["role"] == "admin"
    assert user["monthly_deck_quota"] == 12
    assert db.verify_password(user["password_hash"], "player-password")
    assert app.test_client().get(path).status_code == 400


def test_resend_delivers_fresh_link_without_duplicate_user(app, admin_client, delivery):
    users = db.UsersRepo(app.config["DB_PATH"])
    uid = users.create(
        email="player@example.com", display_name="Player", monthly_deck_quota=7
    )
    old_token = db.InviteRepo(app.config["DB_PATH"]).create(uid)
    response = admin_client.post(f"/admin/users/{uid}/reinvite", follow_redirects=True)
    assert response.status_code == 200 and b"Invitation email sent" in response.data
    assert len(delivery["messages"]) == 1
    assert old_token not in delivery["messages"][0]["text"]
    assert len(users.list_all()) == 2
    row = users.get(uid)
    assert row["status"] == "invited" and row["monthly_deck_quota"] == 7


@pytest.mark.parametrize("outcome", [401, 403, 429, 500, "timeout"])
def test_send_failure_is_visible_and_resend_recovers(
    app, admin_client, delivery, outcome, caplog
):
    delivery["outcome"] = outcome
    response = admin_client.post(
        "/admin/users/create",
        data={"email": "player@example.com"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert (
        b"couldn&#39;t confirm" in response.data
        and b"Try Resend invite" in response.data
    )
    assert b"Invitation email sent" not in response.data
    row = db.UsersRepo(app.config["DB_PATH"]).get_by_email("player@example.com")
    assert row["status"] == "invited" and row["password_hash"] is None
    token = invite_path(delivery["messages"][0]).rsplit("/", 1)[1]
    assert token.encode() not in response.data
    assert token not in caplog.text and "test-invite-key" not in caplog.text
    delivery["outcome"] = 200
    response = admin_client.post(
        f"/admin/users/{row['id']}/reinvite", follow_redirects=True
    )
    assert b"Invitation email sent" in response.data
    assert len(delivery["messages"]) == 2


def test_host_header_cannot_change_invite_destination(admin_client, delivery):
    response = admin_client.post(
        "/admin/users/create",
        data={"email": "player@example.com"},
        headers={"X-Forwarded-Host": "attacker.example"},
    )
    assert response.status_code == 302
    assert "attacker.example" not in delivery["messages"][0]["text"]
    invite_path(delivery["messages"][0])


@pytest.mark.parametrize("status", ["active", "disabled"])
def test_reinvite_cannot_change_active_or_disabled_accounts(
    app, admin_client, delivery, status
):
    users = db.UsersRepo(app.config["DB_PATH"])
    uid = users.create(email="player@example.com", status=status)
    response = admin_client.post(f"/admin/users/{uid}/reinvite", follow_redirects=True)
    assert response.status_code == 200
    assert users.get(uid)["status"] == status
    assert delivery["messages"] == []
    with db.connect(app.config["DB_PATH"]) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM invite_tokens WHERE user_id = ?", (uid,)
            ).fetchone()[0]
            == 0
        )


def test_duplicate_user_does_not_send_again(app, admin_client, delivery):
    admin_client.post("/admin/users/create", data={"email": "player@example.com"})
    response = admin_client.post(
        "/admin/users/create",
        data={"email": "PLAYER@example.com"},
        follow_redirects=True,
    )
    assert b"already exists" in response.data
    assert len(delivery["messages"]) == 1


@pytest.mark.parametrize("role", [None, "user"])
def test_only_admin_can_trigger_invite_email(app, delivery, role):
    client = app.test_client()
    if role:
        uid = db.UsersRepo(app.config["DB_PATH"]).create(
            email="user@example.com", role=role, status="active"
        )
        with client.session_transaction() as session:
            session["_user_id"] = uid
    target = db.UsersRepo(app.config["DB_PATH"]).create(email="player@example.com")
    for path in ("/admin/users/create", f"/admin/users/{target}/reinvite"):
        response = client.post(path, data={"email": "new@example.com"})
        assert response.status_code == (403 if role else 302)
    assert delivery["messages"] == []


def test_csrf_required_for_both_email_actions(app, admin_client, delivery):
    app.config["WTF_CSRF_ENABLED"] = True
    uid = db.UsersRepo(app.config["DB_PATH"]).create(email="player@example.com")
    for path in ("/admin/users/create", f"/admin/users/{uid}/reinvite"):
        assert (
            admin_client.post(path, data={"email": "other@example.com"}).status_code
            == 400
        )
    assert delivery["messages"] == []


def test_public_missing_mailer_reports_configuration_problem(
    app, admin_client, delivery
):
    app.extensions.pop("recovery_mailer")
    app.config.update(PUBLIC_DEPLOYMENT=True, PASSWORD_RECOVERY_ENABLED=False)
    response = admin_client.post(
        "/admin/users/create",
        data={"email": "player@example.com"},
        follow_redirects=True,
    )
    assert b"invitation email is unavailable" in response.data
    assert b"Send this one-time link" not in response.data
    assert b"/invite/" not in response.data
    assert (
        db.UsersRepo(app.config["DB_PATH"]).get_by_email("player@example.com")["status"]
        == "invited"
    )
    assert delivery["messages"] == []
