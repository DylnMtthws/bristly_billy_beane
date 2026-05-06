"""Base protocol and models for all ingestion sources."""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


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
