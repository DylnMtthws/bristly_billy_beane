"""Durable feedback receipts and isolated-preview report storage."""

import json
import time
import uuid
from pathlib import Path

from sabermetrics import db
from sabermetrics.ui.feedback_images import FeedbackImage


class ReceiptConflict(Exception):
    pass


class ReceiptBusy(Exception):
    pass


class ReceiptLimit(Exception):
    pass


class FeedbackReceipts:
    def __init__(self, path: Path):
        self.path = path

    def claim(self, request_id: str, user_id: str, fingerprint: str) -> dict:
        now = time.time()
        with db.connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM issue_feedback_receipts WHERE id = ?", (request_id,)
            ).fetchone()
            if row:
                if row["user_id"] != user_id or row["fingerprint"] != fingerprint:
                    raise ReceiptConflict
                if row["status"] == "sent":
                    return dict(row)
                if row["status"] == "sending" and row["lease_until"] > now:
                    raise ReceiptBusy
                conn.execute(
                    "UPDATE issue_feedback_receipts SET status='sending', lease_until=? WHERE id=?",
                    (now + 180, request_id),
                )
                conn.commit()
                return dict(row)
            count = conn.execute(
                "SELECT COUNT(*) FROM issue_feedback_receipts WHERE user_id=? AND created_at>?",
                (user_id, now - 86400),
            ).fetchone()[0]
            if count >= 20:
                raise ReceiptLimit
            issue_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO issue_feedback_receipts "
                "(id,user_id,fingerprint,issue_id,created_at,lease_until,status) "
                "VALUES (?,?,?,?,?,?,'sending')",
                (request_id, user_id, fingerprint, issue_id, now, now + 180),
            )
            conn.commit()
            return {
                "id": request_id,
                "issue_id": issue_id,
                "status": "sending",
                "attempted": 0,
                "asset_url": None,
            }

    def asset(self, request_id: str, url: str) -> None:
        with db.connect(self.path) as conn:
            conn.execute(
                "UPDATE issue_feedback_receipts SET asset_url=? WHERE id=?",
                (url, request_id),
            )
            conn.commit()

    def attempting(self, request_id: str) -> None:
        # Persist BEFORE the request. A crash after Linear commits is reconciled
        # with this same server-generated issue UUID on the next retry.
        with db.connect(self.path) as conn:
            conn.execute(
                "UPDATE issue_feedback_receipts SET attempted=1 WHERE id=?",
                (request_id,),
            )
            conn.commit()

    def finish(self, request_id: str, sent: bool) -> None:
        with db.connect(self.path) as conn:
            conn.execute(
                "UPDATE issue_feedback_receipts SET status=?,lease_until=0 WHERE id=?",
                ("sent" if sent else "retry", request_id),
            )
            conn.commit()


class LocalFeedbackStore:
    """Keep dev-preview reports in its disposable SQLite database."""

    def __init__(self, path: Path):
        self.path = path
        with db.connect(path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS deck_lab_dev_feedback (
                    issue_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    report_text TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    screenshot BLOB,
                    screenshot_mime TEXT,
                    created_at REAL NOT NULL
                )
                """)
            conn.commit()

    def exists(self, issue_id: str) -> bool:
        with db.connect(self.path) as conn:
            row = conn.execute(
                "SELECT 1 FROM deck_lab_dev_feedback WHERE issue_id=?", (issue_id,)
            ).fetchone()
        return row is not None

    def create(
        self,
        *,
        issue_id: str,
        request_id: str,
        user_id: str,
        category: str,
        title: str,
        report_text: str,
        context: dict,
        screenshot: FeedbackImage | None,
    ) -> None:
        with db.connect(self.path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO deck_lab_dev_feedback (
                    issue_id, request_id, user_id, category, title, report_text,
                    context_json, screenshot, screenshot_mime, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    issue_id,
                    request_id,
                    user_id,
                    category,
                    title,
                    report_text,
                    json.dumps(context, ensure_ascii=False),
                    screenshot.content if screenshot else None,
                    screenshot.content_type if screenshot else None,
                    time.time(),
                ),
            )
            conn.commit()
