"""Authentication: Flask-Login glue, forms, and the auth blueprint (P1).

Public routes (no login required): ``/login``, ``/logout``, ``/invite/<token>``.
Everything else in the app requires an authenticated, active user; admin-only
areas additionally require the ``admin`` role via :func:`admin_required`.

Accounts are provisioned by the admin only (invite links) — there is no
self-registration route here by design (ADR-015).
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

logger = logging.getLogger(__name__)

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to continue."

bp = Blueprint("auth", __name__)


# --- Flask-Login user wrapper -------------------------------------------


class AuthUser(UserMixin):
    """Flask-Login view over a ``users`` table row (a plain dict)."""

    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def get_id(self) -> str:  # noqa: D102 (Flask-Login contract)
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
    display_name = StringField("Display name", validators=[DataRequired(), Length(max=80)])
    avatar_emoji = StringField("Avatar emoji", validators=[Length(max=8)])
    password = PasswordField(
        "Password", validators=[DataRequired(), Length(min=8, max=200)]
    )
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match")],
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
    """Email + password sign-in."""
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    form = LoginForm()
    if form.validate_on_submit():
        row = _users().get_by_email(form.email.data.strip())
        if (
            row is not None
            and db.verify_password(row.get("password_hash"), form.password.data)
        ):
            if row.get("status") != "active":
                flash("This account is not active. Ask the admin for an invite.", "error")
            else:
                _users().touch_login(row["id"])
                login_user(AuthUser(row))
                logger.info("Login success for %s", row.get("email"))
                return redirect(_safe_next(request.args.get("next")))
        else:
            flash("Invalid email or password.", "error")
            logger.info("Login failure for %s", form.email.data)

    return render_template("login.html", form=form)


@bp.route("/logout")
def logout():
    """Sign out and return to the login page."""
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/invite/<token>", methods=["GET", "POST"])
def accept_invite(token: str):
    """Accept an invite: set a password + profile, then sign in."""
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
