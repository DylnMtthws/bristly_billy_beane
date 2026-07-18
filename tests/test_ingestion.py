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


# --- Corrected Archidekt ingestion: pure parse/verify helpers ---------------


def _archidekt_detail_fixture() -> dict:
    """Minimal /api/decks/{id}/ payload with a commander + two 99-cards."""
    return {
        "name": "Korvold Treasure Tyrant",
        "cards": [
            {
                "quantity": 1,
                "categories": ["Commander"],
                "card": {"oracleCard": {"name": "Korvold, Fae-Cursed King"}},
            },
            {
                "quantity": 1,
                "categories": ["Ramp"],
                "card": {"oracleCard": {"name": "Sol Ring"}},
            },
            {
                "quantity": 1,
                "categories": [],
                "card": {"oracleCard": {"name": "Dockside Extortionist"}},
            },
        ],
    }


def test_parse_deck_detail_extracts_commander_and_cards() -> None:
    """parse_deck_detail separates the Commander slot from the 99."""
    from sabermetrics.ingestion.archidekt import parse_deck_detail

    commanders, cards = parse_deck_detail(_archidekt_detail_fixture())
    assert commanders == ["Korvold, Fae-Cursed King"]
    assert len(cards) == 3
    assert ("Sol Ring", 1, False) in cards
    assert ("Korvold, Fae-Cursed King", 1, True) in cards


def test_parse_deck_detail_skips_nameless_and_empty() -> None:
    """Entries without an oracle name are ignored; empty payload is safe."""
    from sabermetrics.ingestion.archidekt import parse_deck_detail

    commanders, cards = parse_deck_detail({"cards": [{"quantity": 1}]})
    assert commanders == []
    assert cards == []
    assert parse_deck_detail({}) == ([], [])


def test_commander_matches_accepts_commander_slot() -> None:
    """A deck where the card is the commander verifies (case-insensitive)."""
    from sabermetrics.ingestion.archidekt import commander_matches

    assert commander_matches("Korvold, Fae-Cursed King", ["Korvold, Fae-Cursed King"])
    assert commander_matches("korvold, fae-cursed king", ["Korvold, Fae-Cursed King"])


def test_commander_matches_rejects_card_only_in_99() -> None:
    """The core fix: a card present but NOT in the Commander slot is rejected."""
    from sabermetrics.ingestion.archidekt import commander_matches

    # e.g. a World Shaper deck that merely runs Korvold in the 99.
    assert not commander_matches("Korvold, Fae-Cursed King", ["World Shaper"])
    assert not commander_matches("Korvold, Fae-Cursed King", [])


def test_commander_matches_partner_deck() -> None:
    """Partner decks list two commanders; either one verifies."""
    from sabermetrics.ingestion.archidekt import commander_matches

    slot = ["Thrasios, Triton Hero", "Tymna the Weaver"]
    assert commander_matches("Tymna the Weaver", slot)
    assert commander_matches("Thrasios, Triton Hero", slot)


def test_extract_summary_metadata_pulls_tags_and_bracket() -> None:
    """Creator tags, bracket, and creator are pulled from a search summary."""
    from sabermetrics.ingestion.archidekt import extract_summary_metadata

    summary = {
        "owner": {"username": "someUser"},
        "edhBracket": 3,
        "viewCount": 12345,
        "hasPrimer": True,
        "tags": [
            {"name": "Sacrifice"},
            {"name": "Aristocrats"},
            {"id": 1},  # malformed tag without a name — skipped
        ],
        "createdAt": "2023-01-01",
        "updatedAt": "2024-01-01",
    }
    meta = extract_summary_metadata(summary)
    assert meta["creator"] == "someUser"
    assert meta["power_tier"] == 3
    assert meta["tags"] == ["Sacrifice", "Aristocrats"]
    assert meta["view_count"] == 12345
    assert meta["has_primer"] is True


def test_extract_summary_metadata_handles_missing_fields() -> None:
    """A bare summary yields Nones/empties, not exceptions."""
    from sabermetrics.ingestion.archidekt import extract_summary_metadata

    meta = extract_summary_metadata({})
    assert meta["creator"] is None
    assert meta["power_tier"] is None
    assert meta["tags"] == []
    assert meta["has_primer"] is False


def test_parse_deck_detail_excludes_maybeboard() -> None:
    """Cards only in includedInDeck=false categories are dropped from the deck."""
    from sabermetrics.ingestion.archidekt import parse_deck_detail

    data = {
        "categories": [
            {"name": "Commander", "includedInDeck": True},
            {"name": "Ramp", "includedInDeck": True},
            {"name": "Maybeboard", "includedInDeck": False},
            {"name": "Consider Adding", "includedInDeck": False},
        ],
        "cards": [
            {"quantity": 1, "categories": ["Commander"],
             "card": {"oracleCard": {"name": "Korvold, Fae-Cursed King"}}},
            {"quantity": 1, "categories": ["Ramp"],
             "card": {"oracleCard": {"name": "Sol Ring"}}},
            {"quantity": 1, "categories": ["Maybeboard"],
             "card": {"oracleCard": {"name": "Dockside Extortionist"}}},
            {"quantity": 1, "categories": ["Consider Adding", "Maybeboard"],
             "card": {"oracleCard": {"name": "Mana Crypt"}}},
            # In both an excluded and an included category -> kept.
            {"quantity": 1, "categories": ["Maybeboard", "Ramp"],
             "card": {"oracleCard": {"name": "Arcane Signet"}}},
        ],
    }
    commanders, cards = parse_deck_detail(data)
    names = {c[0] for c in cards}
    assert commanders == ["Korvold, Fae-Cursed King"]
    assert "Sol Ring" in names and "Arcane Signet" in names
    assert "Dockside Extortionist" not in names  # maybeboard only
    assert "Mana Crypt" not in names             # excluded categories only
