"""Source health monitoring.

Checks availability of all data sources and updates the source_health table.
"""

import logging
from pathlib import Path
from typing import Any

from sabermetrics.db import SourceHealthRepo

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
        return SourceHealthRepo(self.db_path).get_all()

    def get_source_status(self, source_name: str) -> dict[str, Any] | None:
        """Get health status for a specific source.

        Args:
            source_name: Name of the source to check.

        Returns:
            Source health record or None if not found.
        """
        return SourceHealthRepo(self.db_path).get(source_name)
