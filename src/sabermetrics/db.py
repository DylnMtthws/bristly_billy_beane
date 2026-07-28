"""Central SQLite access layer.

A single place that opens connections (so connection configuration is
consistent), plus thin repositories for the most-duplicated query shapes and a
helper for hydrating Pydantic models from rows. This replaces the pattern of
each module calling ``sqlite3.connect()`` directly with its own ad-hoc setup.

Connection policy (deliberately behavior-preserving):

- ``row_factory`` defaults to :class:`sqlite3.Row`. A ``Row`` supports positional
  (``row[0]``), keyed (``row["col"]``), iteration, and ``dict(row)`` access, so
  it is a safe superset of what existing call sites expect.
- ``foreign_keys`` is intentionally **not** forced on. The schema is created with
  foreign keys enabled (``scripts/setup_db.py``), but application connections
  have historically run with SQLite's per-connection default (off). Turning it on
  globally here could reject inserts that currently succeed, so it stays opt-in.
- WAL journal mode is a persistent property of the database file, already set at
  setup time; no per-connection pragma is needed.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

from sabermetrics.models.card import Card

# --- Password hashing (argon2id) -----------------------------------------

_PASSWORD_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    """Return an argon2id hash for ``password``."""
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Return True iff ``password`` matches ``password_hash``.

    Never raises: a missing hash or any argon2 verification error yields False.
    """
    if not password_hash:
        return False
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except Argon2Error:
        return False


def new_id() -> str:
    """Return a random hex id for a user/feedback row."""
    return uuid.uuid4().hex


def new_token() -> str:
    """Return a URL-safe single-use invite token."""
    return secrets.token_urlsafe(32)


@contextmanager
def connect(
    db_path: str | Path,
    *,
    row_factory: bool = True,
    foreign_keys: bool = False,
) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with consistent configuration.

    The connection is closed when the context exits. Changes are **not**
    auto-committed; callers commit explicitly, matching prior behavior.

    Args:
        db_path: Path to the SQLite database file.
        row_factory: If True (default), set ``row_factory`` to
            :class:`sqlite3.Row`.
        foreign_keys: If True, enable ``PRAGMA foreign_keys`` for this
            connection. Defaults to False to preserve historical behavior.

    Yields:
        An open :class:`sqlite3.Connection`.
    """
    conn = sqlite3.connect(str(db_path))
    if row_factory:
        conn.row_factory = sqlite3.Row
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def row_to_card(row: sqlite3.Row | dict, *, price_usd: float | None = None) -> Card:
    """Hydrate a :class:`Card` from a ``cards`` table row.

    Parses the JSON-encoded ``color_identity`` and ``keywords`` columns and
    optionally attaches a current price. Centralizes the row→model mapping that
    was previously duplicated across modules.

    Args:
        row: A ``cards`` row as a :class:`sqlite3.Row` or dict. Must contain the
            standard card columns.
        price_usd: Optional current price to attach as ``current_price_usd``.
            If omitted, falls back to a ``current_price_usd`` key on the row, if
            present.

    Returns:
        A populated :class:`Card`.
    """
    d = dict(row)
    for field in ("color_identity", "keywords"):
        val = d.get(field, "[]")
        if isinstance(val, str):
            d[field] = json.loads(val) if val else []
        elif val is None:
            d[field] = []

    price = price_usd if price_usd is not None else d.get("current_price_usd")

    return Card(
        id=d["id"],
        oracle_id=d["oracle_id"],
        name=d["name"],
        mana_cost=d.get("mana_cost"),
        cmc=d["cmc"],
        type_line=d["type_line"],
        oracle_text=d.get("oracle_text"),
        color_identity=d["color_identity"],
        keywords=d.get("keywords", []),
        is_legal_commander=bool(d.get("is_legal_commander", False)),
        is_legal_in_99=bool(d.get("is_legal_in_99", True)),
        set_code=d["set_code"],
        rarity=d["rarity"],
        image_uri=d.get("image_uri"),
        last_updated=d.get("last_updated") or datetime.now(),
        current_price_usd=price,
    )


class SourceHealthRepo:
    """Read/write access to the ``source_health`` table.

    Centralizes every ``source_health`` query that was previously copy-pasted
    across the ingestion sources and the health monitor.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path

    def last_successful_sync(self, source: str) -> datetime | None:
        """Return when ``source`` last synced successfully, or None."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT last_successful_sync FROM source_health WHERE source = ?",
                (source,),
            ).fetchone()
        if row and row[0]:
            return datetime.fromisoformat(row[0])
        return None

    def record(self, source: str, success: bool, error: str | None = None) -> None:
        """Record a sync outcome for ``source``.

        On success the row is replaced with a fresh successful timestamp and
        ``consecutive_failures`` reset to 0. On failure the failure timestamp and
        error are recorded and ``consecutive_failures`` is incremented.
        """
        now = datetime.now().isoformat()
        with connect(self.db_path) as conn:
            if success:
                conn.execute(
                    """INSERT OR REPLACE INTO source_health
                    (source, last_successful_sync, consecutive_failures)
                    VALUES (?, ?, 0)""",
                    (source, now),
                )
            else:
                conn.execute(
                    """INSERT INTO source_health
                    (source, last_failed_sync, last_error, consecutive_failures)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(source) DO UPDATE SET
                        last_failed_sync = excluded.last_failed_sync,
                        last_error = excluded.last_error,
                        consecutive_failures = consecutive_failures + 1""",
                    (source, now, error),
                )
            conn.commit()

    def get(self, source: str) -> dict | None:
        """Return the full health record for ``source``, or None."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM source_health WHERE source = ?",
                (source,),
            ).fetchone()
        return dict(row) if row else None

    def get_all(self) -> list[dict]:
        """Return all health records, ordered by source name."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM source_health ORDER BY source"
            ).fetchall()
        return [dict(row) for row in rows]


# Default per-user monthly deck quota when a user's own quota is NULL.
DEFAULT_MONTHLY_DECK_QUOTA = 20


class UsersRepo:
    """Read/write access to the ``users`` table.

    Rows are returned as plain dicts (matching :meth:`SourceHealthRepo.get`);
    the Flask-Login wrapper lives in the UI layer.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path

    def create(
        self,
        *,
        email: str,
        display_name: str | None = None,
        role: str = "user",
        status: str = "invited",
        password_hash: str | None = None,
        avatar_emoji: str | None = None,
        invited_by: str | None = None,
        monthly_deck_quota: int | None = None,
        user_id: str | None = None,
    ) -> str:
        """Insert a new user and return its id.

        Raises:
            sqlite3.IntegrityError: if ``email`` is already taken.
        """
        uid = user_id or new_id()
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO users
                (id, email, display_name, avatar_emoji, password_hash, role,
                 status, monthly_deck_quota, invited_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uid,
                    email,
                    display_name,
                    avatar_emoji,
                    password_hash,
                    role,
                    status,
                    monthly_deck_quota,
                    invited_by,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
        return uid

    def get(self, user_id: str) -> dict | None:
        """Return the user row for ``user_id``, or None."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_by_email(self, email: str) -> dict | None:
        """Return the user row for ``email`` (case-insensitive), or None."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
            ).fetchone()
        return dict(row) if row else None

    def activate_with_password(
        self,
        user_id: str,
        password_hash: str,
        *,
        display_name: str | None = None,
        avatar_emoji: str | None = None,
    ) -> None:
        """Set a user's password + profile and mark them active.

        Used by the invite-acceptance flow. ``display_name``/``avatar_emoji``
        are only written when provided (COALESCE keeps existing values).
        """
        with connect(self.db_path) as conn:
            conn.execute(
                """UPDATE users SET
                    password_hash = ?,
                    display_name = COALESCE(?, display_name),
                    avatar_emoji = COALESCE(?, avatar_emoji),
                    status = 'active'
                WHERE id = ?""",
                (password_hash, display_name, avatar_emoji, user_id),
            )
            conn.commit()

    def set_password(self, user_id: str, password_hash: str) -> None:
        """Replace a user's password hash."""
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (password_hash, user_id),
            )
            conn.commit()

    def set_status(self, user_id: str, status: str) -> None:
        """Set a user's status (``invited`` | ``active`` | ``disabled``)."""
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE users SET status = ? WHERE id = ?", (status, user_id)
            )
            conn.commit()

    def set_quota(self, user_id: str, quota: int | None) -> None:
        """Override a user's monthly deck quota (None = use the global default)."""
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE users SET monthly_deck_quota = ? WHERE id = ?",
                (quota, user_id),
            )
            conn.commit()

    def touch_login(self, user_id: str) -> None:
        """Record a successful login timestamp."""
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), user_id),
            )
            conn.commit()

    def list_all(self) -> list[dict]:
        """Return all users, newest first."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def count_by_status(self) -> dict[str, int]:
        """Return a ``{status: count}`` map across all users."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM users GROUP BY status"
            ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    def backfill_deck_owner(self, user_id: str) -> int:
        """Assign ``user_id`` as owner of any decks lacking an owner.

        Returns the number of rows updated. Idempotent.
        """
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE generated_decks SET owner_id = ? WHERE owner_id IS NULL",
                (user_id,),
            )
            conn.commit()
            return cur.rowcount


class InviteRepo:
    """Single-use, expiring invite tokens tied to a ``users`` row."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path

    def create(self, user_id: str, *, ttl_days: int = 7) -> str:
        """Create an invite token for ``user_id`` and return it."""
        token = new_token()
        expires = (datetime.now() + timedelta(days=ttl_days)).isoformat(
            timespec="seconds"
        )
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO invite_tokens (token, user_id, expires_at, created_at)
                VALUES (?, ?, ?, ?)""",
                (token, user_id, expires, datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        return token

    def get(self, token: str) -> dict | None:
        """Return the invite row for ``token``, or None."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM invite_tokens WHERE token = ?", (token,)
            ).fetchone()
        return dict(row) if row else None

    def get_valid(self, token: str) -> dict | None:
        """Return the invite row only if it is unused and unexpired, else None."""
        row = self.get(token)
        if row is None or row.get("used_at"):
            return None
        expires_at = row.get("expires_at")
        if expires_at and datetime.fromisoformat(expires_at) < datetime.now():
            return None
        return row

    def mark_used(self, token: str) -> None:
        """Mark an invite token as consumed."""
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE invite_tokens SET used_at = ? WHERE token = ?",
                (datetime.now().isoformat(timespec="seconds"), token),
            )
            conn.commit()
