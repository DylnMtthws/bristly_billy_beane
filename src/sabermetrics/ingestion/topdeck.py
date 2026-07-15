"""TopDeck.gg tournament data ingestion.

Fetches Commander tournament results and decklists from TopDeck.gg API.
Populates: tournament_results, decks, deck_cards tables.
"""

import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from sabermetrics.errors import FatalError, NetworkError
from sabermetrics.ingestion.base import SourceHealthMixin, SyncResult
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

TOPDECK_BASE_URL = "https://topdeck.gg/api/v2"

# TopDeck v2 request constants. The tournaments endpoint is POST-only, filters by
# game+format in the JSON body, and windows results by `last` (in DAYS).
TOPDECK_GAME = "Magic: The Gathering"
TOPDECK_FORMAT = "EDH"
# Standing columns to request. `decklist` returns a text list with
# "~~Commanders~~" / "~~Mainboard~~" sections we parse for card membership.
TOPDECK_COLUMNS = ["name", "standing", "wins", "losses", "draws", "winRate", "decklist"]
# Default lookback window (days) for an incremental sync. The tournaments
# endpoint returns the whole window (with inline decklists) in one response, so
# the window is bounded by response size / read timeout rather than pagination;
# 30 days is a reliable single-request size.
DEFAULT_LOOKBACK_DAYS = 30
FULL_LOOKBACK_DAYS = 365


class TopDeckIngestion(SourceHealthMixin):
    """TopDeck.gg tournament data ingestion source."""

    name: str = "topdeck"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._rate_limiter = RateLimiter(requests_per_second=1.5)  # 100/min cap
        self._api_key = os.environ.get("TOPDECK_API_KEY", "")
        # Per-sync cache of card-name -> id lookups (populated lazily).
        self._card_id_cache: dict[str, str | None] = {}

    def _headers(self) -> dict[str, str]:
        """Build request headers. TopDeck expects the raw key (no 'Bearer')."""
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = self._api_key
        return headers

    def _request_body(self, last_days: int) -> dict[str, Any]:
        """Build the POST body for the tournaments endpoint."""
        return {
            "game": TOPDECK_GAME,
            "format": TOPDECK_FORMAT,
            "last": last_days,
            "columns": TOPDECK_COLUMNS,
        }

    def is_available(self) -> bool:
        """Check if TopDeck API is reachable and authenticated."""
        if not self._api_key:
            logger.warning("TOPDECK_API_KEY not set")
            return False
        try:
            self._rate_limiter.wait()
            resp = httpx.post(
                f"{TOPDECK_BASE_URL}/tournaments",
                headers=self._headers(),
                json={"game": TOPDECK_GAME, "format": TOPDECK_FORMAT, "last": 1},
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

            # Fetch tournament list (with inline standings)
            tournaments = self._fetch_tournaments(since=since, full=full)
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
        self, since: datetime | None = None, full: bool = False
    ) -> list[dict[str, Any]]:
        """Fetch EDH tournaments (with inline standings) via POST.

        Args:
            since: If set, window the lookback to cover this date.
            full: If True, use the wide historical window.

        Returns:
            List of tournament dicts, each with `TID`, `startDate`, `standings`.
        """
        if full:
            last_days = FULL_LOOKBACK_DAYS
        elif since is not None:
            last_days = max(1, (datetime.now() - since).days + 1)
        else:
            last_days = DEFAULT_LOOKBACK_DAYS

        self._rate_limiter.wait()
        try:
            resp = httpx.post(
                f"{TOPDECK_BASE_URL}/tournaments",
                headers=self._headers(),
                json=self._request_body(last_days),
                timeout=120,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise NetworkError(f"Failed to fetch tournaments: {e}") from e

        data = resp.json()
        return data if isinstance(data, list) else data.get("tournaments", data.get("data", []))

    def _process_tournament(self, tourney: dict[str, Any]) -> int:
        """Process a single tournament's inline standings into the database.

        Returns:
            Number of results ingested.
        """
        tournament_id = str(tourney.get("TID") or tourney.get("id", ""))
        tournament_date = tourney.get("startDate", "") or tourney.get("date", "")

        # Standings come inline in the tournaments response — no detail fetch.
        standings = tourney.get("standings", [])
        if not standings:
            return 0

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        count = 0

        try:
            for standing in standings:
                player = standing.get("name", "unknown")
                commanders, card_names = _parse_decklist(standing.get("decklist"))
                commander_name = commanders[0] if commanders else ""
                standing_pos = standing.get("standing")
                wins = standing.get("wins")
                losses = standing.get("losses")
                draws = standing.get("draws")
                win_rate = standing.get("winRate")
                # Derive games from W/L/D; skip records with no game data.
                if wins is None and losses is None and draws is None:
                    games_won = games_played = None
                else:
                    games_won = wins or 0
                    games_played = (wins or 0) + (losses or 0) + (draws or 0)

                if not commander_name:
                    continue

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
        """Look up a card's Scryfall ID by name, tolerant of decklist formatting.

        Tries, in order: exact name, case-insensitive, and the front face of a
        double-faced/adventure "A // B" name. Results are cached per sync.
        """
        name = (card_name or "").strip()
        if not name:
            return None
        if name in self._card_id_cache:
            return self._card_id_cache[name]

        # TopDeck decklists escape apostrophes as \' — strip stray backslashes
        # (no real card name contains one) so "Agatha\'s" matches "Agatha's".
        unescaped = name.replace("\\", "")
        candidates = [name]
        if unescaped != name:
            candidates.append(unescaped)
        # Front face of a double-faced/adventure "A // B" name.
        for base in (name, unescaped):
            if "//" in base:
                candidates.append(base.split("//")[0].strip())

        card_id: str | None = None
        for cand in candidates:
            row = conn.execute(
                "SELECT id FROM cards WHERE name = ? LIMIT 1", (cand,)
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT id FROM cards WHERE LOWER(name) = LOWER(?) LIMIT 1",
                    (cand,),
                ).fetchone()
            if row is not None:
                card_id = row[0]
                break

        self._card_id_cache[name] = card_id
        return card_id

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
        """Create a deck and its card entries. Returns deck ID.

        The deck id is derived deterministically from source_id so repeated
        weekly syncs replace the same deck rather than minting a new uuid each
        run (which would orphan deck_cards). Stale card rows are cleared first.
        """
        deck_id = f"td-{source_id}"
        conn.execute("DELETE FROM deck_cards WHERE deck_id = ?", (deck_id,))

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


# Section headers in a TopDeck text decklist.
_COMMANDER_SECTION = re.compile(r"^~~\s*commander", re.IGNORECASE)
_MAINBOARD_SECTION = re.compile(r"^~~\s*(mainboard|maindeck|deck)", re.IGNORECASE)
_CARD_LINE = re.compile(r"^(\d+)\s+(.+)$")


def _parse_decklist(text: str | None) -> tuple[list[str], list[str]]:
    """Parse a TopDeck text decklist into (commanders, mainboard card names).

    The format uses ``~~Commanders~~`` / ``~~Mainboard~~`` section headers and
    ``<qty> <card name>`` lines; line breaks arrive as escaped ``\\n``. Other
    sections (sideboard/maybeboard) are ignored. Quantities are flattened to a
    flat name list (Commander is singleton).

    Args:
        text: Raw decklist string, or None/empty.

    Returns:
        Tuple of (commander names, mainboard card names). Empty lists if no text.
    """
    if not text:
        return [], []

    commanders: list[str] = []
    mainboard: list[str] = []
    section: str | None = None
    for raw in text.replace("\\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if _COMMANDER_SECTION.match(line):
            section = "cmd"
            continue
        if _MAINBOARD_SECTION.match(line):
            section = "main"
            continue
        if line.startswith("~~"):  # sideboard / maybeboard / other
            section = None
            continue
        m = _CARD_LINE.match(line)
        if not m or section is None:
            continue
        name = m.group(2).strip()
        (commanders if section == "cmd" else mainboard).append(name)

    return commanders, mainboard
