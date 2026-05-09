"""Ramp card detection and ramp_candidates table population.

Strips parenthetical reminder text before pattern matching to eliminate
false positives from Treasure token reminder text. Populates a pre-scored
ramp_candidates SQLite table for the ramp generator to query.
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
    ("mana_production", re.compile(r"\badd\s+\{[WUBRGC]", re.IGNORECASE)),
    ("any_color", re.compile(r"\badd\s+(?:one\s+)?mana\s+of\s+any", re.IGNORECASE)),
    ("land_search", re.compile(
        r"search your library for.*land.*put.*(?:onto |on )?the battlefield",
        re.IGNORECASE,
    )),
    ("land_to_play", re.compile(
        r"put.*land.*(?:from|onto|on).*the battlefield",
        re.IGNORECASE,
    )),
    ("treasure_gen", re.compile(r"\bcreate.*treasure", re.IGNORECASE)),
    ("generic_mana", re.compile(r"\badd\s+\{\d+\}", re.IGNORECASE)),
]

# --- Negative patterns (disqualify a card from ramp) ---

_NEGATIVE_PATTERNS: list[re.Pattern] = [
    re.compile(r"spend this mana only", re.IGNORECASE),
    re.compile(r"(?:each )?opponents?\s+creates?\s+.*treasure", re.IGNORECASE),
]

# --- Mana estimation patterns ---

_ADD_CLAUSE = re.compile(r"add\b(.*?)(?:\.|$)", re.IGNORECASE)
_MANA_SYMBOL = re.compile(r"\{([WUBRGC])\}", re.IGNORECASE)
_ADD_ANY_COLOR = re.compile(r"add (?:one mana of any|mana of any)", re.IGNORECASE)
_ADD_GENERIC = re.compile(r"add \{(\d+)\}", re.IGNORECASE)


def _estimate_mana_output(oracle_stripped: str) -> tuple[float, bool]:
    """Estimate mana output per activation from stripped oracle text.

    Returns:
        Tuple of (mana_per_activation, produces_colored).
    """
    lower = oracle_stripped.lower()
    produces_colored = False
    total_mana = 0.0

    if _ADD_ANY_COLOR.search(lower):
        produces_colored = True
        total_mana = max(total_mana, 1.0)

    for match in _ADD_CLAUSE.finditer(lower):
        clause = match.group(1)
        symbols = _MANA_SYMBOL.findall(clause)
        if symbols:
            colored = [s for s in symbols if s.upper() != "C"]
            if colored:
                produces_colored = True
            total_mana = max(total_mana, len(symbols))
        generic = _ADD_GENERIC.findall(clause)
        for g in generic:
            total_mana = max(total_mana, float(g))

    if total_mana == 0 and "add" in lower:
        symbols = _MANA_SYMBOL.findall(lower)
        if symbols:
            colored = [s for s in symbols if s.upper() != "C"]
            if colored:
                produces_colored = True
            total_mana = max(1.0, len(symbols))

    if "search your library" in lower and "land" in lower:
        produces_colored = True
        total_mana = max(total_mana, 1.0)

    return (total_mana or 1.0, produces_colored)


def _classify_ramp_type(type_line: str, oracle_stripped: str) -> str:
    """Classify ramp into rock/land_ramp/dork/enchantment/ritual/treasure_gen/other."""
    tl = type_line.lower()
    ol = oracle_stripped.lower()

    if "creature" in tl and ("add" in ol or "mana" in ol):
        return "dork"
    if "search your library" in ol and "land" in ol:
        return "land_ramp"
    if "put" in ol and "land" in ol and "battlefield" in ol:
        return "land_ramp"
    if re.search(r"\bcreate.*treasure", ol, re.IGNORECASE):
        return "treasure_gen"
    if "artifact" in tl:
        return "rock"
    if "enchantment" in tl:
        return "enchantment"
    if "sorcery" in tl or "instant" in tl:
        return "ritual"
    return "other"


def _resilience_tier(ramp_type: str) -> int:
    """Assign resilience tier (higher = more resilient).

    Land ramp survives board wipes, artifacts/enchantments don't,
    creatures are most fragile.
    """
    return {
        "land_ramp": 4,
        "enchantment": 3,
        "rock": 2,
        "treasure_gen": 2,
        "ritual": 1,
        "dork": 1,
        "other": 2,
    }.get(ramp_type, 2)


def _score_ramp_card(
    cmc: float,
    mana_output: float,
    produces_colored: bool,
    is_conditional: bool,
    ramp_type: str,
) -> float:
    """Compute a ramp quality score.

    Signals:
    - Net mana rate: mana_output / max(cmc, 0.5)
    - Conditionality penalty
    - Color production bonus
    - Resilience tier bonus

    Returns:
        Score in roughly 0-1 range.
    """
    effective_cmc = max(cmc, 0.5)
    net_rate = mana_output / effective_cmc
    score = min(net_rate * 1.5, 3.0)

    if is_conditional:
        score *= 0.4

    if produces_colored:
        score += 0.3

    tier = _resilience_tier(ramp_type)
    score += (tier - 2) * 0.1  # land_ramp +0.2, dork -0.1, rock 0.0

    # Normalize to roughly 0-1
    return min(score / 3.0, 1.0)


_CONDITIONAL_MANA = re.compile(
    r"(if you control|when you (?:cast|discard)|sacrifice a |discard a )",
    re.IGNORECASE,
)


def detect_ramp_card(card: dict) -> dict | None:
    """Detect whether a card is a ramp card and return its ramp metadata.

    Strips parenthetical reminder text before pattern matching to avoid
    false positives from Treasure token reminder text.

    Args:
        card: Card dict with oracle_text, type_line, cmc keys.

    Returns:
        Dict with ramp metadata if card is ramp, None otherwise.
    """
    oracle = card.get("oracle_text") or ""
    type_line = card.get("type_line") or ""
    cmc = float(card.get("cmc", 0) or 0)

    oracle_stripped = _strip_reminder_text(oracle)

    # Check negative patterns first (on original text to catch reminder-embedded restrictions)
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

    ramp_type = _classify_ramp_type(type_line, oracle_stripped)
    mana_output, produces_colored = _estimate_mana_output(oracle_stripped)
    is_conditional = bool(_CONDITIONAL_MANA.search(oracle_stripped))
    net_mana_rate = mana_output / max(cmc, 0.5)

    ramp_score = _score_ramp_card(
        cmc=cmc,
        mana_output=mana_output,
        produces_colored=produces_colored,
        is_conditional=is_conditional,
        ramp_type=ramp_type,
    )

    return {
        "ramp_type": ramp_type,
        "net_mana_rate": round(net_mana_rate, 3),
        "mana_output": round(mana_output, 1),
        "produces_colored": produces_colored,
        "is_conditional": is_conditional,
        "is_restricted": False,  # Restricted cards are excluded by negative patterns
        "resilience_tier": _resilience_tier(ramp_type),
        "ramp_score": round(ramp_score, 4),
    }


def _ensure_ramp_table(conn: sqlite3.Connection) -> None:
    """Create ramp_candidates table if it doesn't exist."""
    conn.execute("""
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
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ramp_candidates_score "
        "ON ramp_candidates(ramp_score DESC)"
    )
    conn.commit()


def populate_ramp_candidates(db_path: Path) -> dict:
    """Scan all Commander-legal cards and populate ramp_candidates table.

    Args:
        db_path: Path to SQLite database.

    Returns:
        Dict with population statistics.
    """
    start = time.time()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        _ensure_ramp_table(conn)

        # Check if already populated at current version
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM ramp_candidates WHERE detection_version = ?",
                (DETECTION_VERSION,),
            ).fetchone()
            if row and row[0] > 0:
                logger.info(
                    "ramp_candidates already populated at version %s (%d rows)",
                    DETECTION_VERSION, row[0],
                )
                return {
                    "rows": row[0],
                    "skipped": True,
                    "version": DETECTION_VERSION,
                    "duration_seconds": 0.0,
                }
        except sqlite3.OperationalError:
            pass  # Table doesn't exist yet

        # Fetch all Commander-legal cards
        cursor = conn.execute(
            "SELECT id, name, oracle_text, type_line, cmc "
            "FROM cards "
            "WHERE is_legal_in_99 = 1"
        )
        cards = [dict(row) for row in cursor.fetchall()]
        logger.info("Scanning %d Commander-legal cards for ramp detection", len(cards))

        # Clear previous version data
        conn.execute(
            "DELETE FROM ramp_candidates WHERE detection_version != ?",
            (DETECTION_VERSION,),
        )

        inserts: list[tuple] = []
        for card in cards:
            result = detect_ramp_card(card)
            if result is not None:
                inserts.append((
                    card["id"],
                    result["ramp_type"],
                    result["net_mana_rate"],
                    result["mana_output"],
                    result["produces_colored"],
                    result["is_conditional"],
                    result["is_restricted"],
                    result["resilience_tier"],
                    result["ramp_score"],
                    DETECTION_VERSION,
                ))

        # Batch insert
        conn.executemany(
            "INSERT OR REPLACE INTO ramp_candidates "
            "(card_id, ramp_type, net_mana_rate, mana_output, produces_colored, "
            "is_conditional, is_restricted, resilience_tier, ramp_score, "
            "detection_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            inserts,
        )
        conn.commit()

        duration = time.time() - start
        logger.info(
            "Populated ramp_candidates: %d cards in %.1fs",
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
