"""Source health monitoring.

Checks availability of all data sources and updates the source_health table.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from sabermetrics.ingestion.base import IngestionSource

logger = logging.getLogger(__name__)


class SourceHealthMonitor:
    """Monitors availability of all registered data sources.

    Calls is_available() on each source and updates the source_health table.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._sources: list[Any] = []  # IngestionSource instances

    def register(self, source: Any) -> None:
        """Register an ingestion source for health monitoring.

        Args:
            source: An object implementing the IngestionSource protocol.
        """
        self._sources.append(source)

    def check_all(self) -> dict[str, bool]:
        """Check availability of all registered sources.

        Returns:
            Dict mapping source name to availability status.
        """
        results: dict[str, bool] = {}
        for source in self._sources:
            name = getattr(source, "name", str(source))
            try:
                available = source.is_available()
                results[name] = available
                logger.info("Source '%s': %s", name, "available" if available else "unavailable")
            except Exception as e:
                results[name] = False
                logger.warning("Source '%s' health check failed: %s", name, e)

        return results

    def get_health_report(self) -> list[dict[str, Any]]:
        """Get health status for all sources from the database.

        Returns:
            List of source health records.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM source_health ORDER BY source")
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_source_status(self, source_name: str) -> dict[str, Any] | None:
        """Get health status for a specific source.

        Args:
            source_name: Name of the source to check.

        Returns:
            Source health record or None if not found.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM source_health WHERE source = ?",
                (source_name,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
