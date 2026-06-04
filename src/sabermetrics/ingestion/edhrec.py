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
from sabermetrics.ingestion.base import SourceHealthMixin, SyncResult
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

EDHREC_JSON_URL = "https://json.edhrec.com"


class EDHRECIngestion(SourceHealthMixin):
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

    def sync(self, full: bool = False) -> SyncResult:
        """Fetch EDHREC data for commanders.

        Args:
            full: If True, refresh all commanders. If False, only stale ones.
        """
        started_at = datetime.now()
        errors: list[str] = []
        items_ingested = 0
        items_skipped = 0
        items_failed = 0

        try:
            commanders = self._get_popular_commanders()
            total_commanders = len(commanders)

            if not full:
                commanders = self._filter_stale_commanders(commanders)

            logger.info(
                "Processing EDHREC data for %d commanders (%d total, full=%s)",
                len(commanders),
                total_commanders,
                full,
            )

            for commander_id, commander_name in commanders:
                try:
                    slug = self._name_to_slug(commander_name)
                    data = self._fetch_commander_data(slug)
                    if data:
                        self._store_commander_data(commander_id, data)
                        items_ingested += 1
                    else:
                        # No EDHREC page — store sentinel to avoid retrying
                        self._store_empty_commander(commander_id)
                        items_skipped += 1
                    processed = items_ingested + items_skipped + items_failed
                    if processed % 100 == 0 and processed > 0:
                        elapsed = (datetime.now() - started_at).total_seconds()
                        rate = processed / elapsed
                        remaining = (
                            (len(commanders) - processed) / rate
                            if rate > 0
                            else 0
                        )
                        logger.info(
                            "Processed %d / %d commanders "
                            "(%.0f/min, ~%.0fm remaining)",
                            processed,
                            len(commanders),
                            rate * 60,
                            remaining / 60,
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

            elapsed_total = (datetime.now() - started_at).total_seconds()
            logger.info(
                "EDHREC sync complete: %d ingested, %d skipped (no page), "
                "%d failed in %.1f minutes",
                items_ingested,
                items_skipped,
                items_failed,
                elapsed_total / 60,
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
            items_updated=items_skipped,
            items_failed=items_failed,
            errors=errors,
            success=success,
        )

    def _get_popular_commanders(self) -> list[tuple[str, str]]:
        """Get all legal commanders from our cards table.

        Returns:
            List of (card_id, card_name) tuples.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                """SELECT id, name FROM cards
                WHERE is_legal_commander = 1
                ORDER BY name"""
            )
            return cursor.fetchall()
        finally:
            conn.close()

    def _filter_stale_commanders(
        self, commanders: list[tuple[str, str]], max_age_days: int = 7
    ) -> list[tuple[str, str]]:
        """Filter out commanders with recent EDHREC data.

        Args:
            commanders: List of (card_id, card_name) tuples.
            max_age_days: Skip commanders updated within this many days.

        Returns:
            Commanders that need refreshing.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                """SELECT commander_id FROM edhrec_commander_data
                WHERE last_updated > datetime('now', ?)""",
                (f"-{max_age_days} days",),
            )
            recent_ids = {row[0] for row in cursor.fetchall()}
        finally:
            conn.close()
        return [(cid, name) for cid, name in commanders if cid not in recent_ids]

    def _store_empty_commander(self, commander_id: str) -> None:
        """Store a sentinel row for commanders without EDHREC pages."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """INSERT OR REPLACE INTO edhrec_commander_data
                (commander_id, themes, salt_score, deck_count, top_cards,
                 last_updated)
                VALUES (?, '[]', NULL, 0, '[]', CURRENT_TIMESTAMP)""",
                (commander_id,),
            )
            conn.commit()
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
                potential_decks = cv.get("potential_decks", 0)
                if potential_decks > deck_count:
                    deck_count = potential_decks
                pct = (
                    (inclusion / potential_decks * 100)
                    if potential_decks > 0
                    else 0
                )
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
