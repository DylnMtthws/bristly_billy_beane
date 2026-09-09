"""Persist a mechanic tag build, and record what produced it.

Two tables, and the second one is the reason this module exists rather than a
lone ``INSERT``:

``card_mechanic_tag`` holds the rows the plan specifies — oracle id, tag id, tag
version, confidence, matched span, snapshot hash.

``mechanic_tag_build`` holds the build that wrote them: which corpus, which tag
library, what the output hashed to, and the coverage figures. Without it, a
reader of the tag table can see 40 cards carrying ``cost:phyrexian_mana`` and
cannot tell whether that is the current library's answer or a stale one, nor
what the library failed to reach. Coverage stored only in a terminal scrollback
is coverage nobody will look at again.

A rebuild **replaces** the tag table's contents inside one transaction. A tag
corpus is derived data with no history worth keeping: a card that stopped
matching should stop being reported, and merging a new build into an old one
would leave rows asserting a predicate that no longer holds.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sabermetrics.substrate.tagging import TagBuild, TagRow

TAG_TABLE = "card_mechanic_tag"
BUILD_TABLE = "mechanic_tag_build"

SCHEMA = (
    f"""
    CREATE TABLE IF NOT EXISTS {TAG_TABLE} (
        oracle_id     TEXT NOT NULL,
        tag_id        TEXT NOT NULL,
        tag_version   TEXT NOT NULL,
        confidence    REAL NOT NULL,
        matched_span  TEXT NOT NULL,
        snapshot_hash TEXT NOT NULL,
        PRIMARY KEY (oracle_id, tag_id)
    )
    """,
    f"CREATE INDEX IF NOT EXISTS idx_mechanic_tag_tag ON {TAG_TABLE}(tag_id)",
    f"""
    CREATE INDEX IF NOT EXISTS idx_mechanic_tag_snapshot
        ON {TAG_TABLE}(snapshot_hash)
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {BUILD_TABLE} (
        content_sha256   TEXT PRIMARY KEY,
        library_sha256   TEXT NOT NULL,
        snapshot_hash    TEXT NOT NULL,
        source_view      TEXT NOT NULL,
        corpus_row_count INTEGER,
        tag_count        INTEGER NOT NULL,
        row_count        INTEGER NOT NULL,
        cards_tagged     INTEGER NOT NULL,
        cards_untagged   INTEGER NOT NULL,
        coverage_json    TEXT NOT NULL,
        built_at         TEXT NOT NULL
    )
    """,
)


@contextmanager
def _connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_schema(db_path: str | Path) -> None:
    """Create both tables if they are absent. Idempotent."""
    with _connect(db_path) as conn:
        for statement in SCHEMA:
            conn.execute(statement)


def write_build(
    db_path: str | Path, result: TagBuild, *, built_at: str | None = None
) -> int:
    """Replace the tag corpus with ``result``, and record the build.

    Args:
        db_path: SQLite database.
        result: The build to persist.
        built_at: ISO timestamp; defaults to now, UTC. Injectable so a test can
            assert the whole write is deterministic.

    Returns:
        Number of tag rows written.
    """
    stamp = built_at or datetime.now(UTC).isoformat()
    ensure_schema(db_path)
    coverage = {
        "total_cards": result.coverage.total_cards,
        "tagged_cards": result.coverage.tagged_cards,
        "untagged_by_type": [list(pair) for pair in result.coverage.untagged_by_type],
        "tag_counts": [list(pair) for pair in result.coverage.tag_counts],
        "family_counts": [list(pair) for pair in result.coverage.family_counts],
    }
    with _connect(db_path) as conn:
        conn.execute(f"DELETE FROM {TAG_TABLE}")
        conn.executemany(
            f"INSERT INTO {TAG_TABLE} (oracle_id, tag_id, tag_version, confidence, "
            "matched_span, snapshot_hash) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    row.oracle_id,
                    row.tag_id,
                    row.tag_version,
                    row.confidence,
                    row.matched_span,
                    row.snapshot_hash,
                )
                for row in result.rows
            ],
        )
        conn.execute(
            f"INSERT OR REPLACE INTO {BUILD_TABLE} (content_sha256, library_sha256, "
            "snapshot_hash, source_view, corpus_row_count, tag_count, row_count, "
            "cards_tagged, cards_untagged, coverage_json, built_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result.content_sha256,
                result.library_sha256,
                result.snapshot.sha256(),
                result.snapshot.source_view,
                result.snapshot.row_count,
                len(result.tag_ids),
                len(result.rows),
                result.coverage.tagged_cards,
                result.coverage.untagged_cards,
                json.dumps(coverage, sort_keys=True),
                stamp,
            ),
        )
    return len(result.rows)


def read_rows(db_path: str | Path, *, tag_id: str | None = None) -> list[TagRow]:
    """Read tag rows back, optionally for one tag, in a stable order."""
    sql = (
        f"SELECT oracle_id, tag_id, tag_version, confidence, matched_span, "
        f"snapshot_hash FROM {TAG_TABLE}"
    )
    params: tuple[str, ...] = ()
    if tag_id:
        sql += " WHERE tag_id = ?"
        params = (tag_id,)
    sql += " ORDER BY oracle_id, tag_id"
    with _connect(db_path) as conn:
        return [
            TagRow(
                oracle_id=row["oracle_id"],
                tag_id=row["tag_id"],
                tag_version=row["tag_version"],
                confidence=row["confidence"],
                matched_span=row["matched_span"],
                snapshot_hash=row["snapshot_hash"],
            )
            for row in conn.execute(sql, params)
        ]


def latest_build(db_path: str | Path) -> dict[str, object] | None:
    """The most recent recorded build, or ``None`` if none has run.

    ``None`` is the honest answer for an unbuilt database and callers are
    expected to render it as "not built" rather than as an empty tag corpus,
    which reads like a corpus in which nothing matched.
    """
    ensure_schema(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT * FROM {BUILD_TABLE} ORDER BY built_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None
