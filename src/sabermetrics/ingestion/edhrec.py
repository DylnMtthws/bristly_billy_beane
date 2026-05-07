"""EDHREC data ingestion via JSON endpoints.

Scrapes EDHREC's underlying JSON API for commander popularity data,
themes, inclusion rates, and salt scores.
Populates: edhrec_commander_data table.
"""

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from sabermetrics.errors import FatalError, NetworkError
from sabermetrics.ingestion.base import SyncResult
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

EDHREC_JSON_URL = "https://json.edhrec.com"


class EDHRECIngestion:
    """EDHREC data ingestion source."""

    name: str = "edhrec"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._rate_limiter = RateLimiter(requests_per_second=1.0)

    def is_available(self) -> bool:
        """Check if EDHREC JSON endpoints are reachable."""
        try:
            self._rate_limiter.wait()
            resp = httpx.get(
                f"{EDHREC_JSON_URL}/pages/top/salt.json",
                timeout=10,
                follow_redirects=True,
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def last_updated(self) -> datetime | None:
        """When did EDHREC last successfully sync?"""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                "SELECT last_successful_sync FROM source_health WHERE source = ?",
                (self.name,),
            )
            row = cursor.fetchone()
            return datetime.fromisoformat(row[0]) if row and row[0] else None
        finally:
            conn.close()

    def sync(self, full: bool = False) -> SyncResult:
        """Fetch EDHREC data for popular commanders.

        Args:
            full: If True, refresh all commanders. If False, only new ones.
        """
        started_at = datetime.now()
        errors: list[str] = []
        items_ingested = 0
        items_failed = 0

        try:
            # Get list of popular commanders from our DB
            commanders = self._get_popular_commanders()
            logger.info(
                "Processing EDHREC data for %d commanders", len(commanders)
            )

            for commander_id, commander_name in commanders:
                try:
                    slug = self._name_to_slug(commander_name)
                    data = self._fetch_commander_data(slug)
                    if data:
                        self._store_commander_data(commander_id, data)
                        items_ingested += 1
                    if items_ingested % 50 == 0 and items_ingested > 0:
                        logger.info(
                            "Processed %d / %d commanders",
                            items_ingested,
                            len(commanders),
                        )
                except NetworkError as e:
                    items_failed += 1
                    errors.append(
                        f"Failed to fetch EDHREC data for '{commander_name}': {e}"
                    )
                except Exception as e:
                    items_failed += 1
                    errors.append(
                        f"Error processing '{commander_name}': {e}"
                    )

            self._update_source_health(success=True)
            success = items_ingested > 0 or items_failed == 0
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

    def _get_popular_commanders(self, limit: int = 200) -> list[tuple[str, str]]:
        """Get top legal commanders from our cards table.

        Returns:
            List of (card_id, card_name) tuples.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                """SELECT id, name FROM cards
                WHERE is_legal_commander = 1
                ORDER BY name
                LIMIT ?""",
                (limit,),
            )
            return cursor.fetchall()
        finally:
            conn.close()

    @staticmethod
    def _name_to_slug(name: str) -> str:
        """Convert a commander name to an EDHREC URL slug.

        Example: "Korvold, Fae-Cursed King" -> "korvold-fae-cursed-king"
        """
        # Handle double-faced cards: take only the front face
        name = name.split(" // ")[0]
        slug = name.lower()
        slug = re.sub(r"[',.]", "", slug)
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = slug.strip("-")
        return slug

    def _fetch_commander_data(self, slug: str) -> dict[str, Any] | None:
        """Fetch EDHREC JSON data for a commander.

        Args:
            slug: EDHREC URL slug for the commander.

        Returns:
            Parsed JSON data or None if not found.
        """
        self._rate_limiter.wait()
        url = f"{EDHREC_JSON_URL}/pages/commanders/{slug}.json"

        try:
            resp = httpx.get(url, timeout=15, follow_redirects=True)
            if resp.status_code == 404:
                logger.debug("No EDHREC page for slug '%s'", slug)
                return None
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise NetworkError(f"EDHREC request failed for '{slug}': {e}") from e

        return resp.json()

    def _store_commander_data(
        self, commander_id: str, data: dict[str, Any]
    ) -> None:
        """Parse EDHREC JSON and store in edhrec_commander_data table."""
        container = data.get("container", data)
        json_dict = container.get("json_dict", container)

        # Extract themes
        themes: list[str] = []
        if "themes" in json_dict:
            themes = [t.get("value", t) if isinstance(t, dict) else str(t)
                      for t in json_dict["themes"]]

        # Extract top cards with inclusion percentages
        top_cards: list[dict[str, Any]] = []
        card_lists = json_dict.get("cardlists", json_dict.get("card_lists", []))
        deck_count = 0

        for card_list in card_lists:
            cardviews = card_list.get("cardviews", [])
            for cv in cardviews:
                card_name = cv.get("name", "")
                inclusion = cv.get("inclusion", 0)
                num_decks = cv.get("num_decks", 0)
                if num_decks > deck_count:
                    deck_count = num_decks
                pct = (inclusion / num_decks * 100) if num_decks > 0 else 0
                top_cards.append({
                    "card_name": card_name,
                    "inclusion_pct": round(pct, 1),
                })

        # Sort by inclusion and keep top entries
        top_cards.sort(key=lambda x: x["inclusion_pct"], reverse=True)
        top_cards = top_cards[:100]

        # Extract salt score if available
        salt_score = json_dict.get("salt", None)

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """INSERT OR REPLACE INTO edhrec_commander_data
                (commander_id, themes, salt_score, deck_count, top_cards,
                 last_updated)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    commander_id,
                    json.dumps(themes),
                    salt_score,
                    deck_count,
                    json.dumps(top_cards),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _update_source_health(
        self, success: bool, error: str | None = None
    ) -> None:
        """Update the source_health table."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            now = datetime.now().isoformat()
            if success:
                conn.execute(
                    """INSERT OR REPLACE INTO source_health
                    (source, last_successful_sync, consecutive_failures)
                    VALUES (?, ?, 0)""",
                    (self.name, now),
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
                    (self.name, now, error),
                )
            conn.commit()
        finally:
            conn.close()
