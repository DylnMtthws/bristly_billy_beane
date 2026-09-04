"""Tailnet authentication: identity from the proxy, accounts from the admin.

The security-relevant assertions are the negative ones. Header-based auth is
safe exactly to the extent that a header cannot be set by the wrong party, so
most of what follows is about requests that must be refused.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sabermetrics import db
from sabermetrics.ui.app import create_app
from sabermetrics.ui.tailscale_auth import (
    LOGIN_HEADER,
    NAME_HEADER,
    TAILNET_RANGE,
    identity_from_headers,
    is_tailnet_address,
)
from scripts.setup_db import setup_database

ADMIN_LOGIN = "DylnMtthws@github"
TESTER_LOGIN = "tester@github"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "auth.db"
    setup_database(path)
    return path


def _app(db_path, mode: str, monkeypatch, *, trust_any_source: bool = True):
    monkeypatch.setenv("SABER_AUTH_MODE", mode)
    app = create_app(db_path)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        TRUST_TAILSCALE_HEADERS_FROM_ANY_ADDRESS=trust_any_source,
    )
    return app


@pytest.fixture
def tailnet_app(db_path, monkeypatch):
    return _app(db_path, "tailscale", monkeypatch)


def _grant(db_path, login, *, role="user", status="active", name=None):
    return db.UsersRepo(db_path).create(
        tailscale_login=login,
        display_name=name or login.split("@")[0],
        role=role,
        status=status,
    )


def _get(app, path="/", login=None, **kwargs):
    headers = {LOGIN_HEADER: login} if login else {}
    return app.test_client().get(path, headers=headers, **kwargs)


# --- Identity extraction --------------------------------------------------


class TestIdentityExtraction:
    def test_tailnet_addresses_are_recognised(self):
        assert is_tailnet_address("100.86.61.75")
        assert is_tailnet_address("100.64.0.1")
        assert is_tailnet_address("100.127.255.254")

    @pytest.mark.parametrize(
        "addr",
        [
            "192.168.4.24",
            "10.0.0.5",
            "127.0.0.1",
            "8.8.8.8",
            "100.63.255.255",
            "",
            None,
        ],
    )
    def test_non_tailnet_addresses_are_not(self, addr):
        assert not is_tailnet_address(addr)

    def test_the_range_is_the_cgnat_block(self):
        assert str(TAILNET_RANGE) == "100.64.0.0/10"

    def test_a_header_from_the_tailnet_is_believed(self):
        identity = identity_from_headers(
            {LOGIN_HEADER: ADMIN_LOGIN, NAME_HEADER: "Dylan Matthews"},
            "100.86.61.75",
        )
        assert identity is not None
        assert identity.login == ADMIN_LOGIN
        assert identity.display_name == "Dylan Matthews"

    def test_a_header_from_off_the_tailnet_is_refused(self):
        """Defence against the app being bound to a LAN interface by mistake."""
        assert (
            identity_from_headers({LOGIN_HEADER: ADMIN_LOGIN}, "192.168.4.24") is None
        )

    def test_a_header_from_localhost_is_refused(self):
        """A direct hit on 127.0.0.1:5000 has not been through the proxy."""
        assert identity_from_headers({LOGIN_HEADER: ADMIN_LOGIN}, "127.0.0.1") is None

    def test_no_header_is_no_identity(self):
        """Funnel traffic is anonymous; anonymous is never a default user."""
        assert identity_from_headers({}, "100.86.61.75") is None
        assert identity_from_headers({LOGIN_HEADER: "   "}, "100.86.61.75") is None

    def test_suggested_name_falls_back_to_the_login_stem(self):
        identity = identity_from_headers({LOGIN_HEADER: "alice@github"}, "100.64.0.1")
        assert identity.suggested_name == "alice"


# --- The escape hatch -----------------------------------------------------


class TestTestOnlyEscapeHatch:
    def test_it_is_refused_outside_testing(self, db_path, monkeypatch):
        """With the source check off, any local caller can claim any identity.

        Enforced where the flag is read rather than at app creation: a config
        set after create_app returned would sail past a boot-time guard, and
        that is exactly how it gets set.
        """
        monkeypatch.setenv("SABER_AUTH_MODE", "tailscale")
        app = create_app(db_path)
        app.config.update(TESTING=False, TRUST_TAILSCALE_HEADERS_FROM_ANY_ADDRESS=True)
        _grant(db_path, ADMIN_LOGIN, role="admin")
        # With TESTING off, Flask turns the RuntimeError into a 500 rather than
        # propagating it. Failing the request is the point: a misconfigured
        # trust boundary serves nothing rather than serving the wrong person.
        response = app.test_client().get("/", headers={LOGIN_HEADER: ADMIN_LOGIN})
        assert response.status_code == 500
        assert b"Sabermetrics" not in response.data

    def test_without_the_hatch_a_non_tailnet_request_is_refused(
        self, db_path, monkeypatch
    ):
        """The real configuration: source checking on, test client on 127.0.0.1."""
        _grant(db_path, ADMIN_LOGIN, role="admin")
        app = _app(db_path, "tailscale", monkeypatch, trust_any_source=False)
        response = app.test_client().get(
            "/", headers={LOGIN_HEADER: ADMIN_LOGIN}, follow_redirects=True
        )
        assert response.status_code == 403

    def test_a_forwarded_tailnet_address_is_accepted(self, db_path, monkeypatch):
        """ProxyFix turns one X-Forwarded-For hop into remote_addr."""
        _grant(db_path, ADMIN_LOGIN, role="admin")
        app = _app(db_path, "tailscale", monkeypatch, trust_any_source=False)
        response = app.test_client().get(
            "/",
            headers={LOGIN_HEADER: ADMIN_LOGIN, "X-Forwarded-For": "100.74.82.90"},
        )
        assert response.status_code == 200


# --- Access control -------------------------------------------------------


class TestAccessControl:
    def test_a_provisioned_identity_is_signed_in_with_no_cookie(
        self, tailnet_app, db_path
    ):
        """No form, no POST, no session — identity arrives with the request."""
        _grant(db_path, ADMIN_LOGIN, role="admin")
        assert _get(tailnet_app, "/", ADMIN_LOGIN).status_code == 200

    def test_an_unprovisioned_identity_is_refused_not_created(
        self, tailnet_app, db_path
    ):
        """Reaching the app and being allowed to spend its tokens differ."""
        response = _get(tailnet_app, "/", "stranger@github", follow_redirects=True)
        assert response.status_code == 403
        assert db.UsersRepo(db_path).get_by_tailscale_login("stranger@github") is None

    def test_the_refusal_names_the_command_that_fixes_it(self, tailnet_app):
        response = _get(tailnet_app, "/", "stranger@github", follow_redirects=True)
        assert b"grant-access stranger@github" in response.data

    def test_an_anonymous_request_is_refused(self, tailnet_app, db_path):
        _grant(db_path, ADMIN_LOGIN, role="admin")
        response = _get(tailnet_app, "/", follow_redirects=True)
        assert response.status_code == 403
        assert b"did not arrive through the tailnet" in response.data

    def test_a_disabled_account_is_refused_on_the_next_request(
        self, tailnet_app, db_path
    ):
        """Revocation is immediate: there is no session left to expire."""
        user_id = _grant(db_path, TESTER_LOGIN)
        assert _get(tailnet_app, "/", TESTER_LOGIN).status_code == 200
        db.UsersRepo(db_path).set_status(user_id, "disabled")
        response = _get(tailnet_app, "/", TESTER_LOGIN, follow_redirects=True)
        assert response.status_code == 403
        assert b"has been disabled" in response.data

    def test_admin_areas_still_require_the_admin_role(self, tailnet_app, db_path):
        _grant(db_path, TESTER_LOGIN)
        _grant(db_path, ADMIN_LOGIN, role="admin")
        assert _get(tailnet_app, "/admin/", TESTER_LOGIN).status_code == 403
        assert _get(tailnet_app, "/admin/", ADMIN_LOGIN).status_code == 200

    def test_one_identity_cannot_hold_two_accounts(self, db_path):
        import sqlite3

        _grant(db_path, TESTER_LOGIN)
        with pytest.raises(sqlite3.IntegrityError):
            _grant(db_path, TESTER_LOGIN)

    def test_many_accounts_may_have_no_tailnet_identity(self, db_path):
        """The unique index is partial, so password-era rows still work."""
        users = db.UsersRepo(db_path)
        users.create(email="a@example.com", status="active")
        users.create(email="b@example.com", status="active")
        assert len(users.list_all()) == 2


class TestOwnerScopingSurvives:
    """Identity changed; authorization did not."""

    def test_a_tester_cannot_read_another_users_candidate(self, tailnet_app, db_path):
        _grant(db_path, ADMIN_LOGIN, role="admin")
        _grant(db_path, TESTER_LOGIN)
        other_id = _grant(db_path, "third@github")

        client = tailnet_app.test_client()
        location = client.post(
            "/lab/build",
            data={"pack_id": "kinnan_basalt"},
            headers={LOGIN_HEADER: TESTER_LOGIN},
        ).headers["Location"]

        assert (
            client.get(location, headers={LOGIN_HEADER: TESTER_LOGIN}).status_code
            == 200
        )
        assert (
            client.get(location, headers={LOGIN_HEADER: "third@github"}).status_code
            == 403
        )
        assert (
            client.get(location, headers={LOGIN_HEADER: ADMIN_LOGIN}).status_code == 200
        )
        assert other_id

    def test_quota_is_still_per_account(self, tailnet_app, db_path):
        user_id = _grant(db_path, TESTER_LOGIN)
        db.UsersRepo(db_path).set_quota(user_id, 0)
        response = tailnet_app.test_client().post(
            "/lab/build",
            data={"pack_id": "kinnan_basalt"},
            headers={LOGIN_HEADER: TESTER_LOGIN},
            follow_redirects=True,
        )
        assert b"Monthly limit reached" in response.data


# --- Mode isolation -------------------------------------------------------


class TestModes:
    def test_password_is_the_default(self, db_path):
        assert create_app(db_path).config["AUTH_MODE"] == "password"

    def test_an_unknown_mode_refuses_to_start(self, db_path, monkeypatch):
        monkeypatch.setenv("SABER_AUTH_MODE", "oauth")
        with pytest.raises(ValueError, match="SABER_AUTH_MODE"):
            create_app(db_path)

    def test_identity_headers_are_ignored_in_password_mode(self, db_path, monkeypatch):
        """A password deployment must not become header-authenticated."""
        _grant(db_path, ADMIN_LOGIN, role="admin")
        app = _app(db_path, "password", monkeypatch)
        response = app.test_client().get(
            "/", headers={LOGIN_HEADER: ADMIN_LOGIN}, follow_redirects=True
        )
        assert b"Sign In" in response.data

    def test_the_invite_route_is_gone_in_tailscale_mode(self, tailnet_app):
        assert tailnet_app.test_client().get("/invite/anything").status_code == 404

    def test_the_invite_route_still_works_in_password_mode(self, db_path, monkeypatch):
        app = _app(db_path, "password", monkeypatch)
        assert app.test_client().get("/invite/bogus").status_code == 400

    def test_logout_explains_itself_rather_than_pretending(self, tailnet_app, db_path):
        """A tailnet user is signed straight back in by the next request.

        Bouncing them to a login page that immediately redirects would look
        like the button was broken, so the flash says what actually happened.
        """
        _grant(db_path, TESTER_LOGIN)
        response = tailnet_app.test_client().get(
            "/logout", headers={LOGIN_HEADER: TESTER_LOGIN}, follow_redirects=True
        )
        assert b"still" in response.data and b"identifies you" in response.data

    def test_logout_of_a_password_session_returns_to_the_login_page(
        self, db_path, monkeypatch
    ):
        app = _app(db_path, "password", monkeypatch)
        db.UsersRepo(db_path).create(
            email="t@example.com",
            status="active",
            password_hash=db.hash_password("correct-horse-battery"),
        )
        client = app.test_client()
        client.post(
            "/login",
            data={"email": "t@example.com", "password": "correct-horse-battery"},
            follow_redirects=True,
        )
        assert b"Sign In" in client.get("/logout", follow_redirects=True).data

    def test_the_login_page_offers_no_password_field_on_a_tailnet(self, tailnet_app):
        response = _get(tailnet_app, "/login", follow_redirects=True)
        assert b'type="password"' not in response.data


# --- Rate-limit keying ----------------------------------------------------


def test_rate_limiting_keys_off_the_caller_not_a_dead_cloudflare_header():
    """It read CF-Connecting-IP for a tunnel that was never deployed.

    That header is never present, so every request shared one key and the login
    limiter was effectively global — one person mistyping a password could
    throttle everybody.
    """
    import ast
    from pathlib import Path as _Path

    # The AST, not the text: the module documents the header it used to read,
    # and a substring search would match that explanation.
    tree = ast.parse(
        _Path("src/sabermetrics/ui/extensions.py").read_text(encoding="utf-8")
    )
    called_headers = [
        arg.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]
    assert "CF-Connecting-IP" not in called_headers


# --- Hybrid mode and public exposure --------------------------------------


PASSWORD = "correct-horse-battery"


@pytest.fixture
def hybrid_app(db_path, monkeypatch):
    monkeypatch.setenv("SABER_SECRET_KEY", "x" * 64)
    return _app(db_path, "hybrid", monkeypatch)


def _password_user(db_path, email="tester@example.com", status="active"):
    return db.UsersRepo(db_path).create(
        email=email,
        display_name="Tester",
        status=status,
        password_hash=db.hash_password(PASSWORD),
    )


def _sign_in(client, email="tester@example.com", password=PASSWORD):
    return client.post(
        "/login", data={"email": email, "password": password}, follow_redirects=True
    )


class TestHybridMode:
    """Funnel traffic is anonymous, so a public visitor needs a password.

    The tailnet path must keep working unchanged alongside it, or the
    passwordless access is lost the moment the app goes public.
    """

    def test_a_tailnet_identity_still_signs_in_with_no_password(
        self, hybrid_app, db_path
    ):
        _grant(db_path, ADMIN_LOGIN, role="admin")
        assert _get(hybrid_app, "/", ADMIN_LOGIN).status_code == 200

    def test_a_public_visitor_is_offered_the_password_form(self, hybrid_app):
        response = hybrid_app.test_client().get("/", follow_redirects=True)
        assert b'type="password"' in response.data

    def test_a_public_visitor_can_sign_in(self, hybrid_app, db_path):
        _password_user(db_path)
        assert b"Sign In" not in _sign_in(hybrid_app.test_client()).data

    def test_invites_work_again_in_hybrid_mode(self, hybrid_app):
        """They are how a public tester gets a password in the first place."""
        assert hybrid_app.test_client().get("/invite/bogus").status_code == 400

    def test_an_unprovisioned_tailnet_identity_is_offered_the_form(self, hybrid_app):
        """On a tailnet but without an account, the form is the only way in."""
        response = _get(hybrid_app, "/", "stranger@github", follow_redirects=True)
        assert b'type="password"' in response.data
        assert b"grant-access stranger@github" in response.data


class TestFunnelTrafficIsNeverTrusted:
    def test_identity_headers_on_a_funnel_request_are_refused(
        self, hybrid_app, db_path
    ):
        """Tailscale sets no identity on Funnel, so their presence is a forgery."""
        _grant(db_path, ADMIN_LOGIN, role="admin")
        response = hybrid_app.test_client().get(
            "/",
            headers={LOGIN_HEADER: ADMIN_LOGIN, "Tailscale-Funnel-Request": "?1"},
            follow_redirects=True,
        )
        assert b'type="password"' in response.data

    def test_the_funnel_marker_is_detected(self):
        from sabermetrics.ui.tailscale_auth import is_funnel_request

        assert is_funnel_request({"Tailscale-Funnel-Request": "?1"})
        assert not is_funnel_request({})

    def test_refusal_holds_even_from_a_tailnet_source_address(self):
        """Both checks are independent; neither is the only thing standing up."""
        assert (
            identity_from_headers(
                {LOGIN_HEADER: ADMIN_LOGIN, "Tailscale-Funnel-Request": "?1"},
                "100.86.61.75",
            )
            is None
        )


class TestAccountLockout:
    """IP throttling alone is weak once /login faces the internet."""

    def test_the_account_locks_after_repeated_failures(self, hybrid_app, db_path):
        _password_user(db_path)
        client = hybrid_app.test_client()
        for _ in range(6):
            response = _sign_in(client, password="wrong")
        assert b"locked" in response.data

    def test_a_locked_account_refuses_the_correct_password(self, hybrid_app, db_path):
        """Otherwise the lock is decorative against an attacker who guesses."""
        _password_user(db_path)
        client = hybrid_app.test_client()
        for _ in range(6):
            _sign_in(client, password="wrong")
        assert b"locked" in _sign_in(client).data

    def test_a_successful_sign_in_resets_the_counter(self, hybrid_app, db_path):
        user_id = _password_user(db_path)
        client = hybrid_app.test_client()
        for _ in range(3):
            _sign_in(client, password="wrong")
        _sign_in(client)
        assert db.UsersRepo(db_path).get(user_id)["failed_login_count"] == 0

    def test_an_expired_lock_stops_locking(self, db_path):
        """A lock in the past is not a lock; nobody has to clear a flag.

        A literal rather than a computed offset: the value is naive because
        that is how db.py stores every timestamp, and a fixed date says so
        without a clock call that would have to be explained.
        """
        users = db.UsersRepo(db_path)
        user_id = _password_user(db_path)
        past = "2020-01-01T00:00:00"
        with db.connect(db_path) as conn:
            conn.execute(
                "UPDATE users SET locked_until = ? WHERE id = ?", (past, user_id)
            )
            conn.commit()
        assert users.lock_expires_at(users.get(user_id)) is None

    def test_failures_against_an_unknown_email_do_not_error(self, hybrid_app):
        """No account to count against; must not 500 or leak that fact."""
        response = _sign_in(hybrid_app.test_client(), email="nobody@example.com")
        assert response.status_code == 200
        assert b"Invalid email or password" in response.data

    def test_the_same_message_is_shown_for_unknown_and_wrong(self, hybrid_app, db_path):
        """The form must not enumerate who has an account."""
        _password_user(db_path)
        unknown = _sign_in(hybrid_app.test_client(), email="nobody@example.com")
        wrong = _sign_in(hybrid_app.test_client(), password="wrong")
        assert b"Invalid email or password" in unknown.data
        assert b"Invalid email or password" in wrong.data


class TestPublicDeployment:
    def test_a_public_deployment_requires_a_stable_secret_key(
        self, db_path, monkeypatch
    ):
        """A per-process key breaks CSRF on every restart, and reads as flaky."""
        monkeypatch.setenv("SABER_PUBLIC", "1")
        monkeypatch.delenv("SABER_SECRET_KEY", raising=False)
        with pytest.raises(ValueError, match="SABER_SECRET_KEY"):
            create_app(db_path)

    def test_a_private_deployment_still_starts_without_one(self, db_path, monkeypatch):
        monkeypatch.delenv("SABER_PUBLIC", raising=False)
        monkeypatch.delenv("SABER_SECRET_KEY", raising=False)
        assert create_app(db_path).config["SECRET_KEY"]

    @pytest.mark.parametrize(
        ("header", "value"),
        [
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ],
    )
    def test_baseline_security_headers_are_always_set(self, hybrid_app, header, value):
        assert hybrid_app.test_client().get("/login").headers[header] == value

    def test_hsts_is_set_only_when_public(self, db_path, monkeypatch):
        """Meaningless over http, and it would pin a stale policy locally."""
        monkeypatch.setenv("SABER_SECRET_KEY", "x" * 64)
        monkeypatch.setenv("SABER_PUBLIC", "1")
        public = _app(db_path, "hybrid", monkeypatch)
        assert "Strict-Transport-Security" in public.test_client().get("/login").headers

        monkeypatch.setenv("SABER_PUBLIC", "0")
        private = _app(db_path, "hybrid", monkeypatch)
        assert (
            "Strict-Transport-Security"
            not in private.test_client().get("/login").headers
        )

    def test_hybrid_is_a_recognised_mode(self, db_path, monkeypatch):
        monkeypatch.setenv("SABER_AUTH_MODE", "hybrid")
        assert create_app(db_path).config["AUTH_MODE"] == "hybrid"
