"""Component scorer for deck analysis (D4.6).

Counts functional components in a deck: ramp, draw, removal,
board wipes, tutors, and mana base quality.
"""

import json
import logging
import re
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Pattern lists for component detection
RAMP_PATTERNS = [
    r"add\s+\{?\w\}?",  # adds mana
    r"add\s+\w+ mana",
    r"search your library for a .* land",
    r"put .* land .* onto the battlefield",
    r"mana\s+dork",
]

DRAW_PATTERNS = [
    r"draw\s+\w+\s+cards?",
    r"draw\s+a\s+card",
    r"look at the top .* cards? of your library",
    r"scry\s+\d",
    r"whenever .* draw",
]

REMOVAL_PATTERNS = [
    r"destroy target",
    r"exile target",
    r"target .* gets? -\d+/-\d+",
    r"deals? \d+ damage to .* target",
    r"counter target spell",
    r"return target .* to .* owner's hand",
]

BOARD_WIPE_PATTERNS = [
    r"destroy all",
    r"exile all",
    r"all creatures get -\d+/-\d+",
    r"each (?:player|opponent) .* sacrifice",
    r"deals? \d+ damage to each",
]

TUTOR_PATTERNS = [
    r"search your library for a card",
    r"search your library for .* card",
    r"search your library for a .* card .* put .* hand",
    r"search your library for a .* card .* put .* battlefield",
]

# Compiled patterns for performance
_RAMP_RE = [re.compile(p, re.IGNORECASE) for p in RAMP_PATTERNS]
_DRAW_RE = [re.compile(p, re.IGNORECASE) for p in DRAW_PATTERNS]
_REMOVAL_RE = [re.compile(p, re.IGNORECASE) for p in REMOVAL_PATTERNS]
_WIPE_RE = [re.compile(p, re.IGNORECASE) for p in BOARD_WIPE_PATTERNS]
_TUTOR_RE = [re.compile(p, re.IGNORECASE) for p in TUTOR_PATTERNS]


class ManaBaseScore(BaseModel):
    """Analysis of a deck's mana base."""

    total_lands: int
    color_sources: dict[str, int]  # {color: count}
    utility_lands: int
    average_land_cmc: float  # Enters-tapped penalty
    mana_rocks: int
    mana_dorks: int
    total_ramp: int
    score: float  # 0.0 to 1.0


def _matches_any(text: str, patterns: list[re.Pattern]) -> bool:
    """Check if text matches any of the compiled patterns."""
    return any(p.search(text) for p in patterns)


def _is_land(card: dict) -> bool:
    """Check if a card is a land."""
    type_line = (card.get("type_line") or "").lower()
    return "land" in type_line


def _is_ramp(card: dict) -> bool:
    """Check if a card provides mana ramp."""
    oracle = (card.get("oracle_text") or "").lower()
    type_line = (card.get("type_line") or "").lower()
    keywords = card.get("keywords", [])
    if isinstance(keywords, str):
        keywords = json.loads(keywords)

    # Sol Ring, mana rocks, etc.
    if "artifact" in type_line and _matches_any(oracle, _RAMP_RE):
        return True
    # Mana dorks
    if "creature" in type_line and _matches_any(oracle, _RAMP_RE):
        return True
    # Land ramp spells
    if _matches_any(oracle, _RAMP_RE):
        return True
    return False


def count_ramp_spells(cards: list[dict]) -> int:
    """Count cards that provide mana acceleration."""
    return sum(1 for c in cards if not _is_land(c) and _is_ramp(c))


def count_card_draw(cards: list[dict]) -> int:
    """Count cards that draw cards or provide card selection."""
    count = 0
    for card in cards:
        if _is_land(card):
            continue
        oracle = (card.get("oracle_text") or "").lower()
        if _matches_any(oracle, _DRAW_RE):
            count += 1
    return count


def count_removal(cards: list[dict]) -> int:
    """Count targeted removal spells."""
    count = 0
    for card in cards:
        if _is_land(card):
            continue
        oracle = (card.get("oracle_text") or "").lower()
        if _matches_any(oracle, _REMOVAL_RE):
            count += 1
    return count


def count_board_wipes(cards: list[dict]) -> int:
    """Count board wipe effects."""
    count = 0
    for card in cards:
        oracle = (card.get("oracle_text") or "").lower()
        if _matches_any(oracle, _WIPE_RE):
            count += 1
    return count


def count_tutors(cards: list[dict]) -> int:
    """Count tutor effects (library search)."""
    count = 0
    for card in cards:
        if _is_land(card):
            continue
        oracle = (card.get("oracle_text") or "").lower()
        # Exclude land search (that's ramp, not tutoring)
        if _matches_any(oracle, _TUTOR_RE):
            # Don't double-count basic land search as tutoring
            if not re.search(r"search your library for a (?:basic )?land", oracle, re.IGNORECASE):
                count += 1
    return count


def analyze_mana_base(cards: list[dict], commander_colors: list[str]) -> ManaBaseScore:
    """Analyze the quality of a deck's mana base.

    Args:
        cards: All cards in the deck (including lands).
        commander_colors: Commander's color identity.

    Returns:
        ManaBaseScore with detailed breakdown and quality score.
    """
    lands = [c for c in cards if _is_land(c)]
    non_lands = [c for c in cards if not _is_land(c)]

    total_lands = len(lands)

    # Count color sources from lands
    color_sources: dict[str, int] = {c: 0 for c in commander_colors}
    utility_lands = 0
    enters_tapped = 0

    for land in lands:
        oracle = (land.get("oracle_text") or "").lower()
        type_line = (land.get("type_line") or "").lower()
        name = (land.get("name") or "").lower()

        # Basic land type detection
        land_color_map = {
            "plains": "W", "island": "U", "swamp": "B",
            "mountain": "R", "forest": "G",
        }
        found_color = False
        for land_type, color in land_color_map.items():
            if land_type in type_line or land_type in name:
                if color in color_sources:
                    color_sources[color] += 1
                    found_color = True

        # Check oracle text for mana production
        if not found_color:
            mana_pattern = r"add\s+\{([WUBRG])\}"
            for match in re.finditer(mana_pattern, oracle, re.IGNORECASE):
                color = match.group(1).upper()
                if color in color_sources:
                    color_sources[color] += 1
                    found_color = True

        # Any-color lands
        if "any color" in oracle or "any one color" in oracle:
            for color in commander_colors:
                if color in color_sources:
                    color_sources[color] += 1
            found_color = True

        if not found_color:
            utility_lands += 1

        # Enters-tapped penalty
        if "enters the battlefield tapped" in oracle:
            enters_tapped += 1

    # Count mana rocks and dorks
    mana_rocks = sum(
        1 for c in non_lands
        if "artifact" in (c.get("type_line") or "").lower() and _is_ramp(c)
    )
    mana_dorks = sum(
        1 for c in non_lands
        if "creature" in (c.get("type_line") or "").lower() and _is_ramp(c)
    )
    total_ramp = count_ramp_spells(non_lands)

    # Compute quality score
    score = 0.0

    # Land count (ideal: 35-38 for 100-card deck)
    if 35 <= total_lands <= 38:
        score += 0.3
    elif 33 <= total_lands <= 40:
        score += 0.2
    elif total_lands >= 30:
        score += 0.1

    # Ramp count (ideal: 10-12)
    if 10 <= total_ramp <= 15:
        score += 0.2
    elif 7 <= total_ramp <= 17:
        score += 0.1

    # Color coverage
    if commander_colors:
        covered = sum(1 for c in commander_colors if color_sources.get(c, 0) >= 5)
        coverage_ratio = covered / len(commander_colors)
        score += 0.3 * coverage_ratio

    # Penalty for too many enters-tapped
    if total_lands > 0:
        tapped_ratio = enters_tapped / total_lands
        if tapped_ratio <= 0.2:
            score += 0.2
        elif tapped_ratio <= 0.35:
            score += 0.1

    avg_land_cmc = enters_tapped * 0.5 / max(total_lands, 1)

    return ManaBaseScore(
        total_lands=total_lands,
        color_sources=color_sources,
        utility_lands=utility_lands,
        average_land_cmc=round(avg_land_cmc, 3),
        mana_rocks=mana_rocks,
        mana_dorks=mana_dorks,
        total_ramp=total_ramp,
        score=round(min(1.0, score), 3),
    )
