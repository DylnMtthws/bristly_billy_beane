"""Create the Sabermetrics SQLite database with all tables.

Idempotent: uses CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
Run: python scripts/setup_db.py [--db-path data/sabermetrics.db]
"""

import argparse
import sqlite3
from pathlib import Path

DDL_STATEMENTS = [
    # 1.1 Cards and Pricing
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
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name)",
    "CREATE INDEX IF NOT EXISTS idx_cards_oracle_id ON cards(oracle_id)",
    "CREATE INDEX IF NOT EXISTS idx_cards_legal_commander ON cards(is_legal_commander)",
    """
    CREATE TABLE IF NOT EXISTS card_prices (
        card_id TEXT,
        price_usd REAL,
        price_usd_foil REAL,
        snapshot_date DATE,
        source TEXT DEFAULT 'scryfall',
        PRIMARY KEY (card_id, snapshot_date),
        FOREIGN KEY (card_id) REFERENCES cards(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_prices_card_date ON card_prices(card_id, snapshot_date DESC)",
    # 1.2 Decks
    """
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
        UNIQUE(source, source_id),
        FOREIGN KEY (commander_id) REFERENCES cards(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_decks_commander ON decks(commander_id)",
    "CREATE INDEX IF NOT EXISTS idx_decks_source ON decks(source)",
    """
    CREATE TABLE IF NOT EXISTS deck_cards (
        deck_id TEXT,
        card_id TEXT,
        quantity INTEGER DEFAULT 1,
        is_commander BOOLEAN DEFAULT FALSE,
        PRIMARY KEY (deck_id, card_id),
        FOREIGN KEY (deck_id) REFERENCES decks(id),
        FOREIGN KEY (card_id) REFERENCES cards(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_deck_cards_card ON deck_cards(card_id)",
    # 1.3 Tournament Results
    """
    CREATE TABLE IF NOT EXISTS tournament_results (
        id TEXT PRIMARY KEY,
        tournament_id TEXT,
        player_name TEXT,
        deck_id TEXT,
        commander_id TEXT,
        standing INTEGER,
        win_rate REAL,
        games_played INTEGER,
        games_won INTEGER,
        tournament_date DATE,
        FOREIGN KEY (deck_id) REFERENCES decks(id),
        FOREIGN KEY (commander_id) REFERENCES cards(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tourney_commander ON tournament_results(commander_id)",
    "CREATE INDEX IF NOT EXISTS idx_tourney_date ON tournament_results(tournament_date DESC)",
    # 1.4 EDHREC Data
    """
    CREATE TABLE IF NOT EXISTS edhrec_commander_data (
        commander_id TEXT PRIMARY KEY,
        themes TEXT,
        salt_score REAL,
        deck_count INTEGER,
        top_cards TEXT,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (commander_id) REFERENCES cards(id)
    )
    """,
    # 1.5 Derived Analytics
    """
    CREATE TABLE IF NOT EXISTS card_cooccurrence (
        card_a_id TEXT,
        card_b_id TEXT,
        commander_id TEXT,
        cooccurrence_count INTEGER,
        cooccurrence_rate REAL,
        PRIMARY KEY (card_a_id, card_b_id, commander_id),
        FOREIGN KEY (card_a_id) REFERENCES cards(id),
        FOREIGN KEY (card_b_id) REFERENCES cards(id),
        FOREIGN KEY (commander_id) REFERENCES cards(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cooccurrence_lookup ON card_cooccurrence(commander_id, card_a_id)",
    """
    CREATE TABLE IF NOT EXISTS card_win_equity (
        card_id TEXT,
        commander_id TEXT,
        win_rate_when_present REAL,
        win_rate_when_absent REAL,
        cwe_score REAL,
        sample_size INTEGER,
        confidence REAL,
        last_computed TIMESTAMP,
        PRIMARY KEY (card_id, commander_id),
        FOREIGN KEY (card_id) REFERENCES cards(id),
        FOREIGN KEY (commander_id) REFERENCES cards(id)
    )
    """,
    # 1.6 Profiles and Generated Decks
    """
    CREATE TABLE IF NOT EXISTS commander_profiles (
        commander_id TEXT PRIMARY KEY,
        profile_json TEXT NOT NULL,
        user_intent TEXT,
        user_intent_hash TEXT,
        set_version TEXT NOT NULL,
        evidence_sources TEXT,
        generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_validated_at TIMESTAMP,
        is_stale BOOLEAN DEFAULT FALSE,
        schema_version TEXT DEFAULT '1.0',
        FOREIGN KEY (commander_id) REFERENCES cards(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_profiles_stale ON commander_profiles(is_stale)",
    """
    CREATE TABLE IF NOT EXISTS generated_decks (
        id TEXT PRIMARY KEY,
        commander_id TEXT NOT NULL,
        profile_id TEXT,
        budget_usd REAL,
        power_target INTEGER,
        strategy TEXT,
        cards_json TEXT,
        rationale TEXT,
        cvar_score REAL,
        estimated_bracket INTEGER,
        generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (commander_id) REFERENCES cards(id)
    )
    """,
    # 1.7 Reference Layer
    """
    CREATE TABLE IF NOT EXISTS reference_chunks (
        id TEXT PRIMARY KEY,
        document TEXT NOT NULL,
        section TEXT,
        tier INTEGER NOT NULL,
        content TEXT NOT NULL,
        embedding BLOB,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunks_document ON reference_chunks(document)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_tier ON reference_chunks(tier)",
    """
    CREATE TABLE IF NOT EXISTS card_rulings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_oracle_id TEXT NOT NULL,
        ruling_date DATE,
        ruling_text TEXT NOT NULL,
        source TEXT DEFAULT 'mtgapi',
        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rulings_oracle ON card_rulings(card_oracle_id)",
    "CREATE INDEX IF NOT EXISTS idx_rulings_date ON card_rulings(ruling_date DESC)",
    # 1.8 Combos
    """
    CREATE TABLE IF NOT EXISTS combos (
        id TEXT PRIMARY KEY,
        cards TEXT NOT NULL,
        color_identity TEXT,
        description TEXT,
        result TEXT,
        prerequisites TEXT,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_combos_color ON combos(color_identity)",
    # 1.9 Operational Tables
    """
    CREATE TABLE IF NOT EXISTS _schema_version (
        version TEXT PRIMARY KEY,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        description TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cost_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        call_type TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER,
        cached_input_tokens INTEGER,
        output_tokens INTEGER,
        cost_usd REAL,
        request_id TEXT,
        metadata TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cost_timestamp ON cost_log(timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_cost_call_type ON cost_log(call_type)",
    """
    CREATE TABLE IF NOT EXISTS source_health (
        source TEXT PRIMARY KEY,
        last_successful_sync TIMESTAMP,
        last_failed_sync TIMESTAMP,
        last_error TEXT,
        consecutive_failures INTEGER DEFAULT 0
    )
    """,
]


def setup_database(db_path: Path) -> None:
    """Create all tables and indexes in the database.

    Args:
        db_path: Path to the SQLite database file.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        # Enable WAL mode for better concurrent read performance
        conn.execute("PRAGMA journal_mode=WAL")
        # Enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON")

        for ddl in DDL_STATEMENTS:
            conn.execute(ddl)

        # Insert initial schema version
        conn.execute(
            "INSERT OR IGNORE INTO _schema_version VALUES ('1.0', CURRENT_TIMESTAMP, 'Initial schema')"
        )

        conn.commit()
        print(f"Database created at {db_path}")

        # Verify table count
        cursor = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        table_count = cursor.fetchone()[0]
        print(f"Tables created: {table_count}")
    finally:
        conn.close()


def main() -> None:
    """Entry point for setup_db script."""
    parser = argparse.ArgumentParser(description="Set up the Sabermetrics database")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("data/sabermetrics.db"),
        help="Path to the SQLite database file",
    )
    args = parser.parse_args()
    setup_database(args.db_path)


if __name__ == "__main__":
    main()
