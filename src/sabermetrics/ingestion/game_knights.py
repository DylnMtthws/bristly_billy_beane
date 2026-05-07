"""Game Knights decklist ingestion from Archidekt.

Scrapes decklists from the GameKnights Archidekt account for use
in the deckbuilding knowledge base. Distinct from general Archidekt
ingestion — this targets a specific known-quality source.

Uses Archidekt's folder-based API: resolve username → user ID →
root folder → recursive folder traversal with pagination.

Overrides the base class _parse_and_store_deck to fetch card data
directly from the Archidekt deck API (GET /api/decks/{id}/),
bypassing mtg-parser which no longer works with Archidekt.
"""

import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import httpx

from sabermetrics.config import load_settings
from sabermetrics.ingestion._decklist_base import DecklistIngestionBase
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

ARCHIDEKT_API_URL = "https://archidekt.com/api"
ARCHIDEKT_DECK_URL = "https://archidekt.com/decks"
COMMANDER_FORMAT_ID = 3


class GameKnightsIngestion(DecklistIngestionBase):
    """Ingests decklists from the Game Knights Archidekt account."""

    name: str = "archidekt_gameknights"
    source_name: str = "archidekt_gameknights"

    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._rate_limiter = RateLimiter(requests_per_second=1.0)
        settings = load_settings()
        self._owner = settings.knowledge_base.game_knights_archidekt_owner
        self._fallback_ids = settings.knowledge_base.game_knights_fallback_deck_ids

    def is_available(self) -> bool:
        """Check if Archidekt is reachable."""
        try:
            self._rate_limiter.wait()
            resp = httpx.get(
                "https://archidekt.com",
                timeout=10,
                follow_redirects=True,
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def _discover_deck_urls(self, limit: int = 500) -> list[str]:
        """Discover Game Knights deck URLs via Archidekt folder traversal.

        Resolves the owner username to a user ID, retrieves the root
        folder, then recursively traverses all subfolders collecting
        Commander deck URLs. Falls back to configured deck IDs if
        the API calls fail.

        Args:
            limit: Maximum number of deck URLs to return.

        Returns:
            List of Archidekt deck URLs.
        """
        urls: list[str] = []

        try:
            root_folder_id = self._resolve_root_folder()
            if root_folder_id:
                self._collect_decks_from_folder(root_folder_id, urls, limit)
        except Exception as e:
            logger.warning("Archidekt folder traversal failed: %s", e)

        if not urls and self._fallback_ids:
            logger.info(
                "API discovery returned no results; using %d fallback deck IDs",
                len(self._fallback_ids),
            )
            urls = [
                f"{ARCHIDEKT_DECK_URL}/{deck_id}"
                for deck_id in self._fallback_ids
            ]

        logger.info("Discovered %d Game Knights deck URLs", len(urls))
        return urls[:limit]

    def _resolve_root_folder(self) -> int | None:
        """Resolve username → user ID → root folder ID.

        Returns:
            Root folder ID, or None if resolution fails.
        """
        # Step 1: username → user ID
        self._rate_limiter.wait()
        resp = httpx.get(
            f"{ARCHIDEKT_API_URL}/users/",
            params={"username": self._owner},
            timeout=15,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            logger.warning("User lookup returned %d", resp.status_code)
            return None

        data = resp.json()
        results = data.get("results", [])
        if not results:
            logger.warning("User '%s' not found on Archidekt", self._owner)
            return None

        user_id = results[0].get("id")
        if not user_id:
            return None

        logger.info("Resolved '%s' to user ID %d", self._owner, user_id)

        # Step 2: user ID → root folder ID
        self._rate_limiter.wait()
        resp = httpx.get(
            f"{ARCHIDEKT_API_URL}/users/{user_id}/",
            timeout=15,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            logger.warning("User profile returned %d", resp.status_code)
            return None

        profile = resp.json()
        root_folder_id = profile.get("rootFolderId")
        if not root_folder_id:
            logger.warning("No rootFolderId in user profile")
            return None

        logger.info("Root folder ID: %d", root_folder_id)
        return root_folder_id

    def _collect_decks_from_folder(
        self, folder_id: int, urls: list[str], limit: int
    ) -> None:
        """Recursively collect Commander deck URLs from a folder.

        Paginates through the folder's decks, filters for Commander
        format, and recurses into subfolders.

        Args:
            folder_id: Archidekt folder ID to traverse.
            urls: Accumulator list of deck URLs (mutated in place).
            limit: Stop collecting once this many URLs are gathered.
        """
        page_url: str | None = (
            f"{ARCHIDEKT_API_URL}/decks/folders/{folder_id}/?format=json"
        )
        subfolder_ids: list[int] = []

        while page_url and len(urls) < limit:
            try:
                self._rate_limiter.wait()
                resp = httpx.get(
                    page_url,
                    timeout=15,
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    logger.debug(
                        "Folder %d returned %d", folder_id, resp.status_code
                    )
                    break

                data: dict[str, Any] = resp.json()

                # Collect Commander decks from this page
                for deck in data.get("decks", []):
                    if len(urls) >= limit:
                        break
                    if deck.get("deckFormat") == COMMANDER_FORMAT_ID:
                        deck_id = deck.get("id")
                        if deck_id:
                            urls.append(f"{ARCHIDEKT_DECK_URL}/{deck_id}")

                # Collect subfolder IDs (only on first page)
                for sub in data.get("subfolders", []):
                    sub_id = sub.get("id")
                    if sub_id and not sub.get("private", False):
                        subfolder_ids.append(sub_id)

                # Paginate
                page_url = data.get("next")

            except Exception as e:
                logger.debug("Error reading folder %d: %s", folder_id, e)
                break

        # Recurse into subfolders
        for sub_id in subfolder_ids:
            if len(urls) >= limit:
                break
            self._collect_decks_from_folder(sub_id, urls, limit)

    def _parse_and_store_deck(self, url: str) -> bool:
        """Fetch and store a deck via the Archidekt JSON API.

        Overrides the base class mtg-parser approach, which no longer
        works with Archidekt. Fetches card data from GET /api/decks/{id}/
        and maps card names to Scryfall IDs in the local cards table.

        Args:
            url: Archidekt deck URL (e.g., https://archidekt.com/decks/12345).

        Returns:
            True if the deck was successfully stored.
        """
        # Extract deck ID from URL
        deck_id_str = url.rstrip("/").split("/")[-1]

        self._rate_limiter.wait()
        try:
            resp = httpx.get(
                f"{ARCHIDEKT_API_URL}/decks/{deck_id_str}/",
                timeout=15,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                logger.debug(
                    "[%s] Archidekt API returned %d for deck %s",
                    self.name, resp.status_code, deck_id_str,
                )
                return False
        except httpx.HTTPError as e:
            logger.warning("[%s] HTTP error fetching deck %s: %s", self.name, deck_id_str, e)
            return False

        data = resp.json()
        api_cards = data.get("cards", [])
        if not api_cards:
            return False

        deck_name = data.get("name", f"Deck {deck_id_str}")

        # Identify commander and build card list
        commander_name: str | None = None
        cards_data: list[tuple[str, int, bool]] = []

        for entry in api_cards:
            oracle = entry.get("card", {}).get("oracleCard", {})
            card_name = oracle.get("name")
            if not card_name:
                continue

            quantity = entry.get("quantity", 1)
            categories = entry.get("categories") or []
            is_commander = "Commander" in categories

            if is_commander and not commander_name:
                commander_name = card_name

            cards_data.append((card_name, quantity, is_commander))

        if not commander_name and cards_data:
            commander_name = cards_data[0][0]
            cards_data[0] = (cards_data[0][0], cards_data[0][1], True)

        # Store in DB
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            commander_id = self._resolve_card_id(conn, commander_name or "")
            if not commander_id:
                logger.debug(
                    "[%s] Commander '%s' not found in DB",
                    self.name, commander_name,
                )
                return False

            new_deck_id = str(uuid.uuid4())
            conn.execute(
                """INSERT OR REPLACE INTO decks
                (id, source, source_id, commander_id, deck_name)
                VALUES (?, ?, ?, ?, ?)""",
                (new_deck_id, self.source_name, url, commander_id, deck_name),
            )

            for card_name, quantity, is_commander in cards_data:
                card_id = self._resolve_card_id(conn, card_name)
                if card_id:
                    conn.execute(
                        """INSERT OR IGNORE INTO deck_cards
                        (deck_id, card_id, quantity, is_commander)
                        VALUES (?, ?, ?, ?)""",
                        (new_deck_id, card_id, quantity, is_commander),
                    )

            conn.commit()
            return True
        except sqlite3.Error as e:
            conn.rollback()
            logger.warning(
                "[%s] DB error storing deck %s: %s", self.name, deck_id_str, e
            )
            return False
        finally:
            conn.close()
