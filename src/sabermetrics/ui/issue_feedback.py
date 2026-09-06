"""Authenticated issue reports, independent of card/deck rating feedback."""

import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from importlib.metadata import version
from threading import BoundedSemaphore
from urllib.parse import unquote, urlsplit

from flask import Blueprint, current_app, request
from flask_login import current_user
from flask_wtf.csrf import CSRFError, generate_csrf
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge, TooManyRequests

from sabermetrics.ui.extensions import limiter
from sabermetrics.ui.feedback_images import MAX_IMAGE_BYTES, sanitize_image
from sabermetrics.ui.feedback_linear import (
    CATEGORIES,
    LinearConfig,
    LinearFeedbackClient,
    LinearUnavailable,
    private_asset,
)
from sabermetrics.ui.feedback_store import (
    FeedbackReceipts,
    ReceiptBusy,
    ReceiptConflict,
    ReceiptLimit,
)

bp = Blueprint("issue_feedback", __name__, url_prefix="/feedback")
logger = logging.getLogger(__name__)
SUCCESS = {"ok": True, "message": "Thanks—your feedback was sent."}


def code_block(text: str) -> str:
    """User text remains literal: no Markdown images, mentions or remote fetches."""
    longest = max((len(m[0]) for m in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def page_context(value: str) -> tuple[str, str]:
    try:
        url = urlsplit(value[:2000])
        path = unquote(url.path)
        if (
            url.scheme
            or url.netloc
            or not path.startswith("/")
            or "\\" in path
            or any(ord(c) < 32 for c in path)
        ):
            return "Unavailable", "Unavailable"
        adapter = current_app.url_map.bind("localhost")
        endpoint, _values = adapter.match(path, method="GET")
        if endpoint.split(".")[0] not in {"main", "cedh", "admin"}:
            return "Unavailable", "Unavailable"
        # Only the path is sent; query strings/fragments never reach Linear.
        return path[:500], endpoint.split(".")[-1].replace("_", " ").capitalize()
    except (HTTPException, ValueError):
        return "Unavailable", "Unavailable"


def device_context() -> dict:
    ua = request.user_agent.string[:1000]
    browser = "Unknown"
    for name, pattern in [
        ("Edge", r"Edg(?:A|iOS)?/(\d+)"),
        ("Firefox", r"(?:Firefox|FxiOS)/(\d+)"),
        ("Chrome", r"(?:Chrome|CriOS)/(\d+)"),
        ("Safari", r"Version/(\d+).*Safari/"),
    ]:
        match = re.search(pattern, ua)
        if match:
            browser = f"{name} {match[1]}"
            break
    system = next(
        (
            name
            for name, fragment in [
                ("iOS", "iPhone"),
                ("iPadOS", "iPad"),
                ("Android", "Android"),
                ("Windows", "Windows"),
                ("macOS", "Macintosh"),
                ("Linux", "Linux"),
            ]
            if fragment in ua
        ),
        "Unknown",
    )
    viewport = []
    for key in ("viewport_width", "viewport_height"):
        value = request.form.get(key, "")
        viewport.append(
            str(min(20000, max(1, int(value))))
            if value.isdecimal() and len(value) < 6
            else "unknown"
        )
    return {"browser": browser, "os": system, "viewport": " × ".join(viewport)}


def init_feedback(app) -> None:
    config = LinearConfig.from_env()
    app.config["LINEAR_FEEDBACK_ENABLED"] = config is not None
    app.extensions["feedback_slots"] = BoundedSemaphore(1)
    if config:
        app.extensions["linear_feedback"] = LinearFeedbackClient(config)

    # Register before CSRF's hook, which parses multipart forms. Upload limits
    # and auth must take effect before accepting any image bytes.
    @app.before_request
    def feedback_request_limits():
        if request.endpoint not in {
            "issue_feedback.submit",
            "issue_feedback.session_token",
        }:
            return None
        request.max_content_length = MAX_IMAGE_BYTES + 65536
        # Werkzeug buffers multipart chunks of 64 KiB, including image data.
        # A smaller limit rejects valid large files before they can spool.
        request.max_form_memory_size = 131072
        request.max_form_parts = 12
        if not current_user.is_authenticated or not current_user.is_active:
            return {"error": "Please sign in before sending feedback."}, 401
        if not app.config["LINEAR_FEEDBACK_ENABLED"]:
            return {"error": "Feedback is currently unavailable."}, 503
        return None

    app.register_blueprint(bp)


@bp.errorhandler(CSRFError)
def csrf_error(error):
    return {
        "error": "Your session has expired. Sign in again before retrying.",
        "code": "session_expired",
    }, 400


@bp.get("/session")
def session_token():
    # Same-origin only; a new tab may have renewed the login cookie while the
    # original tab retained its report. Never put the token into a URL.
    return {"csrf_token": generate_csrf()}, 200, {"Cache-Control": "no-store"}


@bp.errorhandler(RequestEntityTooLarge)
def too_large(error):
    return {"error": "Choose one image up to 10 MB and a shorter description."}, 413


@bp.errorhandler(TooManyRequests)
def too_many(error):
    return {"error": "Too many attempts. Please wait before trying again."}, 429


@bp.post("/submit")
@limiter.limit("5 per minute; 10 per hour", key_func=lambda: str(current_user.id))
def submit():
    if any(len(request.form.getlist(key)) != 1 for key in request.form):
        return {"error": "Invalid feedback form."}, 400
    category = request.form.get("category", "")
    description = request.form.get("description", "").strip()
    if (
        category not in CATEGORIES
        or not 10 <= len(description) <= 5000
        or "\x00" in description
    ):
        return {
            "error": "Choose a category and describe the issue in 10–5,000 characters."
        }, 400
    try:
        request_id = str(uuid.UUID(request.form.get("submission_id", ""), version=4))
    except ValueError:
        return {"error": "Please reopen the feedback form and try again."}, 400
    files = list(request.files.items(multi=True))
    if len(files) > 1 or (files and files[0][0] != "screenshot"):
        return {"error": "Attach only one screenshot."}, 400
    slots = current_app.extensions["feedback_slots"]
    if not slots.acquire(blocking=False):
        return {
            "error": "Another report is being processed. Please retry shortly."
        }, 429
    receipts = FeedbackReceipts(current_app.config["DB_PATH"])
    claimed = False
    sent = False
    try:
        screenshot = sanitize_image(files[0][1]) if files else None
        path, page = page_context(request.form.get("page_path", ""))
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    category,
                    description,
                    path,
                    (
                        hashlib.sha256(screenshot.content).hexdigest()
                        if screenshot
                        else None
                    ),
                ],
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        row = receipts.claim(request_id, str(current_user.id), fingerprint)
        if row["status"] == "sent":
            return SUCCESS
        claimed = True
        client = current_app.extensions["linear_feedback"]
        if row["attempted"] and client.exists(row["issue_id"]):
            sent = True
            return SUCCESS
        asset = row["asset_url"]
        if screenshot and not asset:
            asset = client.upload(screenshot, row["issue_id"])
            receipts.asset(request_id, asset)
        context = {
            "category": CATEGORIES[category],
            "reporter": current_user.display_name,
            "email": current_user.email,
            "page": page,
            "path": path,
            "reported_at": datetime.now(UTC).isoformat(),
            "app_version": version("sabermetrics"),
            "build": os.environ.get("SABER_BUILD_SHA", "unknown")[:80],
            **device_context(),
        }
        body = "## User report\n\n" + code_block(description)
        body += "\n\n## Context\n\n" + code_block(
            json.dumps(context, indent=2, ensure_ascii=False)
        )
        if asset:
            body += f"\n\n## Screenshot\n\n![User screenshot]({private_asset(asset)})"
        title = f"[{CATEGORIES[category]}] " + " ".join(description.split())[:140]
        receipts.attempting(request_id)
        client.create(row["issue_id"], category, title, body)
        sent = True
        return SUCCESS
    except ValueError as exc:
        return {"error": str(exc)}, 400
    except ReceiptConflict:
        return {
            "error": "This submission has changed. Restore the original report to retry."
        }, 409
    except ReceiptBusy:
        return {
            "error": "This report is still being processed. Please retry shortly."
        }, 409
    except ReceiptLimit:
        return {
            "error": "You've reached today's feedback limit. Please try again tomorrow."
        }, 429
    except (LinearUnavailable, sqlite3.Error):
        logger.warning("Feedback delivery could not be confirmed")
        return {
            "error": "We couldn't confirm delivery. Your report is still here; retry to check and send it safely.",
            "retry_same": True,
        }, 503
    finally:
        try:
            if claimed:
                receipts.finish(request_id, sent)
        except sqlite3.Error:
            logger.warning("Feedback receipt could not be updated")
        slots.release()
