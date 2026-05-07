"""Card role tagging via pattern matching on oracle text and type lines.

Tags every card with functional roles (ramp, draw, removal, etc.) and
functional categories (sacrifice_outlet, etb_payoff, aura, etc.) so that
Stage 2 (Pareto filter) and infrastructure generators can select cards
without LLM calls.
"""

import json
import logging
import re
import sqlite3
import time
from pathlib import Path

import yaml

from sabermetrics.models.tags import RoleTagResult, TaggingStats

logger = logging.getLogger(__name__)

ROLE_TAGS = [
    "ramp", "fixing", "draw", "removal", "board_wipe", "tutor",
    "recursion", "protection", "threat", "wincon", "utility", "land",
]

# --- Role detection patterns (oracle text) ---

_ROLE_PATTERNS: dict[str, list[re.Pattern]] = {
    "ramp": [
        re.compile(r"add\s+\{[WUBRGC]", re.IGNORECASE),
        re.compile(r"add\s+one mana of any", re.IGNORECASE),
        re.compile(r"search your library for.*(?:basic )?land.*put.*(?:onto|on) the battlefield", re.IGNORECASE),
        re.compile(r"put.*land.*(?:from|onto|on).*the battlefield", re.IGNORECASE),
        re.compile(r"create.*treasure", re.IGNORECASE),
    ],
    "fixing": [
        re.compile(r"add one mana of any color", re.IGNORECASE),
        re.compile(r"add\s+\{[WUBRG]\}\s*(?:or|,)\s*\{[WUBRG]\}", re.IGNORECASE),
    ],
    "draw": [
        re.compile(r"draw\s+(?:a\s+)?card", re.IGNORECASE),
        re.compile(r"draws?\s+\d+\s+card", re.IGNORECASE),
        re.compile(r"look at the top.*(?:put|draw)", re.IGNORECASE),
        re.compile(r"reveal.*(?:put.*hand|draw)", re.IGNORECASE),
    ],
    "removal": [
        re.compile(r"destroy target", re.IGNORECASE),
        re.compile(r"exile target", re.IGNORECASE),
        re.compile(r"deals?\s+\d+\s+damage\s+to\s+(?:target|any|each)", re.IGNORECASE),
        re.compile(r"target.*gets?\s+\-\d+/\-\d+", re.IGNORECASE),
        re.compile(r"counter target spell", re.IGNORECASE),
        re.compile(r"return target.*to.*(?:owner|hand)", re.IGNORECASE),
    ],
    "board_wipe": [
        re.compile(r"destroy all", re.IGNORECASE),
        re.compile(r"exile all", re.IGNORECASE),
        re.compile(r"(?:each|all) (?:creature|permanent|nonland).*gets?\s+\-\d+/\-\d+", re.IGNORECASE),
        re.compile(r"return all.*to.*(?:owner|hand)", re.IGNORECASE),
    ],
    "tutor": [
        re.compile(r"search your library for a card", re.IGNORECASE),
        re.compile(r"search your library for an? (?:creature|instant|sorcery|artifact|enchantment)", re.IGNORECASE),
    ],
    "recursion": [
        re.compile(r"return.*from.*graveyard.*(?:hand|battlefield|to the battlefield)", re.IGNORECASE),
        re.compile(r"return.*(?:card|creature|permanent).*to the battlefield.*(?:under|from)", re.IGNORECASE),
        re.compile(r"put.*from.*graveyard.*(?:onto|into).*(?:battlefield|hand)", re.IGNORECASE),
        re.compile(r"cast.*from.*graveyard", re.IGNORECASE),
    ],
    "protection": [
        re.compile(r"hexproof", re.IGNORECASE),
        re.compile(r"indestructible", re.IGNORECASE),
        re.compile(r"phase out", re.IGNORECASE),
        re.compile(r"can't be (?:the target|countered|destroyed)", re.IGNORECASE),
        re.compile(r"protection from", re.IGNORECASE),
    ],
    "wincon": [
        re.compile(r"you win the game", re.IGNORECASE),
        re.compile(r"extra turn", re.IGNORECASE),
        re.compile(r"each opponent loses", re.IGNORECASE),
        re.compile(r"infinite", re.IGNORECASE),
        re.compile(r"damage to each opponent", re.IGNORECASE),
    ],
    "threat": [
        re.compile(r"deals? combat damage to.*player", re.IGNORECASE),
        re.compile(r"commander damage", re.IGNORECASE),
    ],
}


def _load_functional_categories() -> dict[str, dict]:
    """Load functional category definitions from config YAML."""
    config_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "functional_categories.yaml"
    if not config_path.exists():
        logger.warning("functional_categories.yaml not found at %s", config_path)
        return {}
    with open(config_path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("categories", {})


def _load_overrides() -> dict[str, dict]:
    """Load manual role tag overrides from config YAML."""
    config_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "role_tag_overrides.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("overrides", {})


# Compile functional category patterns lazily
_COMPILED_CATEGORIES: dict[str, dict[str, list[re.Pattern]]] | None = None


def _get_compiled_categories() -> dict[str, dict[str, list[re.Pattern]]]:
    """Get compiled regex patterns for functional categories."""
    global _COMPILED_CATEGORIES
    if _COMPILED_CATEGORIES is not None:
        return _COMPILED_CATEGORIES

    raw = _load_functional_categories()
    _COMPILED_CATEGORIES = {}
    for cat_name, cat_def in raw.items():
        compiled: dict[str, list[re.Pattern]] = {
            "oracle_patterns": [],
            "type_patterns": [],
        }
        for pat_str in cat_def.get("oracle_patterns", []):
            try:
                compiled["oracle_patterns"].append(re.compile(pat_str, re.IGNORECASE))
            except re.error:
                logger.warning("Bad regex in category %s: %s", cat_name, pat_str)
        for pat_str in cat_def.get("type_patterns", []):
            try:
                compiled["type_patterns"].append(re.compile(pat_str, re.IGNORECASE))
            except re.error:
                logger.warning("Bad regex in category %s type: %s", cat_name, pat_str)
        _COMPILED_CATEGORIES[cat_name] = compiled
    return _COMPILED_CATEGORIES


def tag_card_roles(card: dict) -> RoleTagResult:
    """Pattern-match oracle text + type line to assign role tags and functional categories.

    Args:
        card: Card dict with at least 'oracle_text', 'type_line', and 'name' keys.

    Returns:
        RoleTagResult with matched role_tags and functional_categories.
    """
    oracle_text = (card.get("oracle_text") or "").strip()
    type_line = (card.get("type_line") or "").strip()
    card_name = (card.get("name") or "").strip()
    type_lower = type_line.lower()

    # Check overrides first
    overrides = _load_overrides()
    if card_name in overrides:
        override = overrides[card_name]
        return RoleTagResult(
            role_tags=override.get("role_tags", []),
            functional_categories=override.get("functional_categories", []),
        )

    # --- Role tags ---
    roles: list[str] = []

    # Type-line based roles
    if "land" in type_lower and "creature" not in type_lower:
        roles.append("land")
    else:
        # Oracle text pattern matching
        for role, patterns in _ROLE_PATTERNS.items():
            for pat in patterns:
                if pat.search(oracle_text):
                    roles.append(role)
                    break

    # If no specific role matched for non-land, default to utility
    if not roles:
        roles.append("utility")

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped_roles: list[str] = []
    for r in roles:
        if r not in seen:
            seen.add(r)
            deduped_roles.append(r)

    # --- Functional categories ---
    categories: list[str] = []
    compiled = _get_compiled_categories()

    for cat_name, cat_patterns in compiled.items():
        matched = False
        for pat in cat_patterns.get("oracle_patterns", []):
            if pat.search(oracle_text):
                matched = True
                break
        if not matched:
            for pat in cat_patterns.get("type_patterns", []):
                if pat.search(type_line):
                    matched = True
                    break
        if matched:
            categories.append(cat_name)

    return RoleTagResult(role_tags=deduped_roles, functional_categories=categories)


def _ensure_tag_columns(conn: sqlite3.Connection) -> None:
    """Add role_tags columns to cards table if they don't exist."""
    cursor = conn.execute("PRAGMA table_info(cards)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    migrations = [
        ("role_tags", "TEXT"),
        ("functional_categories", "TEXT"),
        ("tags_extracted_at", "TIMESTAMP"),
        ("tags_extraction_version", "TEXT"),
    ]
    for col_name, col_type in migrations:
        if col_name not in existing_columns:
            conn.execute(f"ALTER TABLE cards ADD COLUMN {col_name} {col_type}")
            logger.info("Added column %s to cards table", col_name)
    conn.commit()


def tag_all_cards(db_path: Path, version: str) -> TaggingStats:
    """Batch-tag all cards in the database with role tags and functional categories.

    Skips cards that are already tagged at the current version.

    Args:
        db_path: Path to SQLite database.
        version: Version string for invalidation (e.g. "1.0.0").

    Returns:
        TaggingStats with counts and distribution.
    """
    start = time.time()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        _ensure_tag_columns(conn)

        # Get cards that need tagging (version mismatch or never tagged)
        cursor = conn.execute(
            "SELECT id, name, oracle_text, type_line "
            "FROM cards "
            "WHERE tags_extraction_version IS NULL "
            "OR tags_extraction_version != ?",
            (version,),
        )
        cards = [dict(row) for row in cursor.fetchall()]
        total = len(cards)

        if total == 0:
            logger.info("All cards already tagged at version %s", version)
            return TaggingStats(
                total_cards=0,
                tagged_cards=0,
                skipped_cards=0,
                version=version,
                duration_seconds=0.0,
            )

        logger.info("Tagging %d cards at version %s", total, version)

        role_dist: dict[str, int] = {}
        cat_dist: dict[str, int] = {}
        tagged = 0
        batch_size = 500

        for i in range(0, total, batch_size):
            batch = cards[i:i + batch_size]
            updates: list[tuple] = []

            for card in batch:
                result = tag_card_roles(card)
                for r in result.role_tags:
                    role_dist[r] = role_dist.get(r, 0) + 1
                for c in result.functional_categories:
                    cat_dist[c] = cat_dist.get(c, 0) + 1

                updates.append((
                    json.dumps(result.role_tags),
                    json.dumps(result.functional_categories),
                    version,
                    card["id"],
                ))
                tagged += 1

            conn.executemany(
                "UPDATE cards SET "
                "role_tags = ?, functional_categories = ?, "
                "tags_extracted_at = CURRENT_TIMESTAMP, "
                "tags_extraction_version = ? "
                "WHERE id = ?",
                updates,
            )
            conn.commit()

            if (i + batch_size) % 5000 < batch_size:
                logger.info("Tagged %d/%d cards", min(i + batch_size, total), total)

        duration = time.time() - start
        logger.info(
            "Tagging complete: %d cards in %.1fs", tagged, duration,
        )

        return TaggingStats(
            total_cards=total,
            tagged_cards=tagged,
            skipped_cards=0,
            version=version,
            duration_seconds=round(duration, 2),
            role_distribution=role_dist,
            category_distribution=cat_dist,
        )
    finally:
        conn.close()
