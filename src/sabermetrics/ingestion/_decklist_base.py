"""Shared base class for decklist ingestion sources (Moxfield, Archidekt, deckstats).

All three sources use mtg-parser for parsing and share the same DB
insertion logic. Each subclass provides source-specific URL discovery.
"""

import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import mtg_parser

from sabermetrics.errors import FatalError
from sabermetrics.ingestion.base import SourceHealthMixin, SyncResult
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)


class DecklistIngestionBase(SourceHealthMixin):
    """Base class for mtg-parser based decklist ingestion sources."""

    name: str = ""
    source_name: str = ""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._rate_limiter = RateLimiter(requests_per_second=1.0)

    def sync(self, full: bool = False) -> SyncResult:
        """Discover and parse decklists from this source.

        Args:
            full: If True, re-process all known URLs.
                  If False, only process new discoveries.
        """
        started_at = datetime.now()
        errors: list[str] = []
        items_ingested = 0
        items_failed = 0

        try:
            urls = self._discover_deck_urls()
            logger.info(
                "[%s] Found %d deck URLs to process", self.name, len(urls)
            )

            for url in urls:
                try:
                    # Skip if already ingested (unless full refresh)
                    if not full and self._deck_exists(url):
                        continue

                    success = self._parse_and_store_deck(url)
                    if success:
                        items_ingested += 1
                    else:
                        items_failed += 1
                        errors.append(f"Failed to parse deck: {url}")
                except Exception as e:
                    items_failed += 1
                    errors.append(f"Error processing {url}: {e}")

                if items_ingested % 25 == 0 and items_ingested > 0:
                    logger.info(
                        "[%s] Processed %d decks", self.name, items_ingested
                    )

            self._update_source_health(success=True)
            success_flag = True
        except FatalError:
            raise
        except Exception as e:
            errors.append(str(e))
            self._update_source_health(success=False, error=str(e))
            success_flag = False

        return SyncResult(
            source_name=self.name,
            started_at=started_at,
            completed_at=datetime.now(),
            items_ingested=items_ingested,
            items_updated=0,
            items_failed=items_failed,
            errors=errors,
            success=success_flag,
        )

    def _discover_deck_urls(self, limit: int = 100) -> list[str]:
        """Discover deck URLs from this source. Override in subclasses."""
        return []

    def _deck_exists(self, url: str) -> bool:
        """Check if a deck from this URL has already been ingested."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                "SELECT 1 FROM decks WHERE source = ? AND source_id = ? LIMIT 1",
                (self.source_name, url),
            )
            return cursor.fetchone() is not None
        finally:
            conn.close()

    def _parse_and_store_deck(self, url: str) -> bool:
        """Parse a deck URL via mtg-parser and store in the database.

        Returns:
            True if successful.
        """
        self._rate_limiter.wait()

        try:
            parsed_cards = mtg_parser.parse_deck(url)
        except Exception as e:
            logger.warning("[%s] mtg-parser failed for %s: %s", self.name, url, e)
            return False

        if parsed_cards is None:
            logger.debug("[%s] No cards returned for %s", self.name, url)
            return False

        card_list = list(parsed_cards)
        if not card_list:
            return False

        # Identify commander (tagged as "Commander" or similar)
        commander_name = None
        deck_cards_data: list[tuple[str, int, bool]] = []

        for card in card_list:
            is_commander = "Commander" in (card.tags or [])
            if is_commander and not commander_name:
                commander_name = card.name
            deck_cards_data.append((card.name, card.quantity, is_commander))

        if not commander_name:
            # Fallback: first card might be commander
            if deck_cards_data:
                commander_name = deck_cards_data[0][0]
                deck_cards_data[0] = (
                    deck_cards_data[0][0],
                    deck_cards_data[0][1],
                    True,
                )

        # Resolve commander to DB card ID
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            commander_id = self._resolve_card_id(conn, commander_name or "")
            if not commander_id:
                logger.debug(
                    "[%s] Commander '%s' not found in DB", self.name, commander_name
                )
                return False

            deck_id = str(uuid.uuid4())

            conn.execute(
                """INSERT OR REPLACE INTO decks
                (id, source, source_id, commander_id, deck_name)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    deck_id,
                    self.source_name,
                    url,
                    commander_id,
                    f"{commander_name} deck",
                ),
            )

            for card_name, quantity, is_commander in deck_cards_data:
                card_id = self._resolve_card_id(conn, card_name)
                if card_id:
                    conn.execute(
                        """INSERT OR IGNORE INTO deck_cards
                        (deck_id, card_id, quantity, is_commander)
                        VALUES (?, ?, ?, ?)""",
                        (deck_id, card_id, quantity, is_commander),
                    )

            conn.commit()
            return True
        except sqlite3.Error as e:
            conn.rollback()
            logger.warning("[%s] DB error storing deck %s: %s", self.name, url, e)
            return False
        finally:
            conn.close()

    def _resolve_card_id(
        self, conn: sqlite3.Connection, card_name: str
    ) -> str | None:
        """Look up a card's Scryfall ID by name."""
        cursor = conn.execute(
            "SELECT id FROM cards WHERE name = ? LIMIT 1", (card_name,)
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def _get_top_commanders(self, limit: int = 20) -> list[tuple[str, str]]:
        """Get top commanders from the cards table for URL discovery."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                """SELECT id, name FROM cards
                WHERE is_legal_commander = 1
                ORDER BY name LIMIT ?""",
                (limit,),
            )
            return cursor.fetchall()
        finally:
            conn.close()
