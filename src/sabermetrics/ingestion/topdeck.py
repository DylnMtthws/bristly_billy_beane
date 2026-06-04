"""TopDeck.gg tournament data ingestion.

Fetches Commander tournament results and decklists from TopDeck.gg API.
Populates: tournament_results, decks, deck_cards tables.
"""

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from sabermetrics.errors import FatalError, NetworkError
from sabermetrics.ingestion.base import SourceHealthMixin, SyncResult
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

TOPDECK_BASE_URL = "https://topdeck.gg/api/v2"


class TopDeckIngestion(SourceHealthMixin):
    """TopDeck.gg tournament data ingestion source."""

    name: str = "topdeck"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._rate_limiter = RateLimiter(requests_per_second=1.5)  # 100/min cap
        self._api_key = os.environ.get("TOPDECK_API_KEY", "")

    def _headers(self) -> dict[str, str]:
        """Build request headers with auth."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def is_available(self) -> bool:
        """Check if TopDeck API is reachable and authenticated."""
        if not self._api_key:
            logger.warning("TOPDECK_API_KEY not set")
            return False
        try:
            self._rate_limiter.wait()
            resp = httpx.get(
                f"{TOPDECK_BASE_URL}/tournaments",
                headers=self._headers(),
                params={"format": "EDH", "limit": 1},
                timeout=10,
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def sync(self, full: bool = False) -> SyncResult:
        """Fetch tournament data from TopDeck.gg.

        Args:
            full: If True, fetch all available tournaments.
                  If False, fetch since last sync.
        """
        started_at = datetime.now()
        errors: list[str] = []
        items_ingested = 0
        items_failed = 0

        try:
            # Determine since date
            since = None
            if not full:
                since = self.last_updated()

            # Fetch tournament list
            tournaments = self._fetch_tournaments(since=since)
            logger.info("Found %d tournaments to process", len(tournaments))

            # Process each tournament
            for tourney in tournaments:
                try:
                    count = self._process_tournament(tourney)
                    items_ingested += count
                except Exception as e:
                    items_failed += 1
                    msg = f"Failed to process tournament {tourney.get('id', '?')}: {e}"
                    errors.append(msg)
                    logger.warning(msg)

            self._update_source_health(success=True)
            success = True
        except FatalError:
            raise
        except Exception as e:
            errors.append(str(e))
            self._update_source_health(success=False, error=str(e))
            success = False

        return SyncResult(
            source_name=self.name,
            started_at=started_at,
            completed_at=datetime.now(),
            items_ingested=items_ingested,
            items_updated=0,
            items_failed=items_failed,
            errors=errors,
            success=success,
        )

    def _fetch_tournaments(
        self, since: datetime | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Fetch list of EDH tournaments."""
        params: dict[str, Any] = {"format": "EDH", "limit": limit}
        if since:
            params["since"] = since.isoformat()

        self._rate_limiter.wait()
        try:
            resp = httpx.get(
                f"{TOPDECK_BASE_URL}/tournaments",
                headers=self._headers(),
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise NetworkError(f"Failed to fetch tournaments: {e}") from e

        data = resp.json()
        return data if isinstance(data, list) else data.get("tournaments", data.get("data", []))

    def _fetch_tournament_detail(self, tournament_id: str) -> dict[str, Any]:
        """Fetch detailed standings for a tournament."""
        self._rate_limiter.wait()
        try:
            resp = httpx.get(
                f"{TOPDECK_BASE_URL}/tournaments/{tournament_id}",
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise NetworkError(
                f"Failed to fetch tournament {tournament_id}: {e}"
            ) from e
        return resp.json()

    def _process_tournament(self, tourney: dict[str, Any]) -> int:
        """Process a single tournament's standings into the database.

        Returns:
            Number of results ingested.
        """
        tournament_id = str(tourney.get("id", ""))
        tournament_date = tourney.get("date", "")

        # Fetch detailed standings
        detail = self._fetch_tournament_detail(tournament_id)
        standings = detail.get("standings", [])
        if not standings:
            return 0

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        count = 0

        try:
            for standing in standings:
                player = standing.get("player", "unknown")
                deck_data = standing.get("deck", {})
                commander_name = deck_data.get("commander", "")
                card_names = deck_data.get("cards", [])
                standing_pos = standing.get("standing")
                win_rate = standing.get("win_rate")
                games_played = standing.get("games_played")
                games_won = standing.get("games_won")

                # Look up commander by name
                commander_id = self._resolve_card_id(conn, commander_name)
                if not commander_id:
                    logger.debug(
                        "Commander '%s' not found in cards table, skipping",
                        commander_name,
                    )
                    continue

                # Create deck entry if we have cards
                deck_id = None
                if card_names:
                    deck_id = self._create_deck(
                        conn,
                        commander_id=commander_id,
                        commander_name=commander_name,
                        card_names=card_names,
                        source_id=f"{tournament_id}-{player}",
                        deck_name=f"{player}'s {commander_name}",
                        creator=player,
                    )

                # Insert tournament result
                result_id = f"td-{tournament_id}-{player}"
                conn.execute(
                    """INSERT OR REPLACE INTO tournament_results
                    (id, tournament_id, player_name, deck_id, commander_id,
                     standing, win_rate, games_played, games_won, tournament_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        result_id,
                        tournament_id,
                        player,
                        deck_id,
                        commander_id,
                        standing_pos,
                        win_rate,
                        games_played,
                        games_won,
                        tournament_date,
                    ),
                )
                count += 1

            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise FatalError(f"Database write failed: {e}") from e
        finally:
            conn.close()

        return count

    def _resolve_card_id(self, conn: sqlite3.Connection, card_name: str) -> str | None:
        """Look up a card's Scryfall ID by name."""
        cursor = conn.execute(
            "SELECT id FROM cards WHERE name = ? LIMIT 1", (card_name,)
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def _create_deck(
        self,
        conn: sqlite3.Connection,
        commander_id: str,
        commander_name: str,
        card_names: list[str],
        source_id: str,
        deck_name: str,
        creator: str,
    ) -> str:
        """Create a deck and its card entries. Returns deck ID."""
        deck_id = str(uuid.uuid4())

        conn.execute(
            """INSERT OR REPLACE INTO decks
            (id, source, source_id, commander_id, deck_name, creator)
            VALUES (?, 'topdeck', ?, ?, ?, ?)""",
            (deck_id, source_id, commander_id, deck_name, creator),
        )

        # Insert commander as deck card
        conn.execute(
            """INSERT OR IGNORE INTO deck_cards
            (deck_id, card_id, quantity, is_commander)
            VALUES (?, ?, 1, TRUE)""",
            (deck_id, commander_id),
        )

        # Insert other cards
        for card_name in card_names:
            card_id = self._resolve_card_id(conn, card_name)
            if card_id:
                conn.execute(
                    """INSERT OR IGNORE INTO deck_cards
                    (deck_id, card_id, quantity, is_commander)
                    VALUES (?, ?, 1, FALSE)""",
                    (deck_id, card_id),
                )

        return deck_id
