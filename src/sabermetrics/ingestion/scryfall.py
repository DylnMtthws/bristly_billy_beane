"""Scryfall bulk data ingestion.

Downloads the default_cards bulk dataset and populates the cards and
card_prices tables. Handles double-faced cards, missing prices, and
idempotent re-runs via INSERT OR REPLACE.
"""

import json
import logging
import re
import sqlite3
import tempfile
from datetime import datetime, date
from pathlib import Path
from typing import Any

import httpx

from sabermetrics.errors import FatalError, NetworkError, RecoverableError
from sabermetrics.ingestion.base import SyncResult
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

SCRYFALL_BULK_URL = "https://api.scryfall.com/bulk-data"
BATCH_SIZE = 1000


class ScryfallIngestion:
    """Scryfall bulk data ingestion source."""

    name: str = "scryfall"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._rate_limiter = RateLimiter(requests_per_second=1.0)

    def is_available(self) -> bool:
        """Check if Scryfall API is reachable."""
        try:
            self._rate_limiter.wait()
            resp = httpx.get(SCRYFALL_BULK_URL, timeout=10)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def last_updated(self) -> datetime | None:
        """When did Scryfall last successfully sync?"""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                "SELECT last_successful_sync FROM source_health WHERE source = ?",
                (self.name,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return datetime.fromisoformat(row[0])
            return None
        finally:
            conn.close()

    def sync(self, full: bool = False) -> SyncResult:
        """Download and ingest Scryfall bulk card data.

        Args:
            full: Ignored for Scryfall (always full bulk download).

        Returns:
            SyncResult with ingestion metrics.
        """
        started_at = datetime.now()
        errors: list[str] = []
        items_ingested = 0
        items_updated = 0
        items_failed = 0

        try:
            # Step 1: Get bulk data download URL
            download_url = self._get_bulk_download_url()

            # Step 2: Download bulk data to temp file
            cards_data = self._download_bulk_data(download_url)

            # Step 3: Parse and insert into database
            items_ingested, items_updated, items_failed, card_errors = (
                self._ingest_cards(cards_data)
            )
            errors.extend(card_errors)

            # Step 4: Update source health
            self._update_source_health(success=True)

            success = items_failed == 0 or items_ingested > 0
        except FatalError:
            raise
        except (RecoverableError, NetworkError) as e:
            errors.append(str(e))
            self._update_source_health(success=False, error=str(e))
            success = False
        except Exception as e:
            errors.append(f"Unexpected error: {e}")
            self._update_source_health(success=False, error=str(e))
            success = False

        return SyncResult(
            source_name=self.name,
            started_at=started_at,
            completed_at=datetime.now(),
            items_ingested=items_ingested,
            items_updated=items_updated,
            items_failed=items_failed,
            errors=errors,
            success=success,
        )

    def _get_bulk_download_url(self) -> str:
        """Fetch the download URL for the default_cards bulk dataset."""
        self._rate_limiter.wait()

        retries = 3
        for attempt in range(retries):
            try:
                resp = httpx.get(SCRYFALL_BULK_URL, timeout=30)
                resp.raise_for_status()
                break
            except httpx.HTTPError as e:
                if attempt == retries - 1:
                    raise NetworkError(
                        f"Failed to fetch bulk data list after {retries} attempts: {e}"
                    ) from e
                logger.warning(
                    "Bulk data list fetch attempt %d failed: %s", attempt + 1, e
                )

        data = resp.json()
        for entry in data.get("data", []):
            if entry.get("type") == "default_cards":
                return entry["download_uri"]

        raise FatalError("Could not find 'default_cards' bulk dataset in Scryfall API")

    def _download_bulk_data(self, url: str) -> list[dict[str, Any]]:
        """Download bulk JSON data, streaming to a temp file then parsing.

        Args:
            url: The Scryfall bulk download URL.

        Returns:
            List of card dictionaries.
        """
        logger.info("Downloading Scryfall bulk data from %s", url)

        retries = 3
        for attempt in range(retries):
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", suffix=".json", delete=False
                ) as tmp:
                    tmp_path = Path(tmp.name)
                    with httpx.stream("GET", url, timeout=300, follow_redirects=True) as resp:
                        resp.raise_for_status()
                        total = int(resp.headers.get("content-length", 0))
                        downloaded = 0
                        for chunk in resp.iter_bytes(chunk_size=8192):
                            tmp.write(chunk)
                            downloaded += len(chunk)
                            if total > 0 and downloaded % (10 * 1024 * 1024) < 8192:
                                pct = (downloaded / total) * 100
                                logger.info(
                                    "Download progress: %.1f%% (%d MB)",
                                    pct,
                                    downloaded // (1024 * 1024),
                                )

                # Parse the JSON file
                logger.info("Parsing bulk data file (%d MB)", tmp_path.stat().st_size // (1024 * 1024))
                with open(tmp_path) as f:
                    cards = json.load(f)

                # Clean up temp file
                tmp_path.unlink(missing_ok=True)

                logger.info("Parsed %d cards from bulk data", len(cards))
                return cards

            except (httpx.HTTPError, json.JSONDecodeError) as e:
                tmp_path.unlink(missing_ok=True)
                if isinstance(e, json.JSONDecodeError):
                    raise FatalError(f"Corrupted bulk download (JSON parse error): {e}") from e
                if attempt == retries - 1:
                    raise NetworkError(
                        f"Failed to download bulk data after {retries} attempts: {e}"
                    ) from e
                logger.warning("Download attempt %d failed: %s", attempt + 1, e)

        raise NetworkError("Failed to download bulk data")  # unreachable but satisfies type checker

    def _ingest_cards(
        self, cards_data: list[dict[str, Any]]
    ) -> tuple[int, int, int, list[str]]:
        """Parse card data and insert into database.

        Args:
            cards_data: List of raw Scryfall card objects.

        Returns:
            Tuple of (ingested, updated, failed, errors).
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")

        card_rows: list[tuple] = []
        price_rows: list[tuple] = []
        errors: list[str] = []
        failed = 0
        today = date.today().isoformat()

        for raw_card in cards_data:
            try:
                row, price_row = self._parse_card(raw_card, today)
                card_rows.append(row)
                if price_row is not None:
                    price_rows.append(price_row)
            except Exception as e:
                failed += 1
                card_name = raw_card.get("name", "unknown")
                errors.append(f"Failed to parse card '{card_name}': {e}")
                if failed <= 10:
                    logger.warning("Failed to parse card '%s': %s", card_name, e)

        # Batch insert cards
        ingested = 0
        try:
            for i in range(0, len(card_rows), BATCH_SIZE):
                batch = card_rows[i : i + BATCH_SIZE]
                conn.executemany(
                    """INSERT OR REPLACE INTO cards
                    (id, oracle_id, name, mana_cost, cmc, type_line, oracle_text,
                     color_identity, keywords, is_legal_commander, is_legal_in_99,
                     set_code, rarity, image_uri, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    batch,
                )
                ingested += len(batch)
                if ingested % 10000 == 0:
                    logger.info("Inserted %d / %d cards", ingested, len(card_rows))

            # Batch insert prices
            for i in range(0, len(price_rows), BATCH_SIZE):
                batch = price_rows[i : i + BATCH_SIZE]
                conn.executemany(
                    """INSERT OR REPLACE INTO card_prices
                    (card_id, price_usd, price_usd_foil, snapshot_date, source)
                    VALUES (?, ?, ?, ?, 'scryfall')""",
                    batch,
                )

            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise FatalError(f"Database write failed: {e}") from e
        finally:
            conn.close()

        logger.info(
            "Ingestion complete: %d cards, %d prices, %d failed",
            ingested,
            len(price_rows),
            failed,
        )
        return ingested, len(price_rows), failed, errors

    def _parse_card(
        self, raw: dict[str, Any], snapshot_date: str
    ) -> tuple[tuple, tuple | None]:
        """Parse a single Scryfall card object into DB row tuples.

        Args:
            raw: Raw Scryfall card JSON object.
            snapshot_date: Today's date string for price snapshots.

        Returns:
            Tuple of (card_row, price_row_or_None).
        """
        card_id = raw["id"]
        oracle_id = raw.get("oracle_id", "")
        name = raw["name"]

        # Handle double-faced cards
        card_faces = raw.get("card_faces")
        if card_faces and not raw.get("oracle_text"):
            oracle_text = " // ".join(
                face.get("oracle_text", "") for face in card_faces
            )
        else:
            oracle_text = raw.get("oracle_text")

        if card_faces and not raw.get("mana_cost"):
            mana_cost = card_faces[0].get("mana_cost", "")
        else:
            mana_cost = raw.get("mana_cost")

        if card_faces and not raw.get("type_line"):
            type_line = " // ".join(
                face.get("type_line", "") for face in card_faces
            )
        else:
            type_line = raw.get("type_line", "")

        cmc = raw.get("cmc", 0.0)
        color_identity = json.dumps(raw.get("color_identity", []))
        keywords = json.dumps(raw.get("keywords", []))

        # Legality derivation
        legalities = raw.get("legalities", {})
        is_legal_in_99 = legalities.get("commander") == "legal"

        # is_legal_commander: must be legal AND have appropriate type/text
        is_legal_commander = False
        if is_legal_in_99:
            is_legendary_creature = bool(
                re.search(r"Legendary.*Creature", type_line, re.IGNORECASE)
            )
            is_legendary_planeswalker = bool(
                re.search(r"Legendary.*Planeswalker", type_line, re.IGNORECASE)
            )
            can_be_commander_text = bool(
                oracle_text
                and re.search(
                    r"can be your commander", oracle_text, re.IGNORECASE
                )
            )
            is_legal_commander = (
                is_legendary_creature
                or is_legendary_planeswalker
                or can_be_commander_text
            )

        set_code = raw.get("set", "")
        rarity = raw.get("rarity", "")

        # Image URI: top-level or from first face
        image_uris = raw.get("image_uris")
        if image_uris:
            image_uri = image_uris.get("normal")
        elif card_faces and card_faces[0].get("image_uris"):
            image_uri = card_faces[0]["image_uris"].get("normal")
        else:
            image_uri = None

        card_row = (
            card_id,
            oracle_id,
            name,
            mana_cost,
            cmc,
            type_line,
            oracle_text,
            color_identity,
            keywords,
            is_legal_commander,
            is_legal_in_99,
            set_code,
            rarity,
            image_uri,
        )

        # Prices
        prices = raw.get("prices", {})
        price_usd = self._parse_price(prices.get("usd"))
        price_usd_foil = self._parse_price(prices.get("usd_foil"))

        if price_usd is not None or price_usd_foil is not None:
            price_row: tuple | None = (
                card_id,
                price_usd,
                price_usd_foil,
                snapshot_date,
            )
        else:
            price_row = None

        return card_row, price_row

    @staticmethod
    def _parse_price(value: str | None) -> float | None:
        """Parse a Scryfall price string to float, or None."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def _update_source_health(
        self, success: bool, error: str | None = None
    ) -> None:
        """Update the source_health table."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            now = datetime.now().isoformat()
            if success:
                conn.execute(
                    """INSERT OR REPLACE INTO source_health
                    (source, last_successful_sync, consecutive_failures)
                    VALUES (?, ?, 0)""",
                    (self.name, now),
                )
            else:
                conn.execute(
                    """INSERT INTO source_health
                    (source, last_failed_sync, last_error, consecutive_failures)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(source) DO UPDATE SET
                        last_failed_sync = excluded.last_failed_sync,
                        last_error = excluded.last_error,
                        consecutive_failures = consecutive_failures + 1""",
                    (self.name, now, error),
                )
            conn.commit()
        finally:
            conn.close()
