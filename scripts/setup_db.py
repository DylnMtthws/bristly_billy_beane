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
        owner_id TEXT,
        deck_name TEXT,
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
    # NB: idx on generated_decks(owner_id) is created in ensure_portal_schema(),
    # after the column is ALTER-added — an index here would fail on pre-existing
    # DBs whose generated_decks lacks the column until the migration runs.
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
        user_id TEXT,
        deck_id TEXT,
        metadata TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cost_timestamp ON cost_log(timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_cost_call_type ON cost_log(call_type)",
    # idx on cost_log(user_id)/(deck_id) are created in ensure_portal_schema(),
    # after the columns are ALTER-added (see note on generated_decks above).
    """
    CREATE TABLE IF NOT EXISTS source_health (
        source TEXT PRIMARY KEY,
        last_successful_sync TIMESTAMP,
        last_failed_sync TIMESTAMP,
        last_error TEXT,
        consecutive_failures INTEGER DEFAULT 0
    )
    """,
    # 1.13 Generation Traces (per-card decision history)
    """
    CREATE TABLE IF NOT EXISTS generation_traces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        generation_id TEXT NOT NULL,
        card_name TEXT NOT NULL,
        card_id TEXT,
        stage TEXT NOT NULL,
        action TEXT NOT NULL,
        score REAL,
        score_components_json TEXT,
        reason TEXT,
        timestamp REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_traces_gen ON generation_traces(generation_id)",
    "CREATE INDEX IF NOT EXISTS idx_traces_card ON generation_traces(card_name)",
    # 1.10 Ramp Candidates (auto-populated by ramp_detector)
    """
    CREATE TABLE IF NOT EXISTS ramp_candidates (
        card_id TEXT PRIMARY KEY,
        ramp_type TEXT NOT NULL,
        net_mana_rate REAL,
        mana_output REAL,
        produces_colored BOOLEAN,
        is_conditional BOOLEAN,
        is_restricted BOOLEAN,
        resilience_tier INTEGER,
        ramp_score REAL,
        detection_version TEXT,
        computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (card_id) REFERENCES cards(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ramp_candidates_score ON ramp_candidates(ramp_score DESC)",
    # 1.11 Removal Candidates (auto-populated by removal_detector)
    """
    CREATE TABLE IF NOT EXISTS removal_candidates (
        card_id TEXT PRIMARY KEY,
        removal_type TEXT NOT NULL,
        target_type TEXT,
        is_exile BOOLEAN,
        is_instant BOOLEAN,
        is_free_cast BOOLEAN,
        flexibility_score REAL,
        removal_score REAL,
        detection_version TEXT,
        computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (card_id) REFERENCES cards(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_removal_candidates_score ON removal_candidates(removal_score DESC)",
    # 1.12 Protection Candidates (auto-populated by protection_detector)
    """
    CREATE TABLE IF NOT EXISTS protection_candidates (
        card_id TEXT PRIMARY KEY,
        protection_type TEXT NOT NULL,
        is_board_wide BOOLEAN,
        is_instant BOOLEAN,
        is_free_cast BOOLEAN,
        coverage_score REAL,
        protection_score REAL,
        detection_version TEXT,
        computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (card_id) REFERENCES cards(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_protection_candidates_score ON protection_candidates(protection_score DESC)",
    # 1.14 Multi-user portal: accounts, invites, favorites, feedback
    """
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE,
        display_name TEXT,
        avatar_emoji TEXT,
        password_hash TEXT,
        tailscale_login TEXT,
        failed_login_count INTEGER DEFAULT 0,
        locked_until TIMESTAMP,
        role TEXT NOT NULL DEFAULT 'user',
        status TEXT NOT NULL DEFAULT 'invited',
        monthly_deck_quota INTEGER,
        invited_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_login_at TIMESTAMP,
        FOREIGN KEY (invited_by) REFERENCES users(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)",
    "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)",
    """
    CREATE TABLE IF NOT EXISTS invite_tokens (
        token TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        expires_at TIMESTAMP,
        used_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_invite_tokens_user ON invite_tokens(user_id)",
    """
    CREATE TABLE IF NOT EXISTS favorite_commanders (
        user_id TEXT NOT NULL,
        commander_id TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, commander_id),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (commander_id) REFERENCES cards(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS favorite_decks (
        user_id TEXT NOT NULL,
        deck_id TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, deck_id),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (deck_id) REFERENCES generated_decks(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS card_feedback (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        deck_id TEXT NOT NULL,
        card_id TEXT,
        card_name TEXT NOT NULL,
        vote TEXT,
        comment TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, deck_id, card_id),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (deck_id) REFERENCES generated_decks(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_card_feedback_name ON card_feedback(card_name)",
    "CREATE INDEX IF NOT EXISTS idx_card_feedback_deck ON card_feedback(deck_id)",
    "CREATE INDEX IF NOT EXISTS idx_card_feedback_user ON card_feedback(user_id)",
    """
    CREATE TABLE IF NOT EXISTS deck_feedback (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        deck_id TEXT NOT NULL,
        verdict TEXT,
        comment TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, deck_id),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (deck_id) REFERENCES generated_decks(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_deck_feedback_deck ON deck_feedback(deck_id)",
    "CREATE INDEX IF NOT EXISTS idx_deck_feedback_user ON deck_feedback(user_id)",
]


# Canonical commander source for the Explore page: one row per commander NAME
# (cheapest legal printing), exposing a computed price_usd. Mirrors the
# card_candidates view in analytics/filters.py but filters to legal commanders.
# Uses SELECT * so it inherits any runtime-added `cards` columns (role_tags,
# functional_categories) without referencing columns that may not exist yet.
COMMANDER_CANDIDATE_VIEW_SQL = """
CREATE VIEW IF NOT EXISTS commander_candidates AS
WITH latest AS (
    SELECT MAX(snapshot_date) AS d FROM card_prices
),
latest_prices AS (
    SELECT cp.card_id, cp.price_usd
    FROM card_prices cp, latest
    WHERE cp.snapshot_date = latest.d
),
ranked AS (
    SELECT
        c.*,
        lp.price_usd AS price_usd,
        ROW_NUMBER() OVER (
            PARTITION BY c.name
            ORDER BY (lp.price_usd IS NULL) ASC, lp.price_usd ASC, c.id ASC
        ) AS _rn
    FROM cards c
    LEFT JOIN latest_prices lp ON lp.card_id = c.id
    WHERE c.is_legal_commander = 1
)
SELECT * FROM ranked WHERE _rn = 1
"""


def ensure_portal_schema(conn: sqlite3.Connection) -> None:
    """Idempotently apply multi-user portal migrations to an existing database.

    The new portal tables live in DDL_STATEMENTS (created for fresh DBs). This
    handler covers what CREATE TABLE IF NOT EXISTS cannot: adding columns to
    pre-existing tables and creating the commander_candidates view. Mirrors the
    PRAGMA table_info + ALTER TABLE pattern in analytics/role_tagger. Safe to
    run repeatedly and on an already-populated database.
    """
    column_migrations = {
        "generated_decks": [("owner_id", "TEXT"), ("deck_name", "TEXT")],
        "cost_log": [("user_id", "TEXT"), ("deck_id", "TEXT")],
        # Tailnet identity (ADR-026). Its own column rather than reuse of
        # `email`, because a Tailscale login is not always an email address:
        # GitHub SSO renders as "someone@github".
        "users": [
            ("tailscale_login", "TEXT"),
            # Per-account lockout (ADR-027). IP rate limiting alone is weak
            # once /login faces the internet: an attacker rotates addresses,
            # and Funnel traffic may share one.
            ("failed_login_count", "INTEGER"),
            ("locked_until", "TIMESTAMP"),
            ("session_version", "INTEGER NOT NULL DEFAULT 0"),
        ],
    }
    for table, cols in column_migrations.items():
        cursor = conn.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cursor.fetchall()}
        for col_name, col_type in cols:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")

    conn.execute(COMMANDER_CANDIDATE_VIEW_SQL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_generated_decks_owner "
        "ON generated_decks(owner_id)"
    )
    # UNIQUE so one tailnet identity cannot map to two accounts. Partial, so
    # any number of password-only accounts may have a NULL login.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tailscale_login "
        "ON users(tailscale_login) WHERE tailscale_login IS NOT NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_user ON cost_log(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_deck ON cost_log(deck_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token_digest TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            session_version INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0
        )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_password_reset_user_created "
        "ON password_reset_tokens(user_id, created_at)"
    )
    conn.commit()


def ensure_cedh_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create the cEDH Deck Lab tables.

    Separate from ensure_portal_schema so the cEDH path can be added to an
    existing database without touching the casual generator's tables. Safe to
    run repeatedly.

    The candidate document is stored as JSON rather than shredded into
    columns: it is a versioned artifact handed to another repository, and the
    thing that must survive a schema change here is the document itself.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cedh_candidates (
            candidate_id TEXT PRIMARY KEY,
            owner_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            pack_id TEXT NOT NULL,
            commander_key TEXT NOT NULL,
            commander_name TEXT NOT NULL,
            deck_sha256 TEXT NOT NULL,
            candidate_json TEXT NOT NULL,
            evidence_hash TEXT,
            meta_available INTEGER DEFAULT 0,
            simulation_status TEXT,
            simulation_json TEXT,
            explanation_json TEXT,
            warnings_json TEXT
        )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cedh_owner "
        "ON cedh_candidates(owner_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cedh_commander "
        "ON cedh_candidates(commander_key)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS build_jobs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL,
            request_json TEXT NOT NULL,
            candidate_id TEXT,
            error_code TEXT,
            error_detail TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (candidate_id) REFERENCES cedh_candidates(candidate_id)
        )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_build_jobs_user_created "
        "ON build_jobs(user_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_build_jobs_status ON build_jobs(status)"
    )
    conn.commit()


def setup_database(db_path: Path) -> None:
    """Create all tables and indexes in the database.

    Args:
        db_path: Path to the SQLite database file.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        # Enable WAL mode for better concurrent read performance
        conn.execute("PRAGMA journal_mode=WAL")
        # Enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON")

        for ddl in DDL_STATEMENTS:
            conn.execute(ddl)

        # Idempotent column/view migrations for pre-existing databases
        ensure_portal_schema(conn)
        ensure_cedh_schema(conn)

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
