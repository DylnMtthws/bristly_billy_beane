"""Parameterized detection engine shared by the candidate detectors.

This replaces three near-identical copies of the same detect/populate logic
that previously lived in ``ramp_detector``, ``removal_detector``, and
``protection_detector``. Each detector now declares a :class:`Detector` spec
and delegates to :func:`run_detect` / :func:`populate_candidates` here.

Behavior is intentionally identical to the prior per-module implementations:
negative patterns are checked against the *original* oracle text, positive
patterns against the reminder-stripped text, and population is version-gated
with the same skip/clear/batch-insert semantics.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def strip_reminder_text(oracle: str) -> str:
    """Remove parenthetical reminder text from oracle text.

    Args:
        oracle: Raw oracle text.

    Returns:
        Oracle text with all parenthetical expressions removed.
    """
    return re.sub(r"\([^)]*\)", "", oracle)


@dataclass(frozen=True)
class Detector:
    """Specification for one oracle-text candidate detector.

    Attributes:
        name: Human label used in log messages (e.g. ``"ramp"``).
        table: Name of the ``*_candidates`` table to populate.
        detection_version: Version string; bump to force recomputation.
        positive_patterns: ``(label, compiled regex)`` pairs matched against
            the reminder-stripped oracle text. The card qualifies if any match.
        negative_patterns: Compiled regexes matched against the *original*
            oracle text. Any match disqualifies the card.
        extract: Given ``(card, oracle_stripped)``, returns the metadata dict
            for a qualifying card. Keys must include every name in ``columns``.
        columns: Result-dict keys, in the column order of the INSERT statement
            (excluding ``card_id`` and ``detection_version``, added by the
            engine).
        create_table_sql: DDL creating the candidates table if absent.
        index_sql: DDL creating the score index if absent.
    """

    name: str
    table: str
    detection_version: str
    positive_patterns: list[tuple[str, re.Pattern]]
    negative_patterns: list[re.Pattern]
    extract: Callable[[dict, str], dict]
    columns: list[str]
    create_table_sql: str
    index_sql: str


def run_detect(detector: Detector, card: dict) -> dict | None:
    """Detect whether a card qualifies and return its metadata.

    Strips parenthetical reminder text before positive matching to avoid
    false positives from keyword reminder text. Negative patterns are checked
    against the original (unstripped) text.

    Args:
        detector: The detector specification to apply.
        card: Card dict with ``oracle_text``, ``type_line``, ``cmc`` keys.

    Returns:
        The metadata dict if the card qualifies, ``None`` otherwise.
    """
    oracle = card.get("oracle_text") or ""
    oracle_stripped = strip_reminder_text(oracle)

    for neg_pat in detector.negative_patterns:
        if neg_pat.search(oracle):
            return None

    for _label, pat in detector.positive_patterns:
        if pat.search(oracle_stripped):
            break
    else:
        return None

    return detector.extract(card, oracle_stripped)


def populate_candidates(detector: Detector, db_path: Path) -> dict:
    """Scan all Commander-legal cards and populate the candidates table.

    Args:
        detector: The detector specification to apply.
        db_path: Path to the SQLite database.

    Returns:
        Dict with population statistics (``rows``, ``skipped``, ``version``,
        ``duration_seconds``).
    """
    start = time.time()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        conn.execute(detector.create_table_sql)
        conn.execute(detector.index_sql)
        conn.commit()

        # Skip if already populated at the current detection version.
        try:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {detector.table} "
                "WHERE detection_version = ?",
                (detector.detection_version,),
            ).fetchone()
            if row and row[0] > 0:
                logger.info(
                    "%s already populated at version %s (%d rows)",
                    detector.table,
                    detector.detection_version,
                    row[0],
                )
                return {
                    "rows": row[0],
                    "skipped": True,
                    "version": detector.detection_version,
                    "duration_seconds": 0.0,
                }
        except sqlite3.OperationalError:
            pass  # Table doesn't exist yet

        cursor = conn.execute(
            "SELECT id, name, oracle_text, type_line, cmc "
            "FROM cards "
            "WHERE is_legal_in_99 = 1"
        )
        cards = [dict(row) for row in cursor.fetchall()]
        logger.info(
            "Scanning %d Commander-legal cards for %s detection",
            len(cards),
            detector.name,
        )

        # Clear previous version data.
        conn.execute(
            f"DELETE FROM {detector.table} WHERE detection_version != ?",
            (detector.detection_version,),
        )

        inserts: list[tuple] = []
        for card in cards:
            result = run_detect(detector, card)
            if result is not None:
                inserts.append(
                    (
                        card["id"],
                        *(result[col] for col in detector.columns),
                        detector.detection_version,
                    )
                )

        col_list = ", ".join(["card_id", *detector.columns, "detection_version"])
        placeholders = ", ".join(["?"] * (len(detector.columns) + 2))
        conn.executemany(
            f"INSERT OR REPLACE INTO {detector.table} ({col_list}) "
            f"VALUES ({placeholders})",
            inserts,
        )
        conn.commit()

        duration = time.time() - start
        logger.info(
            "Populated %s: %d cards in %.1fs",
            detector.table,
            len(inserts),
            duration,
        )

        return {
            "rows": len(inserts),
            "skipped": False,
            "version": detector.detection_version,
            "duration_seconds": round(duration, 2),
        }
    finally:
        conn.close()
