"""Archidekt decklist ingestion via mtg-parser.

Parses Archidekt deck URLs into the decks and deck_cards tables.
Uses the /api/decks/v3/ search endpoint to discover popular decks.
"""

import logging
from pathlib import Path

import httpx

from sabermetrics.ingestion._decklist_base import DecklistIngestionBase
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

ARCHIDEKT_API_URL = "https://archidekt.com/api"
ARCHIDEKT_DECK_URL = "https://archidekt.com/decks"


class ArchidektIngestion(DecklistIngestionBase):
    """Archidekt decklist ingestion source."""

    name: str = "archidekt"
    source_name: str = "archidekt"

    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._rate_limiter = RateLimiter(requests_per_second=1.0)

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

    def _discover_deck_urls(self, limit: int = 100) -> list[str]:
        """Discover popular Commander deck URLs from Archidekt.

        Uses /api/decks/v3/ search endpoint. Note: Archidekt's API
        has limited server-side filtering, so results may not be
        precisely commander-only. Client-side filtering is applied.
        """
        urls: list[str] = []
        commanders = self._get_top_commanders(limit=20)

        for _commander_id, commander_name in commanders:
            if len(urls) >= limit:
                break

            try:
                self._rate_limiter.wait()
                resp = httpx.get(
                    f"{ARCHIDEKT_API_URL}/decks/v3/",
                    params={
                        "deckFormat": 3,  # Commander format
                        "commanders": commander_name,
                        "orderBy": "-viewCount",
                        "pageSize": 10,
                    },
                    timeout=15,
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    continue

                data = resp.json()
                results = data.get("results", data) if isinstance(data, dict) else data
                if isinstance(results, list):
                    for deck in results:
                        deck_id = deck.get("id", "")
                        if deck_id:
                            urls.append(f"{ARCHIDEKT_DECK_URL}/{deck_id}")
            except Exception as e:
                logger.debug(
                    "Archidekt search failed for '%s': %s", commander_name, e
                )

        return urls
