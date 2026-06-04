"""Generation trace logging for per-card decision history.

Records what happened to each card at every pipeline stage, enabling
diagnosis of why specific cards were included, excluded, or swapped.

Volume is controlled via a watchlist: only auto-include staples,
explicitly requested cards, and all swap/replace actions emit events.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# DDL for the trace table — also in scripts/setup_db.py but duplicated
# here so flush() can self-create the table if needed.
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS generation_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id TEXT NOT NULL,
    card_name TEXT NOT NULL,
    card_id TEXT,
    stage TEXT NOT NULL,
    action TEXT NOT NULL,
    score REAL,
    score_components_json TEXT,
    reason TEXT,
    timestamp REAL
)
"""
_CREATE_IDX_GEN = (
    "CREATE INDEX IF NOT EXISTS idx_traces_gen "
    "ON generation_traces(generation_id)"
)
_CREATE_IDX_CARD = (
    "CREATE INDEX IF NOT EXISTS idx_traces_card "
    "ON generation_traces(card_name)"
)


class TraceEvent(BaseModel):
    """A single trace event for one card at one pipeline stage."""

    card_name: str
    card_id: str | None = None
    stage: str
    action: str
    score: float | None = None
    score_components: dict | None = None
    reason: str = ""
    timestamp: float = Field(default_factory=time.time)


class GenerationTracer:
    """Accumulates trace events in-memory during a pipeline run.

    Args:
        generation_id: Identifier for this generation (updated before flush).
        watchlist: Set of card names that emit events in high-volume stages.
    """

    def __init__(
        self,
        generation_id: str,
        watchlist: set[str] | None = None,
    ) -> None:
        self.generation_id = generation_id
        self.watchlist: set[str] = watchlist or set()
        self._events: list[TraceEvent] = []

    def record(
        self,
        card_name: str,
        stage: str,
        action: str,
        *,
        card_id: str | None = None,
        score: float | None = None,
        score_components: dict | None = None,
        reason: str = "",
        force: bool = False,
    ) -> None:
        """Append a trace event.

        In high-volume stages, only watchlisted cards emit events unless
        ``force=True``.  Low-volume stages (swap_refine, llm_safety,
        budget_redist) should pass ``force=True`` so all swap/replace
        actions are captured regardless of watchlist.

        Args:
            card_name: Name of the card.
            stage: Pipeline stage identifier.
            action: What happened (placed, rejected, protected, etc.).
            card_id: Optional Scryfall card ID.
            score: Numeric score if applicable.
            score_components: Dict of score breakdown.
            reason: Human-readable explanation.
            force: If True, bypass watchlist filtering.
        """
        if not force and card_name not in self.watchlist:
            return

        self._events.append(TraceEvent(
            card_name=card_name,
            card_id=card_id,
            stage=stage,
            action=action,
            score=score,
            score_components=score_components,
            reason=reason,
        ))

    def set_generation_id(self, generation_id: str) -> None:
        """Update the generation ID (called once the real deck ID is known)."""
        self.generation_id = generation_id

    @property
    def events(self) -> list[TraceEvent]:
        """Return accumulated events (read-only access)."""
        return list(self._events)

    def flush(self, db_path: Path | str) -> int:
        """Batch INSERT all events into the generation_traces table.

        Creates the table if it doesn't exist.

        Args:
            db_path: Path to the SQLite database.

        Returns:
            Number of events written.
        """
        if not self._events:
            return 0

        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(_CREATE_TABLE)
            conn.execute(_CREATE_IDX_GEN)
            conn.execute(_CREATE_IDX_CARD)

            rows = [
                (
                    self.generation_id,
                    e.card_name,
                    e.card_id,
                    e.stage,
                    e.action,
                    e.score,
                    json.dumps(e.score_components) if e.score_components else None,
                    e.reason,
                    e.timestamp,
                )
                for e in self._events
            ]
            conn.executemany(
                "INSERT INTO generation_traces "
                "(generation_id, card_name, card_id, stage, action, "
                "score, score_components_json, reason, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
            count = len(rows)
            logger.info(
                "Flushed %d trace events for generation %s",
                count, self.generation_id,
            )
            return count
        finally:
            conn.close()


def get_trace(
    db_path: Path | str,
    generation_id: str,
    card_names: list[str] | None = None,
) -> pd.DataFrame:
    """Query trace events for a generation.

    Args:
        db_path: Path to the SQLite database.
        generation_id: The deck generation ID to query.
        card_names: Optional list of card names to filter to.

    Returns:
        DataFrame with columns: card_name, card_id, stage, action,
        score, score_components_json, reason, timestamp.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        query = (
            "SELECT card_name, card_id, stage, action, score, "
            "score_components_json, reason, timestamp "
            "FROM generation_traces "
            "WHERE generation_id = ?"
        )
        params: list = [generation_id]

        if card_names:
            placeholders = ",".join("?" * len(card_names))
            query += f" AND card_name IN ({placeholders})"
            params.extend(card_names)

        query += " ORDER BY timestamp ASC"

        df = pd.read_sql_query(query, conn, params=params)
        return df
    finally:
        conn.close()
