"""Reddit r/EDH cultural signal search.

On-demand search wrapper for Reddit's public JSON API. Used during
profile generation to gather community discussion about commanders.
Does NOT persist to database — returns RedditThread models directly.
"""

import logging
from typing import Any

import httpx

from sabermetrics.errors import NetworkError
from sabermetrics.models.evidence import RedditThread
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

REDDIT_BASE_URL = "https://www.reddit.com"
USER_AGENT = "Sabermetrics/1.0 (personal research)"


class RedditSearch:
    """Reddit r/EDH search wrapper for cultural signals.

    This is NOT an IngestionSource — it does not implement sync() or
    write to the database. It's called on-demand during profile
    generation to gather community discussion signals.
    """

    def __init__(self) -> None:
        self._rate_limiter = RateLimiter(requests_per_second=1.0)

    def search_commander(
        self,
        commander_name: str,
        top_k: int = 20,
        min_upvotes: int = 50,
    ) -> list[RedditThread]:
        """Search r/EDH for discussions about a commander.

        Args:
            commander_name: Commander card name to search for.
            top_k: Maximum number of threads to return.
            min_upvotes: Minimum upvote threshold for quality signal.

        Returns:
            List of RedditThread models, sorted by upvotes descending.
        """
        query = f"{commander_name} strategy"
        raw_threads = self._search_subreddit(query)

        # Filter and sort
        threads: list[RedditThread] = []
        for raw in raw_threads:
            data = raw.get("data", {})
            upvotes = data.get("ups", 0)
            if upvotes < min_upvotes:
                continue

            thread = RedditThread(
                title=data.get("title", ""),
                url=f"https://www.reddit.com{data.get('permalink', '')}",
                upvotes=upvotes,
                created_utc=int(data.get("created_utc", 0)),
                summary=self._extract_summary(data.get("selftext", "")),
            )
            threads.append(thread)

        # Sort by upvotes descending, take top_k
        threads.sort(key=lambda t: t.upvotes, reverse=True)
        return threads[:top_k]

    def _search_subreddit(self, query: str) -> list[dict[str, Any]]:
        """Execute a search against r/EDH.

        Args:
            query: Search query string.

        Returns:
            List of raw Reddit post data dicts.
        """
        self._rate_limiter.wait()

        try:
            resp = httpx.get(
                f"{REDDIT_BASE_URL}/r/EDH/search.json",
                params={
                    "q": query,
                    "restrict_sr": 1,
                    "sort": "top",
                    "t": "year",
                    "limit": 25,
                },
                headers={"User-Agent": USER_AGENT},
                timeout=15,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise NetworkError(f"Reddit search failed: {e}") from e

        data = resp.json()
        children = data.get("data", {}).get("children", [])
        return children

    @staticmethod
    def _extract_summary(selftext: str, max_length: int = 300) -> str | None:
        """Extract a brief summary from a Reddit post's selftext.

        Args:
            selftext: Raw post body text.
            max_length: Maximum summary length.

        Returns:
            Truncated summary or None if empty.
        """
        if not selftext:
            return None
        text = selftext.strip()
        if len(text) > max_length:
            text = text[:max_length].rsplit(" ", 1)[0] + "..."
        return text
