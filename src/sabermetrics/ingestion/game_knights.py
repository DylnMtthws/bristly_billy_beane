"""Game Knights decklist ingestion from Archidekt.

Scrapes decklists from the GameKnights Archidekt account for use
in the deckbuilding knowledge base. Distinct from general Archidekt
ingestion — this targets a specific known-quality source.
"""

import logging
from pathlib import Path

import httpx

from sabermetrics.config import load_settings
from sabermetrics.ingestion._decklist_base import DecklistIngestionBase
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

ARCHIDEKT_API_URL = "https://archidekt.com/api"
ARCHIDEKT_DECK_URL = "https://archidekt.com/decks"


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

    def _discover_deck_urls(self, limit: int = 200) -> list[str]:
        """Discover Game Knights deck URLs from Archidekt API.

        Searches for all Commander decks owned by the GameKnights account,
        paginating through results ordered by view count. Falls back to
        a configured list of known deck IDs if the API search fails.

        Args:
            limit: Maximum number of deck URLs to return.

        Returns:
            List of Archidekt deck URLs.
        """
        urls: list[str] = []
        page = 1

        while len(urls) < limit:
            try:
                self._rate_limiter.wait()
                resp = httpx.get(
                    f"{ARCHIDEKT_API_URL}/decks/cards/",
                    params={
                        "owner": self._owner,
                        "ownerexact": "true",
                        "deckFormat": 3,  # Commander format
                        "orderBy": "-viewCount",
                        "pageSize": 50,
                        "page": page,
                    },
                    timeout=15,
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Archidekt API returned %d for page %d",
                        resp.status_code,
                        page,
                    )
                    break

                data = resp.json()
                results = data.get("results", []) if isinstance(data, dict) else data
                if not isinstance(results, list) or not results:
                    break

                for deck in results:
                    deck_id = deck.get("id", "")
                    if deck_id:
                        urls.append(f"{ARCHIDEKT_DECK_URL}/{deck_id}")

                # Check if there are more pages
                next_url = data.get("next") if isinstance(data, dict) else None
                if not next_url:
                    break
                page += 1

            except Exception as e:
                logger.warning(
                    "Archidekt API search failed on page %d: %s", page, e
                )
                break

        if not urls and self._fallback_ids:
            logger.info(
                "API search returned no results; using %d fallback deck IDs",
                len(self._fallback_ids),
            )
            urls = [
                f"{ARCHIDEKT_DECK_URL}/{deck_id}"
                for deck_id in self._fallback_ids
            ]

        logger.info("Discovered %d Game Knights deck URLs", len(urls))
        return urls[:limit]
