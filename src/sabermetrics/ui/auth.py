"""Authentication: Flask-Login glue, forms, and the auth blueprint.

Two modes, chosen by ``SABER_AUTH_MODE`` and fixed at app-creation time:

``tailscale`` (ADR-026, how it is deployed)
    The app sits behind ``tailscale serve`` and takes the caller's identity
    from the headers that proxy sets. There is no password, no invite link and
    no login form: Tailscale has already authenticated the user, and the
    ``users`` table only says what they may do. Provisioning is one command —
    ``sabermetrics grant-access <login>``.

``password`` (default; local development and the test suite)
    The original email + argon2id flow with admin-issued invite links.

Accounts are admin-provisioned in **both** modes. A tailnet identity with no
matching row is refused, not auto-created (ADR-015): being on the tailnet means
you can reach the app, not that you may spend its tokens.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_user,
    logout_user,
)
from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField
from wtforms.validators import DataRequired, EqualTo, Length

from sabermetrics import db
from sabermetrics.ui.extensions import limiter
from sabermetrics.ui.tailscale_auth import TailscaleIdentity, identity_from_headers

logger = logging.getLogger(__name__)

#: Auth mode values. Set on ``app.config["AUTH_MODE"]`` by the app factory.
MODE_TAILSCALE = "tailscale"
MODE_PASSWORD = "password"
#: Both at once: tailnet identity when present, password login otherwise. This
#: is what a Funnel deployment needs — Funnel traffic is anonymous, so public
#: visitors have to be able to sign in, while you keep passwordless access.
MODE_HYBRID = "hybrid"
ALL_MODES = (MODE_TAILSCALE, MODE_PASSWORD, MODE_HYBRID)

#: Failed sign-ins before an account is locked, and for how long.
LOGIN_FAILURE_THRESHOLD = 5
LOGIN_LOCK_MINUTES = 15

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to continue."


def auth_mode() -> str:
    """The configured mode for the current app."""
    mode: str = current_app.config.get("AUTH_MODE", MODE_PASSWORD)
    return mode


def tailscale_mode() -> bool:
    """True when tailnet identity is accepted at all."""
    return auth_mode() in (MODE_TAILSCALE, MODE_HYBRID)


def password_mode() -> bool:
    """True when the password form is offered."""
    return auth_mode() in (MODE_PASSWORD, MODE_HYBRID)


bp = Blueprint("auth", __name__)


# --- Flask-Login user wrapper -------------------------------------------


class AuthUser(UserMixin):
    """Flask-Login view over a ``users`` table row (a plain dict)."""

    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def get_id(self) -> str:
        return str(self._row["id"])

    @property
    def is_active(self) -> bool:
        """Disabled/invited accounts cannot hold a session."""
        return self._row.get("status") == "active"

    @property
    def id(self) -> str:
        return str(self._row["id"])

    @property
    def email(self) -> str | None:
        return self._row.get("email")

    @property
    def display_name(self) -> str | None:
        return self._row.get("display_name") or self._row.get("email")

    @property
    def avatar_emoji(self) -> str | None:
        return self._row.get("avatar_emoji")

    @property
    def role(self) -> str:
        return self._row.get("role", "user")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def monthly_deck_quota(self) -> int:
        q = self._row.get("monthly_deck_quota")
        return int(q) if q is not None else db.DEFAULT_MONTHLY_DECK_QUOTA


def _users() -> db.UsersRepo:
    return db.UsersRepo(current_app.config["DB_PATH"])


def _invites() -> db.InviteRepo:
    return db.InviteRepo(current_app.config["DB_PATH"])


@login_manager.user_loader
def load_user(user_id: str) -> AuthUser | None:
    """Reload a user from its id for each request."""
    row = _users().get(user_id)
    return AuthUser(row) if row else None


@login_manager.request_loader
def load_user_from_tailnet(req) -> AuthUser | None:
    """Authenticate from Tailscale identity headers, in tailscale mode only.

    Flask-Login calls this when the session does not already identify a user,
    so a tailnet request needs no cookie, no form post and no redirect: the
    identity arrives with every request and is re-checked on every request.
    Revoking someone is therefore immediate — set their status to disabled and
    the next request is refused, with no session to expire.

    Returns None for anything unrecognised. None means "not authenticated";
    there is no anonymous or default user.
    """
    if not tailscale_mode():
        return None

    identity = current_tailscale_identity(req)
    if identity is None:
        return None

    row = _users().get_by_tailscale_login(identity.login)
    if row is None:
        # On the tailnet but not provisioned. Deliberately not auto-created:
        # reaching the app and being allowed to spend its tokens are different
        # permissions (ADR-015).
        logger.info("Unprovisioned tailnet identity: %s", identity.login)
        return None
    if row.get("status") != "active":
        logger.info("Disabled account attempted access: %s", identity.login)
        return None
    return AuthUser(row)


def current_tailscale_identity(req=None) -> TailscaleIdentity | None:
    """Read the tailnet identity off a request, honouring the source check.

    ``TRUST_TAILSCALE_HEADERS_FROM_ANY_ADDRESS`` lets the test suite exercise
    the header path without inventing CGNAT source addresses. It is refused
    unless ``TESTING`` is also set, and the check lives here rather than in the
    app factory because that is where the flag is read — a boot-time guard
    would not see a config set after ``create_app`` returned, which is exactly
    how a test (or a mistake) sets it.

    Raises:
        RuntimeError: If the escape hatch is enabled outside testing. Failing
            the request is right: with the source check off, anything that can
            reach the port can claim any identity.
    """
    req = req or request
    skip_source_check = current_app.config.get(
        "TRUST_TAILSCALE_HEADERS_FROM_ANY_ADDRESS", False
    )
    if skip_source_check and not current_app.config.get("TESTING"):
        raise RuntimeError(
            "TRUST_TAILSCALE_HEADERS_FROM_ANY_ADDRESS is a test-only setting "
            "and requires TESTING=True. With it on, any caller that can reach "
            "this port can claim to be any user."
        )
    return identity_from_headers(
        req.headers,
        req.remote_addr,
        require_tailnet_source=not skip_source_check,
    )


@login_manager.unauthorized_handler
def _on_unauthorized():
    """Redirect browsers to login; answer API/XHR callers with 401 JSON."""
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )
    if wants_json:
        return {"error": "authentication required"}, 401
    return redirect(url_for("auth.login", next=request.full_path))


def admin_required(view: Callable) -> Callable:
    """Require an authenticated user with the ``admin`` role, else 403/redirect."""

    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


# --- Forms ---------------------------------------------------------------


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])


class InviteAcceptForm(FlaskForm):
    display_name = StringField(
        "Display name", validators=[DataRequired(), Length(max=80)]
    )
    avatar_emoji = StringField("Avatar emoji", validators=[Length(max=8)])
    password = PasswordField(
        "Password", validators=[DataRequired(), Length(min=8, max=200)]
    )
    confirm = PasswordField(
        "Confirm password",
        validators=[
            DataRequired(),
            EqualTo("password", message="Passwords must match"),
        ],
    )


# --- Routes --------------------------------------------------------------


def _safe_next(target: str | None) -> str:
    """Return a same-site redirect target, defaulting to the home page.

    Rejects absolute/off-site URLs to avoid open-redirect abuse.
    """
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("main.index")


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute; 50 per hour", methods=["POST"])
def login():
    """Sign-in. In tailscale mode this only explains what went wrong.

    There is nothing to submit on a tailnet: if you can reach this page at all
    Tailscale has already told us who you are, so arriving here means either
    the request did not come through ``tailscale serve``, or your identity has
    no account. The page says which, because "invalid login" would be a lie in
    both cases.
    """
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    identity = current_tailscale_identity() if tailscale_mode() else None

    if not password_mode():
        # Tailnet-only: there is nothing to submit, so explain which of the two
        # things went wrong rather than showing a form that cannot help.
        row = _users().get_by_tailscale_login(identity.login) if identity else None
        return (
            render_template(
                "login.html",
                tailscale=True,
                password=False,
                identity=identity,
                disabled=bool(row and row.get("status") != "active"),
            ),
            403,
        )

    form = LoginForm()
    if form.validate_on_submit():
        users = _users()
        row = users.get_by_email(form.email.data.strip())
        locked_until = users.lock_expires_at(row) if row else None

        if locked_until is not None:
            # Same answer whether or not the password was right. Telling an
            # attacker they guessed correctly on a locked account hands them a
            # working credential to retry in fifteen minutes.
            flash(
                "Too many failed attempts. This account is locked for a few "
                "minutes.",
                "error",
            )
            logger.warning("Login attempt on locked account %s", row.get("email"))
        elif row is not None and db.verify_password(
            row.get("password_hash"), form.password.data
        ):
            if row.get("status") != "active":
                flash(
                    "This account is not active. Ask the admin for an invite.", "error"
                )
            else:
                users.clear_failed_logins(row["id"])
                users.touch_login(row["id"])
                login_user(AuthUser(row))
                logger.info("Login success for %s", row.get("email"))
                return redirect(_safe_next(request.args.get("next")))
        else:
            if row is not None and users.register_failed_login(
                row["id"],
                threshold=LOGIN_FAILURE_THRESHOLD,
                lock_minutes=LOGIN_LOCK_MINUTES,
            ):
                logger.warning(
                    "Locked account after repeated failures: %s",
                    row.get("email"),
                )
            # One message for "no such account" and "wrong password", so the
            # form cannot be used to enumerate who has an account here.
            flash("Invalid email or password.", "error")
            logger.info("Login failure for %s", form.email.data)

    return render_template(
        "login.html",
        form=form,
        tailscale=bool(identity),
        password=True,
        identity=identity,
    )


@bp.route("/logout")
def logout():
    """Sign out and return to the login page.

    In tailscale mode there is nothing to sign out of: identity comes from the
    tailnet on every request, so clearing a session would change nothing and
    offering the button would imply otherwise.
    """
    logout_user()
    if tailscale_mode() and current_tailscale_identity() is not None:
        # The session is gone, but the identity arrives with the next request,
        # so they are about to be signed straight back in. Say so rather than
        # bouncing them to a login page that will immediately redirect.
        flash(
            "Signed out of your session. Your Tailscale identity still "
            "identifies you on the tailnet — disconnect from it to end access.",
            "info",
        )
        return redirect(url_for("main.index"))
    return redirect(url_for("auth.login"))


@bp.route("/invite/<token>", methods=["GET", "POST"])
def accept_invite(token: str):
    """Accept an invite: set a password + profile, then sign in.

    Password mode only. Under tailnet auth there is nothing for an invite to
    grant — access is provisioned with ``sabermetrics grant-access`` and the
    identity arrives with the request.
    """
    if not password_mode():
        abort(404)
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    invite = _invites().get_valid(token)
    if invite is None:
        return render_template("invite.html", invalid=True), 400

    user = _users().get(invite["user_id"])
    if user is None:
        return render_template("invite.html", invalid=True), 400

    form = InviteAcceptForm()
    if request.method == "GET":
        form.display_name.data = user.get("display_name") or ""

    if form.validate_on_submit():
        users = _users()
        users.activate_with_password(
            user["id"],
            db.hash_password(form.password.data),
            display_name=form.display_name.data.strip(),
            avatar_emoji=(form.avatar_emoji.data or "").strip() or None,
        )
        _invites().mark_used(token)
        row = users.get(user["id"])
        login_user(AuthUser(row))
        logger.info("Invite accepted for %s", row.get("email"))
        return redirect(url_for("main.index"))

    return render_template(
        "invite.html", form=form, email=user.get("email"), invalid=False
    )
