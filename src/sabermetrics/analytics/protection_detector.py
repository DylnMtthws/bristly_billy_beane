"""Protection card detection and protection_candidates table population.

Strips parenthetical reminder text before pattern matching to eliminate
false positives from keyword reminder text. Populates a pre-scored
protection_candidates SQLite table for the protection generator to query.
"""

import logging
import re
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Detection version — bump when patterns change to force re-computation
DETECTION_VERSION = "1.0.0"


def _strip_reminder_text(oracle: str) -> str:
    """Remove parenthetical reminder text from oracle text.

    Args:
        oracle: Raw oracle text.

    Returns:
        Oracle text with all parenthetical expressions removed.
    """
    return re.sub(r"\([^)]*\)", "", oracle)


# --- Positive patterns (applied to reminder-stripped text) ---

_POSITIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("hexproof", re.compile(r"\bhexproof\b", re.IGNORECASE)),
    ("indestructible", re.compile(r"\bindestructible\b", re.IGNORECASE)),
    ("phase_out", re.compile(r"\bphase(?:s)? out\b", re.IGNORECASE)),
    ("protection_from", re.compile(r"protection from", re.IGNORECASE)),
    ("ward", re.compile(r"\bward\b", re.IGNORECASE)),
    ("shroud", re.compile(r"\bshroud\b", re.IGNORECASE)),
    ("redirect", re.compile(r"choose new targets|change the target", re.IGNORECASE)),
    ("totem_armor", re.compile(r"totem armor", re.IGNORECASE)),
]

# --- Negative patterns (applied to original text) ---

_NEGATIVE_PATTERNS: list[re.Pattern] = [
    re.compile(r"opponent.*gains? hexproof", re.IGNORECASE),
    re.compile(r"loses hexproof|loses indestructible", re.IGNORECASE),
]

# --- Classification regexes ---

_PHASING = re.compile(r"phase(?:s)? out", re.IGNORECASE)
_HEXPROOF = re.compile(r"\bhexproof\b|can't be the target", re.IGNORECASE)
_INDESTRUCTIBLE = re.compile(r"\bindestructible\b|can't be destroyed", re.IGNORECASE)
_REDIRECT = re.compile(r"change the target|choose new targets|changes? its target", re.IGNORECASE)
_WARD = re.compile(r"\bward\b", re.IGNORECASE)
_SHROUD = re.compile(r"\bshroud\b", re.IGNORECASE)
_PROTECTION_FROM = re.compile(r"protection from", re.IGNORECASE)
_TOTEM_ARMOR = re.compile(r"totem armor", re.IGNORECASE)
_BOARD_WIDE = re.compile(
    r"permanents you control|creatures you control|each (?:creature|permanent) you control",
    re.IGNORECASE,
)
_FREE_CAST = re.compile(
    r"without paying (?:its|their) mana cost|if you control a commander",
    re.IGNORECASE,
)


def _classify_protection_type(oracle_stripped: str) -> str:
    """Classify protection into phasing/hexproof/indestructible/redirect/ward/other."""
    if _PHASING.search(oracle_stripped):
        return "phasing"
    if _REDIRECT.search(oracle_stripped):
        return "redirect"
    if _INDESTRUCTIBLE.search(oracle_stripped):
        return "indestructible"
    if _HEXPROOF.search(oracle_stripped) or _SHROUD.search(oracle_stripped):
        return "hexproof"
    if _PROTECTION_FROM.search(oracle_stripped):
        return "protection_from"
    if _TOTEM_ARMOR.search(oracle_stripped):
        return "totem_armor"
    if _WARD.search(oracle_stripped):
        return "ward"
    return "other"


def _coverage_score(oracle: str) -> float:
    """Score protection coverage type (1.0-4.0).

    Phasing is best (dodges everything including exile/sacrifice).
    Hexproof + indestructible together is next.
    Then individual protection modes.
    """
    has_phasing = bool(_PHASING.search(oracle))
    has_hexproof = bool(_HEXPROOF.search(oracle))
    has_indestructible = bool(_INDESTRUCTIBLE.search(oracle))
    has_redirect = bool(_REDIRECT.search(oracle))
    has_protection_from = bool(_PROTECTION_FROM.search(oracle))
    has_shroud = bool(_SHROUD.search(oracle))
    has_totem = bool(_TOTEM_ARMOR.search(oracle))
    has_ward = bool(_WARD.search(oracle))

    if has_phasing:
        return 4.0
    if has_hexproof and has_indestructible:
        return 3.0
    if has_redirect:
        return 2.5
    if has_indestructible:
        return 2.0
    if has_protection_from:
        return 1.8
    if has_hexproof or has_shroud:
        return 1.5
    if has_totem:
        return 1.3
    if has_ward:
        return 1.0
    return 1.0


def _score_protection_card(
    oracle_stripped: str,
    type_line: str,
    cmc: float,
) -> float:
    """Compute a protection quality score.

    Combines coverage type, breadth, mana efficiency, and instant speed.
    Score is normalized to roughly 0-1 range.

    Args:
        oracle_stripped: Oracle text with reminder text stripped.
        type_line: Card type line.
        cmc: Converted mana cost.

    Returns:
        Score in roughly 0-1 range.
    """
    role_score = 0.0

    # Coverage type
    role_score += _coverage_score(oracle_stripped)

    # Breadth (board-wide vs single target)
    if _BOARD_WIDE.search(oracle_stripped):
        role_score += 1.5

    # Mana efficiency
    if _FREE_CAST.search(oracle_stripped):
        role_score += 3.0
    elif cmc <= 1:
        role_score += 2.5
    elif cmc <= 2:
        role_score += 2.0
    elif cmc <= 3:
        role_score += 1.0
    else:
        role_score += 0.0

    # Instant speed
    type_lower = type_line.lower()
    if "instant" in type_lower or "flash" in oracle_stripped.lower():
        role_score += 2.0
    elif "sorcery" in type_lower:
        role_score -= 1.0
    else:
        role_score += 0.5

    # Normalize to 0-1 (max theoretical ~10.5)
    return min(role_score / 10.5, 1.0)


def detect_protection_card(card: dict) -> dict | None:
    """Detect whether a card is a protection card and return its metadata.

    Strips parenthetical reminder text before pattern matching to avoid
    false positives from keyword reminder text.

    Args:
        card: Card dict with oracle_text, type_line, cmc keys.

    Returns:
        Dict with protection metadata if card is protection, None otherwise.
    """
    oracle = card.get("oracle_text") or ""
    type_line = card.get("type_line") or ""
    cmc = float(card.get("cmc", 0) or 0)

    oracle_stripped = _strip_reminder_text(oracle)

    # Check negative patterns on original text
    for neg_pat in _NEGATIVE_PATTERNS:
        if neg_pat.search(oracle):
            return None

    # Check positive patterns on stripped text
    matched_pattern = None
    for pattern_name, pat in _POSITIVE_PATTERNS:
        if pat.search(oracle_stripped):
            matched_pattern = pattern_name
            break

    if matched_pattern is None:
        return None

    protection_type = _classify_protection_type(oracle_stripped)
    is_board_wide = bool(_BOARD_WIDE.search(oracle_stripped))
    type_lower = type_line.lower()
    is_instant = "instant" in type_lower or "flash" in oracle_stripped.lower()
    is_free_cast = bool(_FREE_CAST.search(oracle_stripped))

    protection_score = _score_protection_card(oracle_stripped, type_line, cmc)

    return {
        "protection_type": protection_type,
        "is_board_wide": is_board_wide,
        "is_instant": is_instant,
        "is_free_cast": is_free_cast,
        "coverage_score": round(_coverage_score(oracle_stripped), 4),
        "protection_score": round(protection_score, 4),
    }


def _ensure_protection_table(conn: sqlite3.Connection) -> None:
    """Create protection_candidates table if it doesn't exist."""
    conn.execute("""
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
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_protection_candidates_score "
        "ON protection_candidates(protection_score DESC)"
    )
    conn.commit()


def populate_protection_candidates(db_path: Path) -> dict:
    """Scan all Commander-legal cards and populate protection_candidates table.

    Args:
        db_path: Path to SQLite database.

    Returns:
        Dict with population statistics.
    """
    start = time.time()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        _ensure_protection_table(conn)

        # Check if already populated at current version
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM protection_candidates WHERE detection_version = ?",
                (DETECTION_VERSION,),
            ).fetchone()
            if row and row[0] > 0:
                logger.info(
                    "protection_candidates already populated at version %s (%d rows)",
                    DETECTION_VERSION, row[0],
                )
                return {
                    "rows": row[0],
                    "skipped": True,
                    "version": DETECTION_VERSION,
                    "duration_seconds": 0.0,
                }
        except sqlite3.OperationalError:
            pass

        # Fetch all Commander-legal cards
        cursor = conn.execute(
            "SELECT id, name, oracle_text, type_line, cmc "
            "FROM cards "
            "WHERE is_legal_in_99 = 1"
        )
        cards = [dict(row) for row in cursor.fetchall()]
        logger.info("Scanning %d Commander-legal cards for protection detection", len(cards))

        # Clear previous version data
        conn.execute(
            "DELETE FROM protection_candidates WHERE detection_version != ?",
            (DETECTION_VERSION,),
        )

        inserts: list[tuple] = []
        for card in cards:
            result = detect_protection_card(card)
            if result is not None:
                inserts.append((
                    card["id"],
                    result["protection_type"],
                    result["is_board_wide"],
                    result["is_instant"],
                    result["is_free_cast"],
                    result["coverage_score"],
                    result["protection_score"],
                    DETECTION_VERSION,
                ))

        # Batch insert
        conn.executemany(
            "INSERT OR REPLACE INTO protection_candidates "
            "(card_id, protection_type, is_board_wide, is_instant, "
            "is_free_cast, coverage_score, protection_score, "
            "detection_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            inserts,
        )
        conn.commit()

        duration = time.time() - start
        logger.info(
            "Populated protection_candidates: %d cards in %.1fs",
            len(inserts), duration,
        )

        return {
            "rows": len(inserts),
            "skipped": False,
            "version": DETECTION_VERSION,
            "duration_seconds": round(duration, 2),
        }
    finally:
        conn.close()
