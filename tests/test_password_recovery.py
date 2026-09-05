"""Recovery against isolated SQLite databases and a mocked email transport."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import httpx
import pytest

from sabermetrics import db
from sabermetrics.ui.app import create_app
from sabermetrics.ui.recovery import GENERIC_MESSAGE, EmailConfig, RecoveryMailer
from scripts.setup_db import setup_database


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "recovery.db"
    setup_database(path)
    return path


@pytest.fixture
def configured_env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-email-key")
    monkeypatch.setenv("SABER_EMAIL_FROM", "Deck Lab <accounts@example.com>")
    monkeypatch.setenv("SABER_PUBLIC_URL", "https://decklab.example.com")


@pytest.fixture
def app(db_path, configured_env, monkeypatch):
    monkeypatch.setenv("SABER_AUTH_MODE", "password")
    app = create_app(db_path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
    )
    yield app
    app.extensions["recovery_mailer"].executor.shutdown(wait=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def messages(app, monkeypatch):
    messages = []

    def send(recipient, subject, text):
        messages.append((recipient, subject, text))
        return True

    monkeypatch.setattr(app.extensions["recovery_mailer"], "send", send)
    return messages


@pytest.fixture
def admin(db_path):
    return db.UsersRepo(db_path).create(
        email="admin@example.com",
        display_name="Deck Lab Admin",
        avatar_emoji="A",
        role="admin",
        status="active",
        password_hash=db.hash_password("old-password"),
        monthly_deck_quota=17,
    )


def drain(app):
    app.extensions["recovery_mailer"].executor.submit(lambda: None).result(timeout=5)


def request_token(client, messages, email="admin@example.com"):
    response = client.post("/forgot-password", data={"email": email})
    assert response.status_code == 200
    drain(client.application)
    link = next(
        line for line in messages[-1][2].splitlines() if line.startswith("https:")
    )
    parsed = urlsplit(link)
    assert parsed.netloc == "decklab.example.com"
    assert parsed.path == "/reset-password" and not parsed.query
    return parsed.fragment


def reset(client, token, password="new-password", **kwargs):
    return client.post(
        "/reset-password",
        data={"token": token, "password": password, "confirm": password, **kwargs},
    )


def test_admin_recovery_preserves_profile_and_revokes_sessions_and_invites(
    app, client, db_path, admin, messages
):
    old_browser = app.test_client()
    old_browser.post(
        "/login", data={"email": "admin@example.com", "password": "old-password"}
    )
    assert old_browser.get("/admin/users").status_code == 200
    invite = db.InviteRepo(db_path).create(admin)
    before = db.UsersRepo(db_path).get(admin)
    token = request_token(client, messages, " ADMIN@EXAMPLE.COM ")
    # Requesting or merely opening the form does not change credentials.
    assert db.UsersRepo(db_path).get(admin)["password_hash"] == before["password_hash"]
    assert client.get("/reset-password").status_code == 200
    assert old_browser.get("/admin/users").status_code == 200
    result = reset(client, token)
    assert result.status_code == 302 and result.location.endswith("/login")
    assert client.get("/").status_code == 302  # no automatic login
    assert old_browser.get("/admin/users").status_code == 302
    after = db.UsersRepo(db_path).get(admin)
    for field in (
        "id",
        "email",
        "role",
        "status",
        "display_name",
        "avatar_emoji",
        "monthly_deck_quota",
    ):
        assert before[field] == after[field]
    assert after["session_version"] == 1
    assert db.verify_password(after["password_hash"], "new-password")
    assert not db.verify_password(after["password_hash"], "old-password")
    assert db.InviteRepo(db_path).get_valid(invite) is None
    assert reset(app.test_client(), token).status_code == 400
    drain(app)
    assert messages[-1][1] == "Your Deck Lab password was changed"
    assert "new-password" not in messages[-1][2]
    assert "old-password" not in messages[-1][2]
    assert token not in messages[-1][2]
    client.post(
        "/login", data={"email": "admin@example.com", "password": "new-password"}
    )
    assert client.get("/admin/users").status_code == 200


@pytest.mark.parametrize("kind", ["unknown", "disabled", "invited", "tailnet"])
def test_ineligible_accounts_get_identical_response_and_no_email(
    client, db_path, admin, messages, kind
):
    if kind != "unknown":
        db.UsersRepo(db_path).create(
            email="other@example.com",
            status="active" if kind == "tailnet" else kind,
            password_hash=(
                None if kind == "tailnet" else db.hash_password("password123")
            ),
        )
    valid = client.post("/forgot-password", data={"email": "admin@example.com"})
    drain(client.application)
    messages.clear()
    other = client.post("/forgot-password", data={"email": "other@example.com"})
    drain(client.application)
    assert valid.status_code == other.status_code == 200
    assert valid.data == other.data
    assert GENERIC_MESSAGE.encode().replace(b"'", b"&#39;") in other.data
    assert messages == []


def test_hash_only_storage_and_expiry(client, db_path, admin, messages, monkeypatch):
    monkeypatch.setattr(db.time, "time", lambda: 100000)
    token = request_token(client, messages)
    with db.connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM password_reset_tokens")]
    assert token not in json.dumps(rows)
    assert rows[0]["token_digest"] == db.PasswordResetRepo.digest(token)
    assert rows[0]["expires_at"] == 101800
    monkeypatch.setattr(db.time, "time", lambda: 101800)
    assert reset(client, token).status_code == 400
    assert db.verify_password(
        db.UsersRepo(db_path).get(admin)["password_hash"], "old-password"
    )


def test_concurrent_token_consumption_is_single_use(db_path, admin):
    repo = db.PasswordResetRepo(db_path)
    token, _ = repo.issue("admin@example.com")
    new_hash = db.hash_password("new-password")
    barrier = threading.Barrier(2)

    def consume():
        barrier.wait(timeout=5)
        return repo.consume(token, new_hash)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(consume) for _ in range(2)]
        outcomes = [f.result(timeout=5) for f in futures]
    assert outcomes.count("admin@example.com") == 1
    assert outcomes.count(None) == 1
    assert db.UsersRepo(db_path).get(admin)["session_version"] == 1


def test_other_tokens_invalidated_and_requests_do_not_invalidate_prior_link(
    db_path, admin, monkeypatch
):
    repo = db.PasswordResetRepo(db_path)
    monkeypatch.setattr(db.time, "time", lambda: 100000)
    first, _ = repo.issue("admin@example.com")
    monkeypatch.setattr(db.time, "time", lambda: 100061)
    second, _ = repo.issue("admin@example.com")
    assert repo.consume(first, db.hash_password("new-password")) == "admin@example.com"
    assert repo.consume(second, db.hash_password("another-password")) is None


@pytest.mark.parametrize(
    "change", ["disable", "set_password", "activate_with_password"]
)
def test_account_change_invalidates_outstanding_token(db_path, admin, change):
    repo = db.PasswordResetRepo(db_path)
    token, _ = repo.issue("admin@example.com")
    users = db.UsersRepo(db_path)
    if change == "disable":
        users.set_status(admin, "disabled")
    else:
        getattr(users, change)(admin, db.hash_password("different-password"))
    assert repo.consume(token, db.hash_password("new-password")) is None


def test_recovery_clears_password_lockout(client, db_path, admin, messages):
    users = db.UsersRepo(db_path)
    users.register_failed_login(admin, threshold=1, lock_minutes=15)
    token = request_token(client, messages)
    assert reset(client, token).status_code == 302
    row = users.get(admin)
    assert row["failed_login_count"] == 0 and row["locked_until"] is None


@pytest.mark.parametrize(
    "password,confirm",
    [("short", "short"), ("new-password", "mismatch"), ("x" * 201, "x" * 201)],
)
def test_password_validation_keeps_token_usable(
    client, db_path, admin, messages, password, confirm
):
    token = request_token(client, messages)
    assert reset(client, token, password, confirm=confirm).status_code == 200
    assert db.verify_password(
        db.UsersRepo(db_path).get(admin)["password_hash"], "old-password"
    )
    assert reset(client, token).status_code == 302


def test_csrf_required_for_request_and_reset(app, client, db_path, admin, messages):
    app.config["WTF_CSRF_ENABLED"] = True
    token, _ = db.PasswordResetRepo(db_path).issue("admin@example.com")
    assert (
        client.post("/forgot-password", data={"email": "admin@example.com"}).status_code
        == 400
    )
    assert reset(client, token).status_code == 400
    assert messages == []


def test_host_headers_cannot_poison_reset_link(client, admin, messages):
    response = client.post(
        "/forgot-password",
        data={"email": "admin@example.com"},
        headers={"Host": "attacker.example", "X-Forwarded-Host": "attacker.example"},
    )
    assert response.status_code == 200
    drain(client.application)
    assert "https://decklab.example.com/reset-password#" in messages[0][2]
    assert "attacker.example" not in messages[0][2]


def test_response_headers_and_token_not_in_get_path(client, admin, messages):
    token = request_token(client, messages)
    for path in ("/forgot-password", "/reset-password"):
        response = client.get(path)
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert "script-src 'self'" in response.headers["Content-Security-Policy"]
        assert b"cdn.tailwindcss.com" not in response.data
        assert token.encode() not in response.data
    assert client.get("/static/password-reset.js").status_code == 200
    assert reset(client, "bad-token").status_code == 400


def test_invite_opened_before_reset_cannot_race_password_change(
    client, db_path, admin, monkeypatch
):
    invite = db.InviteRepo(db_path).create(admin)
    token, _ = db.PasswordResetRepo(db_path).issue("admin@example.com")
    recovered_hash = db.hash_password("recovered-password")
    real_hash = db.hash_password

    def reset_during_invite_validation(password):
        assert db.PasswordResetRepo(db_path).consume(token, recovered_hash)
        return real_hash(password)

    monkeypatch.setattr(db, "hash_password", reset_during_invite_validation)
    response = client.post(
        f"/invite/{invite}",
        data={
            "display_name": "Old invite",
            "password": "stale-password",
            "confirm": "stale-password",
        },
    )
    assert response.status_code == 400
    row = db.UsersRepo(db_path).get(admin)
    assert db.verify_password(row["password_hash"], "recovered-password")
    assert row["display_name"] == "Deck Lab Admin"


def test_notification_failure_does_not_undo_password_reset(
    app, client, db_path, admin, messages, monkeypatch
):
    token = request_token(client, messages)
    monkeypatch.setattr(app.extensions["recovery_mailer"], "send", lambda *args: False)
    assert reset(client, token).status_code == 302
    drain(app)
    assert db.verify_password(
        db.UsersRepo(db_path).get(admin)["password_hash"], "new-password"
    )


def test_account_cooldown_hourly_and_global_daily_limits_survive_restarts(
    db_path, admin, monkeypatch
):
    clock = [100000.0]
    monkeypatch.setattr(db.time, "time", lambda: clock[0])
    repo = db.PasswordResetRepo(db_path)
    first, _ = repo.issue("admin@example.com")
    repo.revoke(first)
    assert db.PasswordResetRepo(db_path).issue("ADMIN@example.com") is None
    for _ in range(2):
        clock[0] += 61
        assert repo.issue("admin@example.com") is not None
    clock[0] += 61
    assert repo.issue("admin@example.com") is None
    # Rotating accounts/IPs still cannot exceed the daily email budget.
    for n in range(37):
        email = f"user{n}@example.com"
        db.UsersRepo(db_path).create(
            email=email, status="active", password_hash="test-hash"
        )
        assert repo.issue(email) is not None
    clock[0] += 3601
    assert db.PasswordResetRepo(db_path).issue("admin@example.com") is None
    clock[0] += 86401
    assert repo.issue("admin@example.com") is not None


def test_ip_request_and_reset_limits(app, client, admin, messages):
    app.config["RATELIMIT_ENABLED"] = True
    for _ in range(5):
        assert (
            client.post(
                "/forgot-password", data={"email": "nobody@example.com"}
            ).status_code
            == 200
        )
    assert (
        client.post("/forgot-password", data={"email": "admin@example.com"}).status_code
        == 429
    )
    for _ in range(10):
        assert reset(client, "invalid").status_code == 400
    assert reset(client, "invalid").status_code == 429


def test_slow_email_does_not_hold_request_or_reveal_account(
    app, client, admin, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    mailer = app.extensions["recovery_mailer"]

    def slow_send(*args):
        entered.set()
        assert release.wait(timeout=5)
        return True

    monkeypatch.setattr(mailer, "send", slow_send)
    try:
        assert (
            client.post(
                "/forgot-password", data={"email": "admin@example.com"}
            ).status_code
            == 200
        )
        assert entered.wait(timeout=2)
        assert not release.is_set()
        assert (
            client.post(
                "/forgot-password", data={"email": "missing@example.com"}
            ).status_code
            == 200
        )
    finally:
        release.set()
        drain(app)


def test_queue_capacity_is_bounded(app):
    entered, release = threading.Event(), threading.Event()
    mailer = app.extensions["recovery_mailer"]
    ran = []

    def block():
        entered.set()
        release.wait(timeout=5)

    try:
        mailer.submit(block)
        assert entered.wait(timeout=2)
        for n in range(8):
            mailer.submit(lambda n=n: ran.append(n))
    finally:
        release.set()
        drain(app)
    assert ran == list(range(7))


def test_failed_delivery_revokes_link_without_logging_secrets(
    app, client, db_path, admin, monkeypatch, caplog
):
    mailer = app.extensions["recovery_mailer"]
    captured = []

    def fail(recipient, subject, text):
        captured.append(text.split("#")[1].splitlines()[0])
        return False

    monkeypatch.setattr(mailer, "send", fail)
    assert (
        client.post("/forgot-password", data={"email": "admin@example.com"}).status_code
        == 200
    )
    drain(app)
    assert reset(client, captured[0]).status_code == 400
    assert captured[0] not in caplog.text
    assert "test-email-key" not in caplog.text


@pytest.mark.parametrize(
    "outcome", [200, 401, 429, 500, "timeout", "bad-json", "redirect"]
)
def test_resend_transport_contract_and_safe_errors(
    db_path, configured_env, monkeypatch, caplog, outcome
):
    config = EmailConfig.from_env()
    mailer = RecoveryMailer(db_path, config)
    real_client = httpx.Client

    def handler(request):
        assert str(request.url) == "https://api.resend.com/emails"
        assert request.headers["Authorization"] == "Bearer test-email-key"
        payload = json.loads(request.content)
        assert payload == {
            "from": "Deck Lab <accounts@example.com>",
            "to": ["admin@example.com"],
            "subject": "Test",
            "text": "private-reset-body",
        }
        if outcome == "timeout":
            raise httpx.ReadTimeout("private-reset-body test-email-key")
        if outcome == "bad-json":
            return httpx.Response(200, text="private-reset-body")
        if outcome == "redirect":
            return httpx.Response(307, headers={"Location": "https://attacker.example"})
        return httpx.Response(
            outcome, json={"id": "test-id", "message": "private-reset-body"}
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    try:
        assert mailer.send("admin@example.com", "Test", "private-reset-body") is (
            outcome == 200
        )
        assert "private-reset-body" not in caplog.text
        assert "test-email-key" not in caplog.text
        assert "test-email-key" not in repr(config)
    finally:
        mailer.executor.shutdown(wait=True)


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://user:pw@example.com",
        "https://example.com/path",
        "https://example.com?x=y",
        "https://example.com#token",
        "https://",
        "https://example.com\\evil",
    ],
)
def test_invalid_public_origin_rejected(configured_env, monkeypatch, origin):
    monkeypatch.setenv("SABER_PUBLIC_URL", origin)
    with pytest.raises(ValueError, match="trusted HTTPS origin"):
        EmailConfig.from_env()


@pytest.mark.parametrize(
    "missing", ["RESEND_API_KEY", "SABER_EMAIL_FROM", "SABER_PUBLIC_URL"]
)
def test_partial_configuration_fails_without_secret_values(
    configured_env, monkeypatch, missing
):
    monkeypatch.delenv(missing)
    with pytest.raises(ValueError) as exc:
        EmailConfig.from_env()
    assert "test-email-key" not in str(exc.value)


def test_disabled_recovery_and_tailnet_mode(db_path, monkeypatch):
    for key in ("RESEND_API_KEY", "SABER_EMAIL_FROM", "SABER_PUBLIC_URL"):
        monkeypatch.delenv(key, raising=False)
    app = create_app(db_path)
    client = app.test_client()
    assert b"Forgot password?" not in client.get("/login").data
    assert client.get("/forgot-password").status_code == 503
    app.config["AUTH_MODE"] = "tailscale"
    assert client.get("/forgot-password").status_code == 404
    assert client.get("/reset-password").status_code == 404


def test_existing_database_migration_is_idempotent_and_preserves_users(db_path, admin):
    with db.connect(db_path) as conn:
        conn.execute("DROP TABLE password_reset_tokens")
        conn.execute("ALTER TABLE users DROP COLUMN session_version")
        conn.commit()
    setup_database(db_path)
    setup_database(db_path)
    row = db.UsersRepo(db_path).get(admin)
    assert row["session_version"] == 0 and row["role"] == "admin"
    assert db.verify_password(row["password_hash"], "old-password")
    assert db.PasswordResetRepo(db_path).issue(row["email"]) is not None
