"""Moxfield decklist ingestion via mtg-parser.

Parses Moxfield deck URLs into the decks and deck_cards tables.
Discovery: accepts deck URLs from external sources (TopDeck, EDHREC,
manual curation) or searches for popular commander decks.
"""

import logging
from pathlib import Path

import httpx

from sabermetrics.ingestion._decklist_base import DecklistIngestionBase
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

MOXFIELD_BASE_URL = "https://api2.moxfield.com/v3"
MOXFIELD_DECK_URL = "https://www.moxfield.com/decks"


class MoxfieldIngestion(DecklistIngestionBase):
    """Moxfield decklist ingestion source."""

    name: str = "moxfield"
    source_name: str = "moxfield"

    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._rate_limiter = RateLimiter(requests_per_second=1.0)

    def is_available(self) -> bool:
        """Check if Moxfield is reachable."""
        try:
            self._rate_limiter.wait()
            resp = httpx.get(
                "https://www.moxfield.com",
                timeout=10,
                follow_redirects=True,
                headers=self._headers(),
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def _headers(self) -> dict[str, str]:
        """Moxfield requires a custom User-Agent."""
        return {
            "User-Agent": "Sabermetrics/1.0 (personal research tool)",
        }

    def _discover_deck_urls(self, limit: int = 100) -> list[str]:
        """Discover popular deck URLs from Moxfield.

        Searches for popular commander decks via Moxfield's API.
        Falls back to an empty list if the API isn't accessible.
        """
        urls: list[str] = []

        # Get top commanders from our DB to search for
        commanders = self._get_top_commanders(limit=20)

        for _commander_id, commander_name in commanders:
            if len(urls) >= limit:
                break

            try:
                self._rate_limiter.wait()
                resp = httpx.get(
                    f"{MOXFIELD_BASE_URL}/search",
                    params={
                        "q": commander_name,
                        "fmt": "commander",
                        "sort": "views",
                        "pageSize": 10,
                    },
                    headers=self._headers(),
                    timeout=15,
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    continue

                data = resp.json()
                for deck in data.get("data", []):
                    public_id = deck.get("publicId", "")
                    if public_id:
                        urls.append(f"{MOXFIELD_DECK_URL}/{public_id}")
            except Exception as e:
                logger.debug("Moxfield search failed for '%s': %s", commander_name, e)

        return urls
