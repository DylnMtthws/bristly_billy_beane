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


def _make_edhrec_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite DB with the tables EDHRECIngestion needs."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE cards (
            id TEXT PRIMARY KEY,
            name TEXT,
            is_legal_commander INTEGER DEFAULT 0
        );
        CREATE TABLE edhrec_commander_data (
            commander_id TEXT PRIMARY KEY,
            themes TEXT,
            salt_score REAL,
            deck_count INTEGER,
            top_cards TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE source_health (
            source TEXT PRIMARY KEY,
            last_successful_sync TEXT,
            last_failed_sync TEXT,
            last_error TEXT,
            consecutive_failures INTEGER DEFAULT 0
        );
    """)
    conn.close()
    return db_path


def test_get_popular_commanders_returns_all(tmp_path: Path) -> None:
    """_get_popular_commanders returns all legal commanders, not just 200."""
    db_path = _make_edhrec_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    # Insert 250 commanders — old code would only return 200
    for i in range(250):
        conn.execute(
            "INSERT INTO cards (id, name, is_legal_commander) VALUES (?, ?, 1)",
            (f"id-{i:04d}", f"Commander {i:04d}"),
        )
    conn.commit()
    conn.close()

    source = EDHRECIngestion(db_path)
    commanders = source._get_popular_commanders()
    assert len(commanders) == 250


def test_filter_stale_commanders(tmp_path: Path) -> None:
    """_filter_stale_commanders skips recently-updated commanders."""
    db_path = _make_edhrec_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    # Two commanders
    conn.execute(
        "INSERT INTO cards (id, name, is_legal_commander) VALUES ('a', 'Alpha', 1)"
    )
    conn.execute(
        "INSERT INTO cards (id, name, is_legal_commander) VALUES ('b', 'Beta', 1)"
    )
    # Alpha has recent data, Beta does not
    conn.execute(
        """INSERT INTO edhrec_commander_data
        (commander_id, themes, deck_count, top_cards, last_updated)
        VALUES ('a', '[]', 100, '[]', CURRENT_TIMESTAMP)"""
    )
    conn.commit()
    conn.close()

    source = EDHRECIngestion(db_path)
    all_commanders = [("a", "Alpha"), ("b", "Beta")]
    stale = source._filter_stale_commanders(all_commanders)
    assert len(stale) == 1
    assert stale[0][0] == "b"


def test_filter_stale_commanders_old_data(tmp_path: Path) -> None:
    """Commanders with data older than max_age_days are included."""
    db_path = _make_edhrec_db(tmp_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO cards (id, name, is_legal_commander) VALUES ('a', 'Alpha', 1)"
    )
    # Data is 10 days old
    conn.execute(
        """INSERT INTO edhrec_commander_data
        (commander_id, themes, deck_count, top_cards, last_updated)
        VALUES ('a', '[]', 100, '[]', datetime('now', '-10 days'))"""
    )
    conn.commit()
    conn.close()

    source = EDHRECIngestion(db_path)
    stale = source._filter_stale_commanders([("a", "Alpha")], max_age_days=7)
    assert len(stale) == 1


def test_store_empty_commander(tmp_path: Path) -> None:
    """_store_empty_commander writes a sentinel row."""
    db_path = _make_edhrec_db(tmp_path)

    source = EDHRECIngestion(db_path)
    source._store_empty_commander("cmd-404")

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT deck_count, top_cards FROM edhrec_commander_data WHERE commander_id = ?",
        ("cmd-404",),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 0
    assert row[1] == "[]"


def test_store_empty_commander_skipped_on_incremental(tmp_path: Path) -> None:
    """A sentinel row prevents the commander from being re-fetched."""
    db_path = _make_edhrec_db(tmp_path)

    source = EDHRECIngestion(db_path)
    source._store_empty_commander("cmd-404")

    # Now filter — cmd-404 should be considered recent
    stale = source._filter_stale_commanders([("cmd-404", "Missing Commander")])
    assert len(stale) == 0


def test_health_monitor_init() -> None:
    """SourceHealthMonitor can be instantiated and report from empty DB."""
    db_path = Path("data/sabermetrics.db")
    monitor = SourceHealthMonitor(db_path)
    report = monitor.get_health_report()
    assert isinstance(report, list)
