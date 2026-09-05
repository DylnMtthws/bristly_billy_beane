"""Password recovery with bounded background delivery through Resend.

No raw reset token is persisted locally. Links use a URL fragment, copied into
the POST body by the reset page, so tokens do not enter HTTP access logs.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import BoundedSemaphore
from urllib.parse import urlsplit

import httpx
from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    session,
    url_for,
)
from flask_login import logout_user
from flask_wtf import FlaskForm
from wtforms import HiddenField, PasswordField, StringField
from wtforms.validators import DataRequired, EqualTo, Length, Regexp

from sabermetrics import db
from sabermetrics.ui.auth import password_mode
from sabermetrics.ui.extensions import limiter

logger = logging.getLogger(__name__)
bp = Blueprint("recovery", __name__)
GENERIC_MESSAGE = (
    "If an active account matches that email, you'll receive a password reset link. "
    "Check your inbox and spam folder. If nothing arrives, try again later."
)


@dataclass(frozen=True)
class EmailConfig:
    api_key: str = field(repr=False)
    sender: str
    base_url: str

    @classmethod
    def from_env(cls) -> EmailConfig | None:
        values = [
            os.environ.get(name, "").strip()
            for name in ("RESEND_API_KEY", "SABER_EMAIL_FROM", "SABER_PUBLIC_URL")
        ]
        if not any(values):
            return None
        if not all(values):
            raise ValueError(
                "Password recovery requires RESEND_API_KEY, SABER_EMAIL_FROM, "
                "and SABER_PUBLIC_URL together"
            )
        key, sender, base_url = values
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or any(c.isspace() for c in base_url)
            or "\\" in base_url
        ):
            raise ValueError("SABER_PUBLIC_URL must be a trusted HTTPS origin")
        if "@" not in sender or "\r" in sender or "\n" in sender:
            raise ValueError("SABER_EMAIL_FROM must be a valid sender address")
        return cls(key, sender, base_url.rstrip("/"))


class RecoveryMailer:
    """One worker, at most eight queued/running tasks; no unbounded queue.

    Every request queues the same work before any account lookup. A process
    restart may lose pending mail; users can retry. Delivery errors never log
    credentials, message bodies, or provider responses.
    """

    def __init__(self, db_path: Path, config: EmailConfig) -> None:
        self.repo = db.PasswordResetRepo(db_path)
        self.config = config
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="recovery")
        self.slots = BoundedSemaphore(8)

    def submit(self, work: Callable[[], None]) -> None:
        if not self.slots.acquire(blocking=False):
            logger.warning("Password recovery email queue is full")
            return

        def run() -> None:
            try:
                work()
            except Exception:
                # Exceptions can carry tokens/HTTP bodies. Never log them.
                logger.error("Password recovery background task failed")
            finally:
                self.slots.release()

        try:
            self.executor.submit(run)
        except RuntimeError:
            self.slots.release()
            logger.error("Password recovery email worker is unavailable")

    def send(self, recipient: str, subject: str, text: str) -> bool:
        try:
            with httpx.Client(
                timeout=10, follow_redirects=False, trust_env=False
            ) as client:
                response = client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                    json={
                        "from": self.config.sender,
                        "to": [recipient],
                        "subject": subject,
                        "text": text,
                    },
                )
            if response.status_code == 200 and response.json().get("id"):
                return True
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
        logger.error("Resend did not confirm email acceptance")
        return False

    def send_invite(self, recipient: str, token: str) -> bool:
        """Send an admin-issued invite using the trusted website origin.

        Admin actions wait for the bounded HTTP call so they can report an
        unconfirmed send immediately and offer a retry, rather than claiming
        that a background task delivered an invitation.
        """
        return self.send(
            recipient,
            "You're invited to Deck Lab",
            "You've been invited to Deck Lab. Choose your password and finish "
            "setting up your account using this one-time link within 7 days:\n\n"
            f"{self.config.base_url}/invite/{token}\n\n"
            "If you weren't expecting this invitation, you can ignore this email.",
        )

    def request_reset(self, email: str) -> None:
        issued = self.repo.issue(email)
        if issued is None:
            return
        token, recipient = issued
        link = f"{self.config.base_url}/reset-password#{token}"
        if not self.send(
            recipient,
            "Reset your Deck Lab password",
            "Choose a new password using this link within 30 minutes:\n\n"
            f"{link}\n\n"
            "If you did not request this, you can ignore this email. "
            "Your password has not changed.",
        ):
            self.repo.revoke(token)

    def notify_changed(self, email: str) -> None:
        self.send(
            email,
            "Your Deck Lab password was changed",
            "Your Deck Lab password has been changed and previous sessions "
            "have been signed out. If this wasn't you, use Forgot password "
            f"at {self.config.base_url}/login to recover your account.",
        )


def init_recovery(app: Flask) -> None:
    config = EmailConfig.from_env()
    app.config["PASSWORD_RECOVERY_ENABLED"] = config is not None
    if config is not None:
        app.extensions["recovery_mailer"] = RecoveryMailer(
            app.config["DB_PATH"], config
        )
    app.register_blueprint(bp)


class ForgotPasswordForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Length(max=254)])


class ResetPasswordForm(FlaskForm):
    token = HiddenField(validators=[DataRequired(), Regexp(r"^[A-Za-z0-9_-]{43}\Z")])
    password = PasswordField(
        "New password", validators=[DataRequired(), Length(min=8, max=200)]
    )
    confirm = PasswordField(
        "Confirm password",
        validators=[
            DataRequired(),
            EqualTo("password", message="Passwords must match"),
        ],
    )


@bp.before_request
def require_recovery() -> None:
    if not password_mode():
        abort(404)
    if not current_app.config["PASSWORD_RECOVERY_ENABLED"]:
        abort(503, description="Password recovery is currently unavailable.")


@bp.after_request
def protect_recovery_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per minute; 20 per hour", methods=["POST"])
def forgot_password():
    form = ForgotPasswordForm()
    sent = False
    if form.validate_on_submit():
        email = form.email.data.strip()
        mailer = current_app.extensions["recovery_mailer"]
        mailer.submit(lambda: mailer.request_reset(email))
        sent = True
    return render_template(
        "forgot_password.html", form=form, sent=sent, message=GENERIC_MESSAGE
    )


@bp.route("/reset-password", methods=["GET", "POST"])
@limiter.limit("10 per minute; 50 per hour", methods=["POST"])
def reset_password():
    form = ResetPasswordForm()
    invalid = False
    if form.validate_on_submit():
        assert form.token.data is not None and form.password.data is not None
        email = db.PasswordResetRepo(current_app.config["DB_PATH"]).consume(
            form.token.data, db.hash_password(form.password.data)
        )
        if email is not None:
            logout_user()
            session.clear()
            mailer = current_app.extensions["recovery_mailer"]
            mailer.submit(lambda: mailer.notify_changed(email))
            flash("Password updated. Sign in with your new password.", "success")
            return redirect(url_for("auth.login"))
        invalid = True
    elif form.is_submitted() and form.token.errors:
        invalid = True
    return render_template("reset_password.html", form=form, invalid=invalid), (
        400 if invalid else 200
    )
