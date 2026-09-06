"""Admin portal: user management (P2) + analytics (P6).

Blueprint mounted at ``/admin`` and gated so only an authenticated user with the
``admin`` role can reach any route (ADR-015 — the admin provisions all accounts).
P2 covers user management (invite, enable/disable, quota override). P6 adds the
feedback explorer (+ CSV/JSON export), per-user cost/usage, and popular
commanders — turning collected feedback into something the owner can analyze.
"""

from __future__ import annotations

import csv
import io
import json
import logging

from flask import (
    Blueprint,
    Response,
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


def _analytics() -> db.AdminAnalyticsRepo:
    return db.AdminAnalyticsRepo(current_app.config["DB_PATH"])


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


def _deliver_invite(user_id: str, email: str) -> dict[str, str | bool]:
    """Send automatically when email is configured; report failures honestly."""
    token = _invites().create(user_id)
    invite = _invites().get(token) or {}
    link = url_for("auth.accept_invite", token=token, _external=True)
    result: dict[str, str | bool] = {
        "email": email,
        "link": link,
        "expires_at": str(invite.get("expires_at") or ""),
        "delivered": False,
    }
    mailer = current_app.extensions.get("recovery_mailer")
    if mailer is not None:
        if mailer.send_invite(email, token):
            flash(f"Invitation email sent to {email}.", "success")
            result["delivered"] = True
            result["message"] = "Invitation email accepted for delivery."
        else:
            flash(
                f"The account for {email} is saved, but we couldn't confirm that "
                "the invitation email was sent. Try Resend invite.",
                "error",
            )
            result["message"] = "Email delivery was not confirmed."
        return result
    if current_app.config.get("PUBLIC_DEPLOYMENT"):
        flash(
            "The account is saved, but invitation email is unavailable. "
            "Configure email delivery, then use Resend invite.",
            "error",
        )
        result["message"] = "Email delivery is unavailable."
        return result
    # Keep manual onboarding available for the private/local deployment that
    # intentionally has no outbound email service.
    flash(f"Invited {email}. Send this one-time link:", "success")
    flash(link, "invite")
    result["message"] = "Invitation created for manual delivery."
    return result


def _redesign_users(invite_result: dict | None = None):
    from sabermetrics.admin_dashboard import DeckLabAdminRepo

    query = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    users, counts = DeckLabAdminRepo(current_app.config["DB_PATH"]).users(
        query=query, status=status
    )
    return render_template(
        "deck_lab/admin_users.html",
        users=users,
        counts=counts,
        query=query,
        status=status,
        invite_result=invite_result,
    )


@bp.route("/")
def overview():
    """Admin landing: KPIs across users, decks, spend, and feedback."""
    if current_app.config.get("DECK_LAB_REDESIGN_ENABLED"):
        from sabermetrics.admin_dashboard import DeckLabAdminRepo

        return render_template(
            "deck_lab/admin_overview.html",
            dashboard=DeckLabAdminRepo(current_app.config["DB_PATH"]).overview(
                current_user.id
            ),
        )
    return render_template("admin/overview.html", kpi=_analytics().overview())


@bp.route("/users")
def users():
    """List all users with per-user stats (decks, spend, feedback, last login)."""
    if current_app.config.get("DECK_LAB_REDESIGN_ENABLED"):
        return _redesign_users()
    return render_template("admin/users.html", users=_analytics().per_user_stats())


@bp.route("/users/<user_id>")
def user_detail(user_id: str):
    """Drill-down for a single user: their decks, spend, and feedback."""
    db_path = current_app.config["DB_PATH"]
    user = _users().get(user_id)
    if user is None:
        abort(404)
    decks = db.DecksRepo(db_path).list_for_owner(user_id)
    analytics = _analytics()
    spend = next(
        (u["spend"] for u in analytics.per_user_stats() if u["id"] == user_id), 0.0
    )
    return render_template(
        "admin/user_detail.html", user=user, decks=decks, spend=spend
    )


@bp.route("/users/create", methods=["POST"])
def create_user():
    """Create an invited account and send its one-time invitation email."""
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
    invite_result = _deliver_invite(user_id, email)
    logger.info("Admin %s invited %s", current_user.email, email)
    if current_app.config.get("DECK_LAB_REDESIGN_ENABLED"):
        return _redesign_users(invite_result)
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
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return {"error": "Quota must be a whole number."}, 400
            flash("Quota must be a whole number.", "error")
            return redirect(url_for("admin.users"))
    _users().set_quota(user_id, quota)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {
            "ok": True,
            "quota": quota,
            "label": quota if quota is not None else "default",
        }
    flash("Quota updated.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<user_id>/reinvite", methods=["POST"])
def reinvite(user_id: str):
    """Email a fresh invitation without creating another account."""
    target = _users().get(user_id)
    if target is None:
        abort(404)
    if target.get("status") == "active":
        flash(f"{target.get('email')} is already active.", "error")
        return redirect(url_for("admin.users"))
    if target.get("status") != "invited" or not target.get("email"):
        flash(
            "Only invited accounts with an email address can receive invitations.",
            "error",
        )
        return redirect(url_for("admin.users"))
    invite_result = _deliver_invite(user_id, target["email"])
    if current_app.config.get("DECK_LAB_REDESIGN_ENABLED"):
        return _redesign_users(invite_result)
    return redirect(url_for("admin.users"))


# --- Analytics (P6) ---


@bp.route("/feedback")
def feedback():
    """Feedback explorer: per-card rollup + recent deck verdicts."""
    sort = request.args.get("sort", "total_desc")
    analytics = _analytics()
    if current_app.config.get("DECK_LAB_REDESIGN_ENABLED"):
        return render_template(
            "deck_lab/admin_analytics.html",
            cards=analytics.card_feedback_aggregate(sort=sort),
            deck_feedback=analytics.deck_feedback_list(),
            costs=analytics.cost_totals(),
            generated=analytics.popular_generated(),
            favorited=analytics.popular_favorited(),
            sort=sort,
        )
    return render_template(
        "admin/feedback.html",
        cards=analytics.card_feedback_aggregate(sort=sort),
        deck_feedback=analytics.deck_feedback_list(),
        sort=sort,
    )


@bp.route("/feedback/card/<path:card_name>")
def feedback_card(card_name: str):
    """All comments/votes for one card."""
    return render_template(
        "admin/feedback_card.html",
        card_name=card_name,
        comments=_analytics().card_comments(card_name),
    )


@bp.route("/feedback/export")
def feedback_export():
    """Export all feedback as CSV (card rows) or JSON (card + deck)."""
    fmt = request.args.get("format", "csv")
    analytics = _analytics()
    card_rows = analytics.export_card_rows()

    if fmt == "json":
        payload = json.dumps(
            {"card_feedback": card_rows, "deck_feedback": analytics.export_deck_rows()},
            indent=2,
        )
        return Response(
            payload,
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=feedback.json"},
        )

    buf = io.StringIO()
    fields = [
        "user",
        "deck_id",
        "commander",
        "card_name",
        "vote",
        "comment",
        "updated_at",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in card_rows:
        writer.writerow({k: row.get(k, "") for k in fields})
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=card_feedback.csv"},
    )


@bp.route("/costs")
def costs():
    """Cost & token usage: totals, by call type, and per user."""
    from sabermetrics.config import settings

    analytics = _analytics()
    return render_template(
        "admin/costs.html",
        totals=analytics.cost_totals(),
        by_type=analytics.cost_by_call_type(),
        by_user=analytics.cost_by_user(),
        ceiling=settings.llm.monthly_cost_ceiling_usd,
    )


@bp.route("/commanders")
def commanders():
    """Popular commanders: most-generated and most-favorited."""
    analytics = _analytics()
    return render_template(
        "admin/commanders.html",
        generated=analytics.popular_generated(),
        favorited=analytics.popular_favorited(),
    )
