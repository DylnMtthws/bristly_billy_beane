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

    def update_profile(
        self, user_id: str, display_name: str, avatar_emoji: str | None
    ) -> None:
        """Update a user's display name and avatar emoji."""
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE users SET display_name = ?, avatar_emoji = ? WHERE id = ?",
                (display_name, avatar_emoji, user_id),
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


class FavoritesRepo:
    """Per-user favorites for commanders and generated decks."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path

    # --- commanders ---

    def toggle_commander(self, user_id: str, commander_id: str) -> bool:
        """Toggle a commander favorite. Returns the new state (True = favorited)."""
        with connect(self.db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM favorite_commanders WHERE user_id = ? AND commander_id = ?",
                (user_id, commander_id),
            ).fetchone()
            if exists:
                conn.execute(
                    "DELETE FROM favorite_commanders WHERE user_id = ? AND commander_id = ?",
                    (user_id, commander_id),
                )
                conn.commit()
                return False
            conn.execute(
                "INSERT INTO favorite_commanders (user_id, commander_id, created_at) "
                "VALUES (?, ?, ?)",
                (user_id, commander_id, datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
            return True

    def commander_ids(self, user_id: str) -> set[str]:
        """Return the set of commander ids this user has favorited."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT commander_id FROM favorite_commanders WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return {r[0] for r in rows}

    def list_commanders(self, user_id: str) -> list[dict]:
        """Return favorited commanders with card info + current price, newest first."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT c.id, c.name, c.type_line, c.color_identity, c.mana_cost,
                          c.image_uri, cc.price_usd, f.created_at
                   FROM favorite_commanders f
                   JOIN cards c ON c.id = f.commander_id
                   LEFT JOIN commander_candidates cc ON cc.id = c.id
                   WHERE f.user_id = ?
                   ORDER BY f.created_at DESC""",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- decks ---

    def toggle_deck(self, user_id: str, deck_id: str) -> bool:
        """Toggle a deck favorite. Returns the new state (True = favorited)."""
        with connect(self.db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM favorite_decks WHERE user_id = ? AND deck_id = ?",
                (user_id, deck_id),
            ).fetchone()
            if exists:
                conn.execute(
                    "DELETE FROM favorite_decks WHERE user_id = ? AND deck_id = ?",
                    (user_id, deck_id),
                )
                conn.commit()
                return False
            conn.execute(
                "INSERT INTO favorite_decks (user_id, deck_id, created_at) VALUES (?, ?, ?)",
                (user_id, deck_id, datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
            return True

    def deck_ids(self, user_id: str) -> set[str]:
        """Return the set of deck ids this user has favorited."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT deck_id FROM favorite_decks WHERE user_id = ?", (user_id,)
            ).fetchall()
        return {r[0] for r in rows}

    def list_decks(self, user_id: str) -> list[dict]:
        """Return favorited decks (only those still owned/visible to the user)."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT gd.id, gd.deck_name, gd.budget_usd, gd.power_target,
                          gd.estimated_bracket, gd.generated_at, gd.owner_id,
                          c.name AS commander_name, f.created_at AS favorited_at
                   FROM favorite_decks f
                   JOIN generated_decks gd ON gd.id = f.deck_id
                   JOIN cards c ON c.id = gd.commander_id
                   WHERE f.user_id = ?
                   ORDER BY f.created_at DESC""",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]


class DecksRepo:
    """Owner-scoped access to generated decks (privacy + quota counting)."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path

    def list_for_owner(self, user_id: str, *, limit: int | None = None) -> list[dict]:
        """Return decks owned by ``user_id``, newest first."""
        sql = (
            "SELECT gd.id, gd.deck_name, gd.budget_usd, gd.power_target, "
            "gd.strategy, gd.estimated_bracket, gd.cvar_score, gd.generated_at, "
            "c.name AS commander_name "
            "FROM generated_decks gd JOIN cards c ON c.id = gd.commander_id "
            "WHERE gd.owner_id = ? ORDER BY gd.generated_at DESC"
        )
        params: list = [user_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def owner_of(self, deck_id: str) -> str | None:
        """Return the owner id of a deck, or None if the deck/owner is unset."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT owner_id FROM generated_decks WHERE id = ?", (deck_id,)
            ).fetchone()
        return row[0] if row else None

    def set_owner(self, deck_id: str, user_id: str) -> None:
        """Assign a deck's owner (used right after generation)."""
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE generated_decks SET owner_id = ? WHERE id = ?",
                (user_id, deck_id),
            )
            conn.commit()

    def count_this_month(self, user_id: str) -> int:
        """Count decks this user generated in the current calendar month (UTC)."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM generated_decks "
                "WHERE owner_id = ? "
                "AND generated_at >= strftime('%Y-%m-01 00:00:00', 'now')",
                (user_id,),
            ).fetchone()
        return int(row[0]) if row else 0


class FeedbackRepo:
    """Per-user feedback on cards (in a deck) and on decks as a whole.

    Feedback is the Phase-1 deliverable: deck owners rate each card (thumbs +
    comment) and give the deck an overall verdict. One row per (user, deck,
    card) and per (user, deck); writes upsert.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path

    @staticmethod
    def _norm(value: str | None) -> str | None:
        v = (value or "").strip()
        return v or None

    def upsert_card(
        self,
        user_id: str,
        deck_id: str,
        card_id: str,
        card_name: str,
        vote: str | None,
        comment: str | None,
    ) -> None:
        """Insert or update a card's feedback (vote in {up, down, None})."""
        now = datetime.now().isoformat(timespec="seconds")
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO card_feedback
                (id, user_id, deck_id, card_id, card_name, vote, comment,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, deck_id, card_id) DO UPDATE SET
                    vote = excluded.vote,
                    comment = excluded.comment,
                    card_name = excluded.card_name,
                    updated_at = excluded.updated_at""",
                (
                    new_id(), user_id, deck_id, card_id, card_name,
                    self._norm(vote), self._norm(comment), now, now,
                ),
            )
            conn.commit()

    def upsert_deck(
        self, user_id: str, deck_id: str, verdict: str | None, comment: str | None
    ) -> None:
        """Insert or update a deck's overall feedback (verdict + comment)."""
        now = datetime.now().isoformat(timespec="seconds")
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO deck_feedback
                (id, user_id, deck_id, verdict, comment, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, deck_id) DO UPDATE SET
                    verdict = excluded.verdict,
                    comment = excluded.comment,
                    updated_at = excluded.updated_at""",
                (
                    new_id(), user_id, deck_id,
                    self._norm(verdict), self._norm(comment), now, now,
                ),
            )
            conn.commit()

    def card_map(self, user_id: str, deck_id: str) -> dict[str, dict]:
        """Return {card_id: {vote, comment}} for this user's card feedback."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT card_id, vote, comment FROM card_feedback "
                "WHERE user_id = ? AND deck_id = ?",
                (user_id, deck_id),
            ).fetchall()
        return {r["card_id"]: {"vote": r["vote"], "comment": r["comment"]} for r in rows}

    def deck(self, user_id: str, deck_id: str) -> dict | None:
        """Return this user's deck-level feedback ({verdict, comment}) or None."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT verdict, comment FROM deck_feedback "
                "WHERE user_id = ? AND deck_id = ?",
                (user_id, deck_id),
            ).fetchone()
        return dict(row) if row else None


class AdminAnalyticsRepo:
    """Read-only aggregates for the admin portal (P6).

    Feedback aggregates group by ``card_name`` so they survive deck deletion
    (feedback rows are intentionally kept as research data even when a deck is
    removed). Joins to decks/commanders are LEFT joins for the same reason.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path

    def overview(self) -> dict:
        """High-level KPIs for the admin landing page."""
        with connect(self.db_path) as conn:
            def scalar(sql: str) -> float:
                return conn.execute(sql).fetchone()[0]

            status_rows = conn.execute(
                "SELECT status, COUNT(*) n FROM users GROUP BY status"
            ).fetchall()
            return {
                "users_by_status": {r["status"]: r["n"] for r in status_rows},
                "total_users": scalar("SELECT COUNT(*) FROM users"),
                "total_decks": scalar("SELECT COUNT(*) FROM generated_decks"),
                "spend_30d": scalar(
                    "SELECT COALESCE(SUM(cost_usd),0) FROM cost_log "
                    "WHERE timestamp >= datetime('now','-30 days')"
                ),
                "spend_all": scalar("SELECT COALESCE(SUM(cost_usd),0) FROM cost_log"),
                "card_feedback": scalar("SELECT COUNT(*) FROM card_feedback"),
                "deck_feedback": scalar("SELECT COUNT(*) FROM deck_feedback"),
            }

    # --- Feedback ---

    _FB_SORTS = {
        "total_desc": "total DESC, net ASC",
        "net_asc": "net ASC, total DESC",
        "net_desc": "net DESC, total DESC",
        "down_desc": "down DESC, total DESC",
    }

    def card_feedback_aggregate(
        self, sort: str = "total_desc", limit: int = 300
    ) -> list[dict]:
        """Per-card feedback rollup: up/down counts, net, and comment count."""
        order = self._FB_SORTS.get(sort, self._FB_SORTS["total_desc"])
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""SELECT card_name,
                        SUM(CASE WHEN vote='up' THEN 1 ELSE 0 END) AS up,
                        SUM(CASE WHEN vote='down' THEN 1 ELSE 0 END) AS down,
                        COUNT(*) AS total,
                        SUM(CASE WHEN vote='up' THEN 1 WHEN vote='down' THEN -1 ELSE 0 END) AS net,
                        SUM(CASE WHEN comment IS NOT NULL THEN 1 ELSE 0 END) AS comments
                    FROM card_feedback
                    GROUP BY card_name
                    ORDER BY {order}
                    LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def card_comments(self, card_name: str) -> list[dict]:
        """All comments/votes for one card, newest first (for the drill-down)."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT cf.vote, cf.comment, cf.updated_at,
                          u.display_name AS user, cmd.name AS commander, cf.deck_id
                   FROM card_feedback cf
                   LEFT JOIN users u ON u.id = cf.user_id
                   LEFT JOIN generated_decks gd ON gd.id = cf.deck_id
                   LEFT JOIN cards cmd ON cmd.id = gd.commander_id
                   WHERE cf.card_name = ?
                   ORDER BY cf.updated_at DESC""",
                (card_name,),
            ).fetchall()
        return [dict(r) for r in rows]

    def deck_feedback_list(self, limit: int = 200) -> list[dict]:
        """Recent deck verdicts + comments with commander/user context."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT df.deck_id, df.verdict, df.comment, df.updated_at,
                          u.display_name AS user, cmd.name AS commander
                   FROM deck_feedback df
                   LEFT JOIN users u ON u.id = df.user_id
                   LEFT JOIN generated_decks gd ON gd.id = df.deck_id
                   LEFT JOIN cards cmd ON cmd.id = gd.commander_id
                   ORDER BY df.updated_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def export_card_rows(self) -> list[dict]:
        """Flat card-feedback rows for CSV/JSON export."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT u.email AS user, cf.deck_id, cmd.name AS commander,
                          cf.card_name, cf.vote, cf.comment, cf.updated_at
                   FROM card_feedback cf
                   LEFT JOIN users u ON u.id = cf.user_id
                   LEFT JOIN generated_decks gd ON gd.id = cf.deck_id
                   LEFT JOIN cards cmd ON cmd.id = gd.commander_id
                   ORDER BY cf.updated_at DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    def export_deck_rows(self) -> list[dict]:
        """Flat deck-feedback rows for CSV/JSON export."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT u.email AS user, df.deck_id, cmd.name AS commander,
                          df.verdict, df.comment, df.updated_at
                   FROM deck_feedback df
                   LEFT JOIN users u ON u.id = df.user_id
                   LEFT JOIN generated_decks gd ON gd.id = df.deck_id
                   LEFT JOIN cards cmd ON cmd.id = gd.commander_id
                   ORDER BY df.updated_at DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Users / cost ---

    def per_user_stats(self) -> list[dict]:
        """Per-user rollup: decks, spend, feedback counts, last login."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT u.id, u.email, u.display_name, u.role, u.status,
                          u.monthly_deck_quota, u.last_login_at,
                          (SELECT COUNT(*) FROM generated_decks gd WHERE gd.owner_id=u.id) AS decks,
                          (SELECT COALESCE(SUM(cost_usd),0) FROM cost_log cl WHERE cl.user_id=u.id) AS spend,
                          (SELECT COUNT(*) FROM card_feedback cf WHERE cf.user_id=u.id) AS card_fb,
                          (SELECT COUNT(*) FROM deck_feedback dfb WHERE dfb.user_id=u.id) AS deck_fb
                   FROM users u
                   ORDER BY decks DESC, u.created_at DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    def cost_totals(self) -> dict:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd),0) AS all_time, "
                "COALESCE(SUM(CASE WHEN timestamp >= datetime('now','-30 days') "
                "THEN cost_usd ELSE 0 END),0) AS last_30d FROM cost_log"
            ).fetchone()
        return {"all_time": row["all_time"], "last_30d": row["last_30d"]}

    def cost_by_call_type(self) -> list[dict]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT call_type, COUNT(*) AS calls, COALESCE(SUM(cost_usd),0) AS cost, "
                "COALESCE(SUM(input_tokens),0) AS input_tokens, "
                "COALESCE(SUM(output_tokens),0) AS output_tokens "
                "FROM cost_log GROUP BY call_type ORDER BY cost DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def cost_by_user(self) -> list[dict]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT COALESCE(u.display_name, u.email, cl.user_id, '(unattributed)') AS user,
                          COUNT(*) AS calls, COALESCE(SUM(cl.cost_usd),0) AS cost
                   FROM cost_log cl LEFT JOIN users u ON u.id = cl.user_id
                   GROUP BY cl.user_id ORDER BY cost DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Popular commanders ---

    def popular_generated(self, limit: int = 20) -> list[dict]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT cmd.name AS commander, COUNT(*) AS decks
                   FROM generated_decks gd JOIN cards cmd ON cmd.id = gd.commander_id
                   GROUP BY cmd.name ORDER BY decks DESC, commander LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def popular_favorited(self, limit: int = 20) -> list[dict]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT cmd.name AS commander, COUNT(*) AS favorites
                   FROM favorite_commanders f JOIN cards cmd ON cmd.id = f.commander_id
                   GROUP BY cmd.name ORDER BY favorites DESC, commander LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
