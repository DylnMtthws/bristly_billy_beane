"""Admin portal (P2): user management.

Blueprint mounted at ``/admin`` and gated so only an authenticated user with the
``admin`` role can reach any route (ADR-015 — the admin provisions all accounts).
P2 covers user management (invite, enable/disable, quota override); the analytics
views (feedback explorer, cost, popular commanders) arrive in P6.
"""

from __future__ import annotations

import logging

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
from flask_login import current_user

from sabermetrics import db

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__, url_prefix="/admin")

_VALID_STATUSES = {"active", "disabled", "invited"}


@bp.before_request
def _require_admin():
    """Gate the entire admin portal behind an active admin session."""
    if not current_user.is_authenticated:
        from sabermetrics.ui.auth import login_manager

        return login_manager.unauthorized()
    if not getattr(current_user, "is_admin", False):
        abort(403)
    return None


def _users() -> db.UsersRepo:
    return db.UsersRepo(current_app.config["DB_PATH"])


def _invites() -> db.InviteRepo:
    return db.InviteRepo(current_app.config["DB_PATH"])


@bp.route("/")
def overview():
    """Admin landing: high-level counts (fuller KPIs land in P6)."""
    counts = _users().count_by_status()
    return render_template(
        "admin/overview.html",
        counts=counts,
        total_users=sum(counts.values()),
    )


@bp.route("/users")
def users():
    """List all users with status, role, quota, and last login."""
    return render_template("admin/users.html", users=_users().list_all())


@bp.route("/users/create", methods=["POST"])
def create_user():
    """Create an invited account and surface a one-time invite link."""
    users_repo = _users()
    email = (request.form.get("email") or "").strip()
    display_name = (request.form.get("display_name") or "").strip() or None
    role = "admin" if request.form.get("role") == "admin" else "user"

    quota_raw = (request.form.get("monthly_deck_quota") or "").strip()
    quota: int | None = None
    if quota_raw:
        try:
            quota = max(0, int(quota_raw))
        except ValueError:
            flash("Quota must be a whole number.", "error")
            return redirect(url_for("admin.users"))

    if not email:
        flash("Email is required.", "error")
        return redirect(url_for("admin.users"))
    if users_repo.get_by_email(email):
        flash(f"A user with email {email} already exists.", "error")
        return redirect(url_for("admin.users"))

    user_id = users_repo.create(
        email=email,
        display_name=display_name,
        role=role,
        status="invited",
        monthly_deck_quota=quota,
        invited_by=current_user.id,
    )
    token = _invites().create(user_id)
    link = url_for("auth.accept_invite", token=token, _external=True)
    flash(f"Invited {email}. Send this one-time link:", "success")
    flash(link, "invite")
    logger.info("Admin %s invited %s", current_user.email, email)
    return redirect(url_for("admin.users"))


@bp.route("/users/<user_id>/status", methods=["POST"])
def set_status(user_id: str):
    """Enable or disable a user (or reset to invited)."""
    status = request.form.get("status", "")
    if status not in _VALID_STATUSES:
        abort(400)
    target = _users().get(user_id)
    if target is None:
        abort(404)
    if target["id"] == current_user.id and status != "active":
        flash("You can't disable your own admin account.", "error")
        return redirect(url_for("admin.users"))
    _users().set_status(user_id, status)
    flash(f"{target.get('email')} set to {status}.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<user_id>/quota", methods=["POST"])
def set_quota(user_id: str):
    """Set or clear a user's monthly deck-quota override (blank = default)."""
    if _users().get(user_id) is None:
        abort(404)
    raw = (request.form.get("monthly_deck_quota") or "").strip()
    quota: int | None = None
    if raw:
        try:
            quota = max(0, int(raw))
        except ValueError:
            flash("Quota must be a whole number.", "error")
            return redirect(url_for("admin.users"))
    _users().set_quota(user_id, quota)
    flash("Quota updated.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<user_id>/reinvite", methods=["POST"])
def reinvite(user_id: str):
    """Issue a fresh invite link for an invited/inactive user."""
    target = _users().get(user_id)
    if target is None:
        abort(404)
    if target.get("status") == "active":
        flash(f"{target.get('email')} is already active.", "error")
        return redirect(url_for("admin.users"))
    token = _invites().create(user_id)
    link = url_for("auth.accept_invite", token=token, _external=True)
    flash(f"New invite link for {target.get('email')}:", "success")
    flash(link, "invite")
    return redirect(url_for("admin.users"))
