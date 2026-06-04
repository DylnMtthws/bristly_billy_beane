"""magicthegathering.io rulings ingestion.

Fetches card rulings via the mtgsdk library. Only used for rulings
(NOT primary card data, per ADR-013). Joined to cards via oracle_id.
Populates: card_rulings table.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import httpx

from sabermetrics.errors import FatalError, NetworkError
from sabermetrics.ingestion.base import SourceHealthMixin, SyncResult
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

MTGAPI_BASE_URL = "https://api.magicthegathering.io/v1"


class MtgApiIngestion(SourceHealthMixin):
    """magicthegathering.io rulings ingestion source."""

    name: str = "mtgapi"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._rate_limiter = RateLimiter(requests_per_second=1.0)

    def is_available(self) -> bool:
        """Check if magicthegathering.io API is reachable."""
        try:
            self._rate_limiter.wait()
            resp = httpx.get(
                f"{MTGAPI_BASE_URL}/cards",
                params={"page": 1, "pageSize": 1},
                timeout=10,
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def sync(self, full: bool = False) -> SyncResult:
        """Fetch rulings for cards in our database.

        Strategy: iterate cards in our DB that have oracle_ids,
        fetch rulings from mtgapi by name, store in card_rulings.
        Deduplicates by oracle_id to avoid re-fetching reprints.

        Args:
            full: If True, re-fetch all rulings. If False, skip cards
                  that already have rulings.
        """
        started_at = datetime.now()
        errors: list[str] = []
        items_ingested = 0
        items_failed = 0

        try:
            # Get unique oracle_ids with their names
            cards_to_process = self._get_cards_needing_rulings(full=full)
            logger.info(
                "Fetching rulings for %d unique cards", len(cards_to_process)
            )

            for i, (oracle_id, card_name) in enumerate(cards_to_process):
                try:
                    count = self._fetch_and_store_rulings(oracle_id, card_name)
                    items_ingested += count
                except NetworkError as e:
                    items_failed += 1
                    errors.append(f"Failed to fetch rulings for '{card_name}': {e}")
                except Exception as e:
                    items_failed += 1
                    errors.append(f"Error processing '{card_name}': {e}")

                if (i + 1) % 100 == 0:
                    logger.info(
                        "Processed %d / %d cards (%d rulings so far)",
                        i + 1,
                        len(cards_to_process),
                        items_ingested,
                    )

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

    def _get_cards_needing_rulings(
        self, full: bool, limit: int = 5000
    ) -> list[tuple[str, str]]:
        """Get cards that need rulings fetched.

        Args:
            full: If True, return all cards. If False, skip already-fetched.
            limit: Max cards to process in one run.

        Returns:
            List of (oracle_id, name) tuples.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            if full:
                cursor = conn.execute(
                    """SELECT DISTINCT oracle_id, name FROM cards
                    WHERE oracle_id != '' AND is_legal_in_99 = 1
                    ORDER BY name LIMIT ?""",
                    (limit,),
                )
            else:
                cursor = conn.execute(
                    """SELECT DISTINCT c.oracle_id, c.name FROM cards c
                    LEFT JOIN card_rulings cr ON c.oracle_id = cr.card_oracle_id
                    WHERE c.oracle_id != '' AND c.is_legal_in_99 = 1
                    AND cr.id IS NULL
                    ORDER BY c.name LIMIT ?""",
                    (limit,),
                )
            return cursor.fetchall()
        finally:
            conn.close()

    def _fetch_and_store_rulings(self, oracle_id: str, card_name: str) -> int:
        """Fetch rulings for a card from mtgapi and store them.

        Returns:
            Number of rulings stored.
        """
        # Search by name via the REST API directly (more reliable than SDK)
        self._rate_limiter.wait()
        try:
            resp = httpx.get(
                f"{MTGAPI_BASE_URL}/cards",
                params={"name": f'"{card_name}"', "pageSize": 1},
                timeout=15,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise NetworkError(f"mtgapi request failed: {e}") from e

        data = resp.json()
        cards = data.get("cards", [])
        if not cards:
            return 0

        # Get rulings from the first matching card
        rulings = cards[0].get("rulings", [])
        if not rulings:
            return 0

        conn = sqlite3.connect(str(self.db_path))
        count = 0
        try:
            for ruling in rulings:
                ruling_date = ruling.get("date")
                ruling_text = ruling.get("text", "")
                if not ruling_text:
                    continue

                # Check for duplicates
                cursor = conn.execute(
                    """SELECT 1 FROM card_rulings
                    WHERE card_oracle_id = ? AND ruling_text = ?
                    LIMIT 1""",
                    (oracle_id, ruling_text),
                )
                if cursor.fetchone():
                    continue

                conn.execute(
                    """INSERT INTO card_rulings
                    (card_oracle_id, ruling_date, ruling_text, source)
                    VALUES (?, ?, ?, 'mtgapi')""",
                    (oracle_id, ruling_date, ruling_text),
                )
                count += 1

            conn.commit()
        finally:
            conn.close()

        return count
