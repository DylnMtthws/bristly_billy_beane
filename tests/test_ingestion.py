"""Tests for Phase 2 ingestion sources."""

import sqlite3
from pathlib import Path

from sabermetrics.ingestion.base import SyncResult
from sabermetrics.ingestion.edhrec import EDHRECIngestion
from sabermetrics.ingestion.reddit import RedditSearch
from sabermetrics.ingestion.health import SourceHealthMonitor


def test_sync_result_model() -> None:
    """SyncResult can be created with all fields."""
    from datetime import datetime

    result = SyncResult(
        source_name="test",
        started_at=datetime.now(),
        completed_at=datetime.now(),
        items_ingested=100,
        items_updated=50,
        items_failed=2,
        errors=["error1", "error2"],
        success=True,
    )
    assert result.source_name == "test"
    assert result.items_ingested == 100
    assert result.success is True


def test_edhrec_name_to_slug() -> None:
    """EDHREC slug generation works for various name formats."""
    assert EDHRECIngestion._name_to_slug("Korvold, Fae-Cursed King") == "korvold-fae-cursed-king"
    assert EDHRECIngestion._name_to_slug("Atraxa, Praetors' Voice") == "atraxa-praetors-voice"
    assert EDHRECIngestion._name_to_slug("Thalia, Guardian of Thraben") == "thalia-guardian-of-thraben"
    # DFC: only front face
    assert EDHRECIngestion._name_to_slug("Fable of the Mirror-Breaker // Reflection of Kiki-Jiki") == "fable-of-the-mirror-breaker"


def test_reddit_search_init() -> None:
    """RedditSearch can be instantiated."""
    search = RedditSearch()
    assert search is not None


def test_reddit_extract_summary() -> None:
    """Summary extraction truncates long text."""
    short = "Hello world"
    assert RedditSearch._extract_summary(short) == "Hello world"
    assert RedditSearch._extract_summary("") is None
    assert RedditSearch._extract_summary(None) is None  # type: ignore[arg-type]

    long_text = "word " * 100
    summary = RedditSearch._extract_summary(long_text, max_length=50)
    assert summary is not None
    assert len(summary) <= 55  # 50 + room for "..."
    assert summary.endswith("...")


def test_health_monitor_init() -> None:
    """SourceHealthMonitor can be instantiated and report from empty DB."""
    db_path = Path("data/sabermetrics.db")
    monitor = SourceHealthMonitor(db_path)
    report = monitor.get_health_report()
    assert isinstance(report, list)
