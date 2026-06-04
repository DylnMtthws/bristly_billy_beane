"""Commander Spellbook combo data ingestion.

Fetches combo data from Commander Spellbook's API using cursor-based
pagination. Populates: combos table.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from sabermetrics.errors import FatalError, NetworkError
from sabermetrics.ingestion.base import SourceHealthMixin, SyncResult
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

SPELLBOOK_API_URL = "https://backend.commanderspellbook.com"


class SpellbookIngestion(SourceHealthMixin):
    """Commander Spellbook combo data ingestion source."""

    name: str = "spellbook"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._rate_limiter = RateLimiter(requests_per_second=0.5)

    def is_available(self) -> bool:
        """Check if Commander Spellbook API is reachable."""
        try:
            self._rate_limiter.wait()
            resp = httpx.get(
                f"{SPELLBOOK_API_URL}/variants/",
                params={"limit": 1},
                timeout=10,
            )
            return resp.status_code in (200, 429)  # 429 means it's up, just throttled
        except httpx.HTTPError:
            return False

    def sync(self, full: bool = False) -> SyncResult:
        """Fetch all combos from Commander Spellbook.

        Args:
            full: Ignored; always fetches all combos (cursor-based pagination).
        """
        started_at = datetime.now()
        errors: list[str] = []
        items_ingested = 0
        items_failed = 0

        try:
            items_ingested, items_failed, errors = self._fetch_and_store_all()
            self._update_source_health(success=True)
            success = items_ingested > 0
        except FatalError:
            raise
        except Exception as e:
            errors.append(str(e))
            self._update_source_health(success=False, error=str(e))
            success = items_ingested > 0

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

    def _fetch_and_store_all(self) -> tuple[int, int, list[str]]:
        """Fetch all combos and store per-page to avoid losing progress.

        Returns:
            Tuple of (ingested, failed, errors).
        """
        import time

        items_ingested = 0
        items_failed = 0
        errors: list[str] = []
        url: str | None = f"{SPELLBOOK_API_URL}/variants/?limit=100"

        conn = sqlite3.connect(str(self.db_path))
        try:
            while url:
                self._rate_limiter.wait()

                # Fetch page with retries
                resp = None
                for attempt in range(5):
                    try:
                        resp = httpx.get(url, timeout=30, follow_redirects=True)
                        if resp.status_code == 429:
                            wait = min(2 ** (attempt + 2), 120)
                            logger.warning("Rate limited, waiting %ds...", wait)
                            time.sleep(wait)
                            continue
                        resp.raise_for_status()
                        break
                    except httpx.HTTPError as e:
                        if attempt == 4:
                            logger.error("Failed after retries: %s", e)
                            conn.commit()  # Save what we have
                            return items_ingested, items_failed, errors

                if resp is None or resp.status_code != 200:
                    conn.commit()
                    return items_ingested, items_failed, errors

                data = resp.json()
                results = data.get("results", [])

                # Store this page's results
                for combo in results:
                    try:
                        self._store_combo(conn, combo)
                        items_ingested += 1
                    except Exception as e:
                        items_failed += 1
                        errors.append(
                            f"Failed to store combo {combo.get('id', '?')}: {e}"
                        )

                # Commit per page so we don't lose progress
                conn.commit()

                url = data.get("next")
                if items_ingested % 2000 == 0 and items_ingested > 0:
                    logger.info("Stored %d combos so far...", items_ingested)

            logger.info("Spellbook ingestion complete: %d combos", items_ingested)
        finally:
            conn.close()

        return items_ingested, items_failed, errors

    def _store_combo(
        self, conn: sqlite3.Connection, combo: dict[str, Any]
    ) -> None:
        """Parse and store a single combo."""
        combo_id = str(combo.get("id", ""))

        # Extract card names from uses
        uses = combo.get("uses", [])
        card_names = []
        for use in uses:
            card = use.get("card", {})
            name = card.get("name", "")
            if name:
                card_names.append(name)

        # Extract results from produces
        produces = combo.get("produces", [])
        result_parts = []
        for prod in produces:
            feature = prod.get("feature", {})
            feat_name = feature.get("name", "")
            if feat_name:
                result_parts.append(feat_name)
        result = ", ".join(result_parts) if result_parts else None

        # Parse color identity string (e.g., "WUBR") to list
        identity_str = combo.get("identity", "")
        color_identity = list(identity_str) if identity_str else []

        description = combo.get("description", "")
        prerequisites = combo.get("prerequisites", "") or combo.get("easyPrerequisites", "")

        conn.execute(
            """INSERT OR REPLACE INTO combos
            (id, cards, color_identity, description, result, prerequisites,
             last_updated)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                combo_id,
                json.dumps(card_names),
                json.dumps(color_identity),
                description,
                result,
                prerequisites,
            ),
        )
