"""Tests for deck pattern analysis and knowledge base generation."""

import json
import sqlite3
import tempfile
from pathlib import Path

from sabermetrics.analytics.deck_patterns import (
    ComponentStats,
    DeckbuildingPatterns,
    GameKnightsAnalyzer,
    KnowledgeBaseBuilder,
    _compute_stats,
)
from sabermetrics.config import KnowledgeBaseSettings, Settings, load_settings
from sabermetrics.ingestion.game_knights import GameKnightsIngestion


# --- ComponentStats tests ---


def test_compute_stats_empty() -> None:
    """Empty list returns zero stats."""
    result = _compute_stats([])
    assert result.mean == 0.0
    assert result.median == 0.0
    assert result.std_dev == 0.0


def test_compute_stats_single_value() -> None:
    """Single value returns that value for mean/median, zero std_dev."""
    result = _compute_stats([5.0])
    assert result.mean == 5.0
    assert result.median == 5.0
    assert result.min == 5.0
    assert result.max == 5.0
    assert result.std_dev == 0.0


def test_compute_stats_multiple_values() -> None:
    """Multiple values compute correct descriptive stats."""
    result = _compute_stats([2.0, 4.0, 6.0, 8.0, 10.0])
    assert result.mean == 6.0
    assert result.median == 6.0
    assert result.min == 2.0
    assert result.max == 10.0
    assert result.std_dev > 0


# --- GameKnightsAnalyzer tests ---


def _create_test_db(db_path: Path) -> None:
    """Create a minimal test database with decks and cards."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cards (
            id TEXT PRIMARY KEY,
            oracle_id TEXT NOT NULL,
            name TEXT NOT NULL,
            mana_cost TEXT,
            cmc REAL,
            type_line TEXT,
            oracle_text TEXT,
            color_identity TEXT,
            keywords TEXT,
            is_legal_commander BOOLEAN,
            is_legal_in_99 BOOLEAN,
            set_code TEXT,
            rarity TEXT,
            image_uri TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS decks (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            commander_id TEXT NOT NULL,
            deck_name TEXT,
            creator TEXT,
            estimated_price_usd REAL,
            power_tier INTEGER,
            raw_data TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, source_id)
        );
        CREATE TABLE IF NOT EXISTS deck_cards (
            deck_id TEXT,
            card_id TEXT,
            quantity INTEGER DEFAULT 1,
            is_commander BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (deck_id, card_id)
        );
        CREATE TABLE IF NOT EXISTS source_health (
            source TEXT PRIMARY KEY,
            last_successful_sync TIMESTAMP,
            last_failed_sync TIMESTAMP,
            last_error TEXT,
            consecutive_failures INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS reference_chunks (
            id TEXT PRIMARY KEY,
            document TEXT,
            section TEXT,
            tier INTEGER,
            content TEXT,
            embedding BLOB,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """
    )

    # Insert test cards
    cards = [
        ("cmd1", "orc1", "Korvold, Fae-Cursed King", "{2}{B}{R}{G}", 5.0,
         "Legendary Creature — Dragon Noble", "Flying. Whenever you sacrifice a permanent, draw a card.",
         '["B","R","G"]', '["Flying"]', True, True),
        ("land1", "orc2", "Forest", "", 0.0,
         "Basic Land — Forest", "{T}: Add {G}.",
         "[]", "[]", False, True),
        ("land2", "orc3", "Swamp", "", 0.0,
         "Basic Land — Swamp", "{T}: Add {B}.",
         "[]", "[]", False, True),
        ("land3", "orc4", "Mountain", "", 0.0,
         "Basic Land — Mountain", "{T}: Add {R}.",
         "[]", "[]", False, True),
        ("ramp1", "orc5", "Sol Ring", "{1}", 1.0,
         "Artifact", "{T}: Add {C}{C}.",
         "[]", "[]", False, True),
        ("draw1", "orc6", "Phyrexian Arena", "{1}{B}{B}", 3.0,
         "Enchantment", "At the beginning of your upkeep, you draw a card and you lose 1 life.",
         '["B"]', "[]", False, True),
        ("removal1", "orc7", "Beast Within", "{2}{G}", 3.0,
         "Instant", "Destroy target permanent. Its controller creates a 3/3 green Beast creature token.",
         '["G"]', "[]", False, True),
        ("wipe1", "orc8", "Damnation", "{2}{B}{B}", 4.0,
         "Sorcery", "Destroy all creatures. They can't be regenerated.",
         '["B"]', "[]", False, True),
        ("tutor1", "orc9", "Demonic Tutor", "{1}{B}", 2.0,
         "Sorcery", "Search your library for a card, put that card into your hand, then shuffle.",
         '["B"]', "[]", False, True),
        ("filler1", "orc10", "Grizzly Bears", "{1}{G}", 2.0,
         "Creature — Bear", "Vanilla 2/2",
         '["G"]', "[]", False, True),
    ]

    for card in cards:
        conn.execute(
            "INSERT OR IGNORE INTO cards (id, oracle_id, name, mana_cost, cmc, "
            "type_line, oracle_text, color_identity, keywords, is_legal_commander, is_legal_in_99) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            card,
        )

    # Insert two test decks from Game Knights source
    conn.execute(
        "INSERT INTO decks (id, source, source_id, commander_id, deck_name) "
        "VALUES ('deck1', 'archidekt_gameknights', 'url1', 'cmd1', 'Korvold Deck 1')"
    )
    conn.execute(
        "INSERT INTO decks (id, source, source_id, commander_id, deck_name) "
        "VALUES ('deck2', 'archidekt_gameknights', 'url2', 'cmd1', 'Korvold Deck 2')"
    )

    # Deck 1 cards: commander + lands + functional pieces
    deck1_cards = [
        ("deck1", "cmd1", 1, True),
        ("deck1", "land1", 1, False),
        ("deck1", "land2", 1, False),
        ("deck1", "land3", 1, False),
        ("deck1", "ramp1", 1, False),
        ("deck1", "draw1", 1, False),
        ("deck1", "removal1", 1, False),
        ("deck1", "wipe1", 1, False),
        ("deck1", "tutor1", 1, False),
        ("deck1", "filler1", 1, False),
    ]

    # Deck 2 cards: similar but without tutor
    deck2_cards = [
        ("deck2", "cmd1", 1, True),
        ("deck2", "land1", 1, False),
        ("deck2", "land2", 1, False),
        ("deck2", "ramp1", 1, False),
        ("deck2", "draw1", 1, False),
        ("deck2", "removal1", 1, False),
        ("deck2", "wipe1", 1, False),
        ("deck2", "filler1", 1, False),
    ]

    for row in deck1_cards + deck2_cards:
        conn.execute(
            "INSERT OR IGNORE INTO deck_cards (deck_id, card_id, quantity, is_commander) "
            "VALUES (?, ?, ?, ?)",
            row,
        )

    conn.commit()
    conn.close()


def test_analyzer_with_test_db() -> None:
    """GameKnightsAnalyzer produces correct patterns from test data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _create_test_db(db_path)

        analyzer = GameKnightsAnalyzer(db_path)
        patterns = analyzer.analyze()

        assert patterns.deck_count == 2
        assert patterns.land_counts.mean > 0
        assert patterns.ramp_counts.mean > 0
        assert patterns.draw_counts.mean > 0
        assert patterns.removal_counts.mean > 0
        assert patterns.wipe_counts.mean > 0


def test_analyzer_no_decks() -> None:
    """Analyzer returns empty patterns when no matching decks exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _create_test_db(db_path)

        analyzer = GameKnightsAnalyzer(db_path)
        patterns = analyzer.analyze(source_filter="nonexistent_source")

        assert patterns.deck_count == 0
        assert patterns.land_counts.mean == 0.0


def test_analyzer_most_played_cards() -> None:
    """Most-played cards are correctly identified across decks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _create_test_db(db_path)

        analyzer = GameKnightsAnalyzer(db_path)
        patterns = analyzer.analyze()

        # Sol Ring, Phyrexian Arena, Beast Within, Damnation, Grizzly Bears
        # appear in both decks
        card_names = {c["name"] for c in patterns.most_played_cards}
        assert "Sol Ring" in card_names
        assert "Phyrexian Arena" in card_names


def test_analyzer_color_distribution() -> None:
    """Color distribution reflects commander colors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _create_test_db(db_path)

        analyzer = GameKnightsAnalyzer(db_path)
        patterns = analyzer.analyze()

        # Both decks use Korvold (BRG) so each color appears twice
        assert patterns.color_distribution.get("B", 0) == 2
        assert patterns.color_distribution.get("R", 0) == 2
        assert patterns.color_distribution.get("G", 0) == 2


# --- KnowledgeBaseBuilder tests ---


def test_builder_produces_markdown() -> None:
    """Builder produces a non-empty markdown string with expected sections."""
    patterns = DeckbuildingPatterns(
        deck_count=10,
        land_counts=ComponentStats(mean=36.0, median=36.0, min=33.0, max=39.0, std_dev=1.5),
        ramp_counts=ComponentStats(mean=11.0, median=11.0, min=8.0, max=14.0, std_dev=1.8),
        draw_counts=ComponentStats(mean=10.0, median=10.0, min=7.0, max=13.0, std_dev=1.7),
        removal_counts=ComponentStats(mean=9.0, median=9.0, min=6.0, max=12.0, std_dev=1.6),
        wipe_counts=ComponentStats(mean=3.0, median=3.0, min=2.0, max=5.0, std_dev=0.9),
        tutor_counts=ComponentStats(mean=2.0, median=2.0, min=0.0, max=5.0, std_dev=1.2),
        avg_cmc=ComponentStats(mean=3.1, median=3.0, min=2.5, max=3.8, std_dev=0.4),
        mana_curve={0: 2.0, 1: 8.0, 2: 14.0, 3: 12.0, 4: 8.0, 5: 5.0, 6: 3.0, 7: 2.0},
        color_distribution={"W": 3, "U": 4, "B": 7, "R": 5, "G": 6},
        most_played_cards=[
            {"name": "Sol Ring", "deck_count": 10, "percentage": 100.0},
            {"name": "Arcane Signet", "deck_count": 9, "percentage": 90.0},
        ],
    )

    builder = KnowledgeBaseBuilder()
    result = builder.build(patterns)

    assert "# Deckbuilding Knowledge Base" in result
    assert "## Land Count" in result
    assert "## Ramp" in result
    assert "## Card Draw" in result
    assert "## Removal & Interaction" in result
    assert "## Mana Curve" in result
    assert "## Power Level Context" in result
    assert "## Most Popular Cards" in result
    assert "## Budget Considerations" in result
    assert "Sol Ring" in result
    assert "36.0" in result  # Land count mean


def test_builder_with_edhrec_articles() -> None:
    """Builder incorporates EDHREC article text into sections."""
    patterns = DeckbuildingPatterns(deck_count=5)
    articles = [
        "When building your mana base, the land count should be around 36-38 "
        "for most Commander decks. Ramp is equally critical."
    ]

    builder = KnowledgeBaseBuilder()
    result = builder.build(patterns, articles)

    assert "EDHREC guidance:" in result
    assert "land count" in result.lower() or "mana base" in result.lower()


def test_builder_empty_patterns() -> None:
    """Builder handles zero-deck patterns gracefully."""
    patterns = DeckbuildingPatterns()
    builder = KnowledgeBaseBuilder()
    result = builder.build(patterns)

    assert "0 decks analyzed" in result
    assert "## Land Count" in result


def test_builder_sections_separated_by_double_newlines() -> None:
    """Sections are separated by double newlines for chunker compatibility."""
    patterns = DeckbuildingPatterns(deck_count=1)
    builder = KnowledgeBaseBuilder()
    result = builder.build(patterns)

    # Each section header should be preceded by a double newline
    sections = result.split("\n\n")
    assert len(sections) > 5  # At least the major sections


# --- Config tests ---


def test_knowledge_base_settings_defaults() -> None:
    """KnowledgeBaseSettings has correct defaults."""
    s = KnowledgeBaseSettings()
    assert s.game_knights_archidekt_owner == "GameKnights"
    assert s.game_knights_fallback_deck_ids == []
    assert s.edhrec_articles == []


def test_settings_includes_knowledge_base() -> None:
    """Top-level Settings includes knowledge_base field."""
    s = Settings()
    assert hasattr(s, "knowledge_base")
    assert isinstance(s.knowledge_base, KnowledgeBaseSettings)


def test_load_settings_with_kb_config() -> None:
    """load_settings picks up knowledge_base from settings.yaml."""
    config_path = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
    if not config_path.exists():
        return  # Skip if config not available

    settings = load_settings(config_path)
    assert settings.knowledge_base.game_knights_archidekt_owner == "GameKnights"
    assert len(settings.knowledge_base.edhrec_articles) >= 2


# --- GameKnightsIngestion tests ---


def test_game_knights_ingestion_attributes() -> None:
    """GameKnightsIngestion has correct name and source_name."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        # Create minimal DB with source_health table
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE source_health (source TEXT PRIMARY KEY, "
            "last_successful_sync TIMESTAMP, last_failed_sync TIMESTAMP, "
            "last_error TEXT, consecutive_failures INTEGER DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        ingestion = GameKnightsIngestion(db_path)
        assert ingestion.name == "archidekt_gameknights"
        assert ingestion.source_name == "archidekt_gameknights"


# --- Integration: analyzer + builder pipeline ---


def test_full_analysis_to_kb_pipeline() -> None:
    """End-to-end test: analyze decks and build knowledge base."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        _create_test_db(db_path)

        # Analyze
        analyzer = GameKnightsAnalyzer(db_path)
        patterns = analyzer.analyze()

        # Build KB
        builder = KnowledgeBaseBuilder()
        kb_text = builder.build(patterns)

        assert len(kb_text) > 500
        assert "2 decks analyzed" in kb_text
        assert "## Land Count" in kb_text

        # Write to file and chunk
        kb_path = Path(tmpdir) / "kb.txt"
        kb_path.write_text(kb_text)

        from sabermetrics.reference_layer.chunker import DocumentChunker

        chunker = DocumentChunker()
        chunks = chunker.chunk_article(kb_path, tier=2)
        assert len(chunks) > 0
        assert all(c.tier == 2 for c in chunks)
