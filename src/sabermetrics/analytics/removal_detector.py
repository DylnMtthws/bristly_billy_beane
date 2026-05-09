"""Removal card detection and removal_candidates table population.

Strips parenthetical reminder text before pattern matching to eliminate
false positives from keyword reminder text. Populates a pre-scored
removal_candidates SQLite table for the removal generator to query.
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
    ("destroy_target", re.compile(r"destroy target", re.IGNORECASE)),
    ("exile_target", re.compile(r"exile target", re.IGNORECASE)),
    ("damage_target", re.compile(
        r"deals \d+ damage to (?:target|any|each)",
        re.IGNORECASE,
    )),
    ("minus_counters", re.compile(
        r"target.*gets? -\d+/-\d+",
        re.IGNORECASE,
    )),
    ("counter_spell", re.compile(r"counter target spell", re.IGNORECASE)),
    ("board_wipe_destroy", re.compile(r"destroy all", re.IGNORECASE)),
    ("board_wipe_exile", re.compile(r"exile all", re.IGNORECASE)),
    ("bounce", re.compile(
        r"return target.*to.*(?:owner's |its owner's )?hand",
        re.IGNORECASE,
    )),
]

# --- Negative patterns (applied to original text to catch reminder-embedded caveats) ---

_NEGATIVE_PATTERNS: list[re.Pattern] = [
    re.compile(r"destroy target.*you control", re.IGNORECASE),
    re.compile(r"sacrifice ~this(?!.*target)", re.IGNORECASE),
]

# --- Classification helpers ---

_EXILE_EFFECT = re.compile(
    r"exile target|exiles target|exile all|exile each",
    re.IGNORECASE,
)
_COUNTER_SPELL = re.compile(
    r"counter target (?:spell|activated|triggered)",
    re.IGNORECASE,
)
_BOARD_WIPE = re.compile(
    r"destroy all|exile all|deals \d+ damage to each",
    re.IGNORECASE,
)
_FREE_CAST = re.compile(
    r"without paying (?:its|their) mana cost|if you control a commander",
    re.IGNORECASE,
)
_BOUNCE_EFFECT = re.compile(
    r"return target.*to.*(?:owner's|its owner's) hand|put target.*on top",
    re.IGNORECASE,
)
_OPPONENT_DRAWBACK = re.compile(
    r"(?:its |that |their )(?:controller|owner) (?:draws|searches|gains)",
    re.IGNORECASE,
)


def _classify_removal_type(oracle_stripped: str) -> str:
    """Classify removal as single_target / board_wipe / counterspell."""
    if _BOARD_WIPE.search(oracle_stripped):
        return "board_wipe"
    if _COUNTER_SPELL.search(oracle_stripped):
        return "counterspell"
    return "single_target"


_TARGET_TYPE_RE = re.compile(
    r"target\s+(?:\w+\s+)*(creature|artifact|enchantment|planeswalker|permanent|nonland)",
    re.IGNORECASE,
)


def _classify_target_type(oracle_stripped: str) -> str:
    """Classify what type of permanent the removal targets."""
    lower = oracle_stripped.lower()
    if "target permanent" in lower or "target nonland" in lower:
        return "any"
    if _COUNTER_SPELL.search(lower):
        return "any"
    m = _TARGET_TYPE_RE.search(lower)
    if m:
        matched = m.group(1).lower()
        if matched in ("permanent", "nonland"):
            return "any"
        return matched
    return "any"


def _flexibility_score(oracle: str) -> float:
    """Score how many permanent types the removal can hit (1.0-3.0).

    "Any permanent" or "nonland permanent" scores highest.
    Hitting multiple named types scores in between.
    Single-type scores lowest.
    """
    oracle_lower = oracle.lower()

    if "target permanent" in oracle_lower or "target nonland" in oracle_lower:
        return 3.0
    if _COUNTER_SPELL.search(oracle_lower):
        return 2.5

    types_hit = 0
    for ptype in ["creature", "artifact", "enchantment", "planeswalker"]:
        if ptype in oracle_lower:
            types_hit += 1

    if types_hit >= 3:
        return 2.5
    if types_hit == 2:
        return 2.0
    if types_hit == 1:
        return 1.0
    if "target" in oracle_lower and ("destroy" in oracle_lower or "exile" in oracle_lower):
        return 2.0
    return 1.0


def _permanence_score(oracle: str) -> float:
    """Score removal permanence: exile > destroy > bounce (0.0-1.0)."""
    if _EXILE_EFFECT.search(oracle):
        return 1.0
    if _BOUNCE_EFFECT.search(oracle):
        return 0.2
    return 0.5


def _mana_efficiency_score(cmc: float) -> float:
    """Score mana efficiency (0.0-2.0). Cheaper removal is premium."""
    if cmc <= 1:
        return 2.0
    if cmc <= 2:
        return 1.5
    if cmc <= 3:
        return 1.0
    if cmc <= 4:
        return 0.5
    return 0.0


def _score_removal_card(
    oracle_stripped: str,
    type_line: str,
    cmc: float,
) -> float:
    """Compute a removal quality score.

    Combines flexibility, speed, permanence, mana efficiency, and bonuses.
    Score is normalized to roughly 0-1 range.

    Args:
        oracle_stripped: Oracle text with reminder text stripped.
        type_line: Card type line.
        cmc: Converted mana cost.

    Returns:
        Score in roughly 0-1 range.
    """
    role_score = 0.0

    # Flexibility
    role_score += _flexibility_score(oracle_stripped)

    # Speed (instant > sorcery)
    type_lower = type_line.lower()
    if "instant" in type_lower or "flash" in oracle_stripped.lower():
        role_score += 1.5
    elif "sorcery" in type_lower:
        role_score += 0.0
    else:
        role_score += 0.5

    # Permanence
    role_score += _permanence_score(oracle_stripped)

    # Mana efficiency
    role_score += _mana_efficiency_score(cmc)

    # Free-cast bonus
    if _FREE_CAST.search(oracle_stripped):
        role_score += 2.0

    # Drawback penalty
    if _OPPONENT_DRAWBACK.search(oracle_stripped):
        role_score -= 0.5

    # Normalize to 0-1 (max theoretical ~9.5)
    return min(role_score / 9.5, 1.0)


def detect_removal_card(card: dict) -> dict | None:
    """Detect whether a card is a removal card and return its metadata.

    Strips parenthetical reminder text before pattern matching to avoid
    false positives from keyword reminder text.

    Args:
        card: Card dict with oracle_text, type_line, cmc keys.

    Returns:
        Dict with removal metadata if card is removal, None otherwise.
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

    removal_type = _classify_removal_type(oracle_stripped)
    target_type = _classify_target_type(oracle_stripped)
    is_exile = bool(_EXILE_EFFECT.search(oracle_stripped))
    type_lower = type_line.lower()
    is_instant = "instant" in type_lower or "flash" in oracle_stripped.lower()
    is_free_cast = bool(_FREE_CAST.search(oracle_stripped))

    removal_score = _score_removal_card(oracle_stripped, type_line, cmc)

    return {
        "removal_type": removal_type,
        "target_type": target_type,
        "is_exile": is_exile,
        "is_instant": is_instant,
        "is_free_cast": is_free_cast,
        "flexibility_score": round(_flexibility_score(oracle_stripped), 4),
        "removal_score": round(removal_score, 4),
    }


def _ensure_removal_table(conn: sqlite3.Connection) -> None:
    """Create removal_candidates table if it doesn't exist."""
    conn.execute("""
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
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_removal_candidates_score "
        "ON removal_candidates(removal_score DESC)"
    )
    conn.commit()


def populate_removal_candidates(db_path: Path) -> dict:
    """Scan all Commander-legal cards and populate removal_candidates table.

    Args:
        db_path: Path to SQLite database.

    Returns:
        Dict with population statistics.
    """
    start = time.time()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        _ensure_removal_table(conn)

        # Check if already populated at current version
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM removal_candidates WHERE detection_version = ?",
                (DETECTION_VERSION,),
            ).fetchone()
            if row and row[0] > 0:
                logger.info(
                    "removal_candidates already populated at version %s (%d rows)",
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
        logger.info("Scanning %d Commander-legal cards for removal detection", len(cards))

        # Clear previous version data
        conn.execute(
            "DELETE FROM removal_candidates WHERE detection_version != ?",
            (DETECTION_VERSION,),
        )

        inserts: list[tuple] = []
        for card in cards:
            result = detect_removal_card(card)
            if result is not None:
                inserts.append((
                    card["id"],
                    result["removal_type"],
                    result["target_type"],
                    result["is_exile"],
                    result["is_instant"],
                    result["is_free_cast"],
                    result["flexibility_score"],
                    result["removal_score"],
                    DETECTION_VERSION,
                ))

        # Batch insert
        conn.executemany(
            "INSERT OR REPLACE INTO removal_candidates "
            "(card_id, removal_type, target_type, is_exile, is_instant, "
            "is_free_cast, flexibility_score, removal_score, "
            "detection_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            inserts,
        )
        conn.commit()

        duration = time.time() - start
        logger.info(
            "Populated removal_candidates: %d cards in %.1fs",
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
