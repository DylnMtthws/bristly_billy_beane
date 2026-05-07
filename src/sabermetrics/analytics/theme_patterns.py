"""Theme detection engine for deck composition analysis.

Detects 15 mechanic themes from card oracle text using regex patterns,
following the established components.py pattern of module-level regex
constants with pre-compiled patterns.
"""

import re

# Theme patterns: each theme has 3-5 regex patterns matching oracle text
THEME_PATTERNS: dict[str, list[str]] = {
    "etb_triggers": [
        r"when .* enters the battlefield",
        r"enters the battlefield with",
        r"whenever .* enters",
        r"when .* enters under your control",
    ],
    "sacrifice": [
        r"sacrifice a",
        r"sacrifice another",
        r"when(?:ever)? .* dies",
        r"whenever .* is put into .* graveyard from the battlefield",
        r"as an additional cost .* sacrifice",
    ],
    "token_generation": [
        r"create .* token",
        r"put .* token .* onto the battlefield",
        r"creates? .* creature tokens?",
        r"for each .* create",
    ],
    "graveyard_recursion": [
        r"return .* from .* graveyard",
        r"put .* from .* graveyard .* onto the battlefield",
        r"cast .* from .* graveyard",
        r"exile .* from .* graveyard",
        r"whenever .* leaves .* graveyard",
    ],
    "enchantment_matters": [
        r"whenever you cast an enchantment",
        r"enchantments you control",
        r"enchanted creature",
        r"aura",
        r"constellation",
    ],
    "artifact_matters": [
        r"whenever you cast an artifact",
        r"artifacts you control",
        r"artifact enters the battlefield",
        r"for each artifact",
        r"affinity for artifacts",
    ],
    "counters_plus1": [
        r"put .* \+1/\+1 counter",
        r"\+1/\+1 counters?",
        r"enters .* with .* \+1/\+1",
        r"proliferate",
        r"whenever .* counter is placed",
    ],
    "lifegain": [
        r"you gain .* life",
        r"whenever you gain life",
        r"lifelink",
        r"pay .* life",
        r"life total",
    ],
    "combat_matters": [
        r"whenever .* attacks",
        r"whenever .* deals combat damage",
        r"additional combat",
        r"creatures you control get \+",
        r"exalted",
    ],
    "blink_flicker": [
        r"exile .* then return",
        r"exile .* return .* to the battlefield",
        r"flicker",
        r"whenever .* exile .* return",
    ],
    "mill": [
        r"mill",
        r"put .* cards? from .* library into .* graveyard",
        r"each opponent .* top .* cards? .* library",
    ],
    "tribal": [
        r"other .* you control get",
        r"creatures of the chosen type",
        r"choose a creature type",
        r"lord",
        r"whenever another .* enters the battlefield",
    ],
    "voltron": [
        r"equipped creature",
        r"equip \{",
        r"attach .* to .* creature",
        r"enchanted creature gets",
        r"whenever .* becomes the target",
    ],
    "protection_resilience": [
        r"hexproof",
        r"indestructible",
        r"protection from",
        r"shroud",
        r"ward",
    ],
    "counterspells": [
        r"counter target spell",
        r"counter target .* spell",
        r"counter it",
    ],
}

# Pre-compiled regexes (case-insensitive) for performance
COMPILED_THEME_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    theme: [re.compile(p, re.IGNORECASE) for p in patterns]
    for theme, patterns in THEME_PATTERNS.items()
}


def count_theme_cards(cards: list[dict], theme: str) -> int:
    """Count cards matching a theme's regex patterns.

    Skips lands. A card counts at most once per theme even if it
    matches multiple patterns.

    Args:
        cards: List of card dicts with oracle_text and type_line.
        theme: Theme key from THEME_PATTERNS.

    Returns:
        Number of cards matching the theme.
    """
    patterns = COMPILED_THEME_PATTERNS.get(theme, [])
    if not patterns:
        return 0

    count = 0
    for card in cards:
        type_line = (card.get("type_line") or "").lower()
        if "land" in type_line:
            continue
        oracle = card.get("oracle_text") or ""
        if any(p.search(oracle) for p in patterns):
            count += 1
    return count


def compute_deck_theme_vector(cards: list[dict]) -> dict[str, int]:
    """Compute a full theme vector for a deck.

    Args:
        cards: List of card dicts for the deck.

    Returns:
        Dict mapping each theme name to the count of matching cards.
    """
    return {theme: count_theme_cards(cards, theme) for theme in THEME_PATTERNS}


def classify_dominant_theme(
    vector: dict[str, int], min_threshold: int = 5
) -> str | None:
    """Identify the dominant theme from a theme vector.

    Args:
        vector: Theme vector from compute_deck_theme_vector.
        min_threshold: Minimum card count to qualify as dominant.

    Returns:
        Theme name with highest count above threshold, or None.
    """
    best_theme = None
    best_count = 0
    for theme, count in vector.items():
        if count >= min_threshold and count > best_count:
            best_theme = theme
            best_count = count
    return best_theme
