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
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sabermetrics.models.card import Card


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
