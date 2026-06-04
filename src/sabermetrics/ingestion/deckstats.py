"""deckstats.net decklist ingestion via mtg-parser.

Parses deckstats deck URLs into the decks and deck_cards tables.
"""

import logging
from pathlib import Path

import httpx

from sabermetrics.ingestion._decklist_base import DecklistIngestionBase
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

DECKSTATS_BASE_URL = "https://deckstats.net"


class DeckstatsIngestion(DecklistIngestionBase):
    """deckstats.net decklist ingestion source."""

    name: str = "deckstats"
    source_name: str = "deckstats"

    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._rate_limiter = RateLimiter(requests_per_second=1.0)

    def is_available(self) -> bool:
        """Check if deckstats.net is reachable."""
        try:
            self._rate_limiter.wait()
            resp = httpx.get(
                DECKSTATS_BASE_URL,
                timeout=10,
                follow_redirects=True,
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def _discover_deck_urls(self, limit: int = 100) -> list[str]:
        """Discover popular deck URLs from deckstats.net.

        Searches for popular commander decks. deckstats has limited
        search API, so this returns fewer results than other sources.
        """
        urls: list[str] = []
        commanders = self._get_top_commanders(limit=20)

        for _commander_id, commander_name in commanders:
            if len(urls) >= limit:
                break

            try:
                self._rate_limiter.wait()
                resp = httpx.get(
                    f"{DECKSTATS_BASE_URL}/api.php",
                    params={
                        "action": "search_decks",
                        "search_text": commander_name,
                        "format": 10,  # Commander
                        "limit": 10,
                    },
                    timeout=15,
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    continue

                data = resp.json()
                for deck in data.get("results", []):
                    deck_url = deck.get("url", "")
                    if deck_url:
                        urls.append(deck_url)
            except Exception as e:
                logger.debug(
                    "deckstats search failed for '%s': %s", commander_name, e
                )

        return urls
