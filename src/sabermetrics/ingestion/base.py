"""Base protocol and models for all ingestion sources."""

from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from sabermetrics.db import SourceHealthRepo


class SyncResult(BaseModel):
    """Result of a sync operation."""

    source_name: str
    started_at: datetime
    completed_at: datetime
    items_ingested: int
    items_updated: int
    items_failed: int
    errors: list[str]
    success: bool


class IngestionSource(Protocol):
    """Contract for all data ingestion adapters."""

    name: str

    def is_available(self) -> bool:
        """Check if source is reachable. Used for health monitoring."""
        ...

    def last_updated(self) -> datetime | None:
        """When did this source last successfully sync? None if never."""
        ...

    def sync(self, full: bool = False) -> SyncResult:
        """Pull data from source into local DB.

        Args:
            full: If True, full refresh; if False, incremental (delta only).

        Returns:
            SyncResult with metrics and any errors.

        Raises:
            FatalError: Only for unrecoverable issues (DB corruption, etc.)

        Notes:
            - Handles its own rate limiting
            - Handles its own retries for recoverable errors
            - Marks degraded state in source_health table on partial failure
            - Never raises on transient errors; logs and returns success=False
        """
        ...


class SourceHealthMixin:
    """Provides ``last_updated`` / ``_update_source_health`` via the repo.

    Hosts must set ``self.db_path`` and ``self.name`` in their ``__init__``
    (every ingestion source already does). This replaces the identical
    copy-pasted implementations that previously lived in each source.
    """

    db_path: Path
    name: str

    def last_updated(self) -> datetime | None:
        """When did this source last successfully sync? None if never."""
        return SourceHealthRepo(self.db_path).last_successful_sync(self.name)

    def _update_source_health(
        self, success: bool, error: str | None = None
    ) -> None:
        """Record a sync outcome in the source_health table."""
        SourceHealthRepo(self.db_path).record(self.name, success, error)
