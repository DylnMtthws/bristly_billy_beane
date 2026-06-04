"""Protection card detection and protection_candidates table population.

Strips parenthetical reminder text before pattern matching to eliminate
false positives from keyword reminder text. Populates a pre-scored
protection_candidates SQLite table for the protection generator to query.

The detect/populate plumbing lives in
:mod:`sabermetrics.analytics.detectors.base`; this module supplies only the
protection-specific patterns, scoring, and table layout.
"""

import re
from pathlib import Path

from sabermetrics.analytics.detectors.base import (
    Detector,
    populate_candidates,
    run_detect,
    strip_reminder_text,
)

# Re-exported for backward compatibility with existing imports/tests.
_strip_reminder_text = strip_reminder_text

# Detection version — bump when patterns change to force re-computation
DETECTION_VERSION = "1.0.0"


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


def _extract_protection(card: dict, oracle_stripped: str) -> dict:
    """Build the protection metadata dict for a qualifying card."""
    type_line = card.get("type_line") or ""
    cmc = float(card.get("cmc", 0) or 0)

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


PROTECTION_DETECTOR = Detector(
    name="protection",
    table="protection_candidates",
    detection_version=DETECTION_VERSION,
    positive_patterns=_POSITIVE_PATTERNS,
    negative_patterns=_NEGATIVE_PATTERNS,
    extract=_extract_protection,
    columns=[
        "protection_type",
        "is_board_wide",
        "is_instant",
        "is_free_cast",
        "coverage_score",
        "protection_score",
    ],
    create_table_sql="""
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
    index_sql=(
        "CREATE INDEX IF NOT EXISTS idx_protection_candidates_score "
        "ON protection_candidates(protection_score DESC)"
    ),
)


def detect_protection_card(card: dict) -> dict | None:
    """Detect whether a card is a protection card and return its metadata.

    Args:
        card: Card dict with oracle_text, type_line, cmc keys.

    Returns:
        Dict with protection metadata if card is protection, None otherwise.
    """
    return run_detect(PROTECTION_DETECTOR, card)


def populate_protection_candidates(db_path: Path) -> dict:
    """Scan all Commander-legal cards and populate protection_candidates table.

    Args:
        db_path: Path to SQLite database.

    Returns:
        Dict with population statistics.
    """
    return populate_candidates(PROTECTION_DETECTOR, db_path)
