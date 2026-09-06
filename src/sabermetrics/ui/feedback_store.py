"""Durable submission identity without retaining feedback contents."""

import time
import uuid
from pathlib import Path

from sabermetrics import db


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
