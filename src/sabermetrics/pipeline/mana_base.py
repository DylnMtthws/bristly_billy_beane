"""Karsten-style mana base calculator for Commander decks.

Uses Frank Karsten's hypergeometric probability framework adapted for
99-card Commander decks to determine per-color source requirements,
then greedily selects nonbasic lands and fills basics by pip demand.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from sabermetrics.pipeline.slot_assigner import SlotAssignment

logger = logging.getLogger(__name__)

# Basic land type → color mapping
BASIC_TYPE_COLORS: dict[str, str] = {
    "plains": "W",
    "island": "U",
    "swamp": "B",
    "mountain": "R",
    "forest": "G",
}

# Color → basic land name
COLOR_TO_BASIC: dict[str, str] = {
    "W": "Plains",
    "U": "Island",
    "B": "Swamp",
    "R": "Mountain",
    "G": "Forest",
}

# Mana symbol → color
MANA_SYMBOL_COLORS: dict[str, str] = {
    "W": "W",
    "U": "U",
    "B": "B",
    "R": "R",
    "G": "G",
}

# Sources needed for ~90% on-curve cast rate in 99-card / ~36-land decks.
# Key: (pips_of_color_in_cost, turn_to_cast) → sources required
# This is the in-memory default; overridden by load_karsten_config().
KARSTEN_SOURCES_99: dict[tuple[int, int], int] = {
    (1, 1): 22, (1, 2): 19, (1, 3): 17, (1, 4): 15, (1, 5): 13,
    (1, 6): 12, (1, 7): 11,
    (2, 2): 27, (2, 3): 23, (2, 4): 20, (2, 5): 18,
    (2, 6): 16, (2, 7): 15,
    (3, 3): 29, (3, 4): 26, (3, 5): 23,
    (3, 6): 21, (3, 7): 19,
}

# Cached Karsten config
_KARSTEN_CONFIG: dict | None = None


def load_karsten_config() -> dict:
    """Load Karsten mana base configuration from YAML.

    Returns:
        Dict with keys: color_source_requirements, land_count_targets,
        reference_land_count, minimum_sources_per_color.
    """
    global _KARSTEN_CONFIG
    if _KARSTEN_CONFIG is not None:
        return _KARSTEN_CONFIG

    config_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "karsten_mana_base.yaml"
    if not config_path.exists():
        logger.warning("karsten_mana_base.yaml not found, using built-in defaults")
        _KARSTEN_CONFIG = {
            "color_source_requirements": {},
            "land_count_targets": {},
            "reference_land_count": 36,
            "minimum_sources_per_color": 5,
        }
        return _KARSTEN_CONFIG

    with open(config_path) as f:
        data = yaml.safe_load(f) or {}

    # Rebuild KARSTEN_SOURCES_99 from YAML structure
    csr = data.get("color_source_requirements", {})
    for pips, turns in csr.items():
        pips_int = int(pips)
        for turn_key, sources in turns.items():
            turn_int = int(turn_key.replace("turn_", ""))
            KARSTEN_SOURCES_99[(pips_int, turn_int)] = sources

    _KARSTEN_CONFIG = data
    return _KARSTEN_CONFIG


def target_land_count(avg_cmc: float) -> int:
    """Determine target land count from average CMC using Karsten guidelines.

    Args:
        avg_cmc: Average converted mana cost of non-land cards.

    Returns:
        Recommended total land count (typically 33-41).
    """
    config = load_karsten_config()
    targets = config.get("land_count_targets", {})

    if not targets:
        # Fallback formula: 33 + (avg_cmc - 2.0) * 2.67
        return max(33, min(41, round(33 + (avg_cmc - 2.0) * 2.67)))

    # Find closest bracket in the YAML targets
    best_key = None
    best_dist = float("inf")
    for key_str in targets:
        key_val = float(key_str)
        dist = abs(key_val - avg_cmc)
        if dist < best_dist:
            best_dist = dist
            best_key = key_str

    if best_key is not None:
        return int(targets[best_key])

    return 36  # Safe default

# Regex patterns for oracle text parsing
_ADD_MANA_PATTERN = re.compile(
    r"\{[Tt]\}:\s*[Aa]dd\s+(\{[WUBRGC]\}(?:\s*(?:or|,)\s*\{[WUBRGC]\})*)"
)
_ADD_MANA_SIMPLE = re.compile(
    r"[Aa]dd\s+(\{[WUBRGC]\}(?:\s*(?:or|,)\s*\{[WUBRGC]\})*)"
)
_MANA_SYMBOLS = re.compile(r"\{([WUBRG])\}")
_ANY_COLOR = re.compile(r"(?:any color|any one color|one mana of any color)", re.IGNORECASE)
_ETB_TAPPED = re.compile(
    r"enters the battlefield tapped|enters tapped", re.IGNORECASE
)
_ETB_TAPPED_UPSIDE = re.compile(
    r"(?:scry|you gain|you may pay)", re.IGNORECASE
)
_FETCH_PATTERN = re.compile(
    r"[Ss]earch your library for (?:a|an)\s+(.*?)(?:\s+card)", re.IGNORECASE
)


@dataclass
class LandInfo:
    """Parsed mana-production data for a land card."""

    card: dict
    colors_produced: list[str] = field(default_factory=list)
    enters_tapped: bool = False
    is_fetch: bool = False
    fetch_targets: list[str] = field(default_factory=list)
    is_basic: bool = False
    produces_any_color: bool = False


def parse_land_colors(
    oracle_text: str,
    type_line: str,
    commander_colors: list[str] | None = None,
) -> LandInfo:
    """Parse a land's oracle text to extract mana production info.

    Args:
        oracle_text: The card's oracle text.
        type_line: The card's type line.
        commander_colors: Commander's color identity (for "any color" lands).

    Returns:
        LandInfo with parsed mana data.
    """
    info = LandInfo(card={})
    oracle = oracle_text or ""
    tl = type_line or ""
    tl_lower = tl.lower()
    colors: set[str] = set()

    # Check if basic land
    info.is_basic = "basic" in tl_lower

    # Extract colors from basic land types in type line
    # e.g. "Land — Plains Island" → W, U
    for basic_type, color in BASIC_TYPE_COLORS.items():
        if basic_type in tl_lower:
            colors.add(color)

    # Parse "{T}: Add {W} or {B}." patterns
    for match in _ADD_MANA_PATTERN.finditer(oracle):
        for sym in _MANA_SYMBOLS.finditer(match.group(1)):
            colors.add(sym.group(1))

    # Also catch "Add {W}" without tap symbol (e.g. triggered abilities)
    if not colors:
        for match in _ADD_MANA_SIMPLE.finditer(oracle):
            for sym in _MANA_SYMBOLS.finditer(match.group(1)):
                colors.add(sym.group(1))

    # "any color" detection
    if _ANY_COLOR.search(oracle):
        info.produces_any_color = True
        if commander_colors:
            colors.update(commander_colors)
        else:
            colors.update(["W", "U", "B", "R", "G"])

    # ETB tapped detection
    if _ETB_TAPPED.search(oracle):
        info.enters_tapped = True

    # Fetch land detection
    fetch_match = _FETCH_PATTERN.search(oracle)
    if fetch_match and "land" in oracle.lower():
        info.is_fetch = True
        target_text = fetch_match.group(1).lower()
        for basic_type, color in BASIC_TYPE_COLORS.items():
            if basic_type in target_text:
                info.fetch_targets.append(color)
                colors.add(color)

        # Generic fetch (e.g. "a basic land card") — all commander colors
        if not info.fetch_targets and ("basic land" in target_text or "land" in target_text):
            if commander_colors:
                info.fetch_targets = list(commander_colors)
            else:
                info.fetch_targets = ["W", "U", "B", "R", "G"]

    info.colors_produced = sorted(colors)
    return info


def count_color_pips(cards: list[dict]) -> dict[str, dict]:
    """Count colored mana pips in casting costs of non-land spells.

    Args:
        cards: List of card dicts with 'mana_cost' and 'type_line' keys.

    Returns:
        Dict mapping each color to:
          - total_pips: total pips of that color across all spells
          - max_pips: most pips of that color in a single spell
          - hardest_cast: (max_pips, cmc_of_that_spell) for Karsten lookup
    """
    result: dict[str, dict] = {}
    for color in "WUBRG":
        result[color] = {"total_pips": 0, "max_pips": 0, "hardest_cast": (0, 99)}

    for card in cards:
        type_line = (card.get("type_line") or "").lower()
        if "land" in type_line and "creature" not in type_line:
            continue

        mana_cost = card.get("mana_cost") or ""
        cmc = int(float(card.get("cmc", 0) or 0))
        if cmc < 1:
            cmc = 1  # Floor at 1 for Karsten lookup

        # Count pips per color in this card
        for color in "WUBRG":
            pips = mana_cost.count("{" + color + "}")
            if pips > 0:
                result[color]["total_pips"] += pips
                if pips > result[color]["max_pips"]:
                    result[color]["max_pips"] = pips
                    result[color]["hardest_cast"] = (pips, cmc)
                elif pips == result[color]["max_pips"]:
                    # Same pip count but earlier curve = harder to cast
                    _, existing_cmc = result[color]["hardest_cast"]
                    if cmc < existing_cmc:
                        result[color]["hardest_cast"] = (pips, cmc)

    return result


def compute_color_targets(
    spells: list[dict],
    commander_colors: list[str],
    total_lands: int,
) -> dict[str, int]:
    """Determine how many sources of each color the deck needs.

    Uses Karsten's hypergeometric framework: for the hardest-to-cast
    spell of each color, look up required sources for ~90% on-curve
    cast probability, then scale to actual land count.

    Args:
        spells: Non-land cards already selected for the deck.
        commander_colors: Commander's color identity.
        total_lands: Target number of total lands.

    Returns:
        Dict mapping each commander color to required source count.
    """
    pip_data = count_color_pips(spells)
    targets: dict[str, int] = {}

    for color in commander_colors:
        data = pip_data.get(color, {"max_pips": 0, "hardest_cast": (0, 99)})
        max_pips, hardest_cmc = data["hardest_cast"]

        if max_pips == 0:
            # Color in identity but no pips in deck — minimum floor
            targets[color] = 5
            continue

        # Look up Karsten table. Turn = CMC (we want to cast on curve).
        # Cap at what the table supports.
        pips_key = min(max_pips, 3)
        turn_key = min(max(hardest_cmc, pips_key), 7)

        raw_sources = KARSTEN_SOURCES_99.get(
            (pips_key, turn_key),
            # Fallback: linear interpolation for high CMC
            max(10, 30 - turn_key * 2),
        )

        # Scale from reference 36-land deck to actual land count
        scaled = round(raw_sources * total_lands / 36)

        # Floor: at least 5 sources per commander color
        targets[color] = max(5, scaled)

    # Ensure targets don't exceed total lands
    total_target = sum(targets.values())
    if total_target > total_lands * 1.5:
        # Scale down proportionally — overlap from duals/tri-lands
        # means we don't need targets to literally sum to total_lands
        scale_factor = (total_lands * 1.5) / total_target
        for color in targets:
            targets[color] = max(5, round(targets[color] * scale_factor))

    return targets


def _score_land(
    land_info: LandInfo,
    color_deficit: dict[str, float],
    commander_colors: list[str],
) -> float:
    """Score a nonbasic land by how well it fills color gaps.

    Args:
        land_info: Parsed land data.
        color_deficit: Remaining sources needed per color (can go negative).
        commander_colors: Commander's color identity.

    Returns:
        Numeric score (higher = better fit for current needs).
    """
    score = 0.0

    # Points for each color produced that's still below target
    relevant_colors = [
        c for c in land_info.colors_produced if c in commander_colors
    ]
    for color in relevant_colors:
        deficit = color_deficit.get(color, 0)
        if deficit > 0:
            # More points for colors we need more of
            score += deficit

    # Bonus for multi-color lands (cover multiple gaps simultaneously)
    colors_filling_gaps = sum(
        1 for c in relevant_colors if color_deficit.get(c, 0) > 0
    )
    if colors_filling_gaps >= 2:
        score *= 1.2
    if colors_filling_gaps >= 3:
        score *= 1.1

    # Fetch land bonus (flexibility + untapped)
    if land_info.is_fetch:
        score *= 1.1

    # ETB tapped penalty
    if land_info.enters_tapped:
        oracle = (land_info.card.get("oracle_text") or "").lower()
        if _ETB_TAPPED_UPSIDE.search(oracle):
            score *= 0.85  # Tapped but with upside
        else:
            score *= 0.8

    return score


def build_mana_base(
    land_candidates: list[tuple[dict, dict]],
    spells: list[dict],
    commander_colors: list[str],
    total_lands: int,
    max_budget: float | None = None,
    running_price: float = 0.0,
) -> list[SlotAssignment]:
    """Build a complete mana base using Karsten-style color targets.

    Replaces naive CVAR-scored land filling with mana-math-driven
    selection of nonbasics + pip-weighted basic land distribution.

    Args:
        land_candidates: List of (card_dict, scoring_dict) for land cards.
        spells: List of card dicts for non-land cards already in the deck.
        commander_colors: Commander's color identity (e.g. ["W", "U", "G"]).
        total_lands: Target total land count.
        max_budget: Optional total budget (across entire deck).
        running_price: Current deck price before adding lands.

    Returns:
        List of SlotAssignment for all lands (nonbasics + basics).
    """
    if not commander_colors:
        commander_colors = ["C"]  # Colorless commander

    # Step 1: Parse all land candidates
    parsed_lands: list[tuple[LandInfo, float]] = []
    for card, scoring in land_candidates:
        info = parse_land_colors(
            oracle_text=card.get("oracle_text") or "",
            type_line=card.get("type_line") or "",
            commander_colors=commander_colors,
        )
        info.card = card
        base_score = scoring.get("cvar_score", 0.0)
        parsed_lands.append((info, base_score))

    # Step 2: Compute per-color source targets
    color_targets = compute_color_targets(spells, commander_colors, total_lands)
    logger.info("Karsten color targets: %s", color_targets)

    # Step 3: Track remaining color needs (float for fractional tracking)
    color_deficit: dict[str, float] = {
        c: float(t) for c, t in color_targets.items()
    }

    # Step 4: Greedily select nonbasic lands
    assignments: list[SlotAssignment] = []
    used_names: set[str] = set()
    land_price = 0.0

    # Reserve slots for basics (at least 1 per commander color, min 3 total)
    min_basics = max(3, len(commander_colors))
    nonbasic_cap = total_lands - min_basics

    # Score and sort candidates by mana-math fit
    while len(assignments) < nonbasic_cap and parsed_lands:
        # Re-score based on current deficit
        scored: list[tuple[LandInfo, float, float]] = []
        for info, base in parsed_lands:
            name = info.card.get("name", "")
            if name in used_names:
                continue
            mana_score = _score_land(info, color_deficit, commander_colors)
            # Blend: 70% mana-math score, 30% CVAR base score
            combined = 0.7 * mana_score + 0.3 * base * 10
            scored.append((info, base, combined))

        if not scored:
            break

        scored.sort(key=lambda x: x[2], reverse=True)
        best_info, best_base, best_score = scored[0]
        card = best_info.card

        name = card.get("name", "")
        price = float(card.get("price_usd", 0) or 0)

        # Budget check
        if max_budget and running_price + land_price + price > max_budget:
            # Remove this candidate and try next
            parsed_lands = [
                (i, b) for i, b in parsed_lands if i.card.get("name") != name
            ]
            continue

        # Skip if this land produces no relevant colors and score is 0
        relevant = [c for c in best_info.colors_produced if c in commander_colors]
        if not relevant and not best_info.produces_any_color and best_score <= 0:
            parsed_lands = [
                (i, b) for i, b in parsed_lands if i.card.get("name") != name
            ]
            continue

        # Accept this land
        assignments.append(SlotAssignment(
            card=card,
            slot_role="land",
            score=round(best_score, 4),
            alternatives=[],
        ))
        used_names.add(name)
        land_price += price

        # Update color deficit
        for color in best_info.colors_produced:
            if color in color_deficit:
                color_deficit[color] -= 1.0

        # Fetch lands count as partial sources for their targets
        if best_info.is_fetch:
            for color in best_info.fetch_targets:
                if color in color_deficit and color not in best_info.colors_produced:
                    color_deficit[color] -= 0.5

        # Remove from candidate pool
        parsed_lands = [
            (i, b) for i, b in parsed_lands if i.card.get("name") != name
        ]

    # Step 5: Fill remaining slots with basics, weighted by remaining deficit
    basics_needed = total_lands - len(assignments)
    if basics_needed > 0 and commander_colors:
        _fill_basics_by_deficit(
            assignments, commander_colors, color_deficit, basics_needed,
        )

    logger.info(
        "Mana base: %d nonbasics + %d basics = %d total lands",
        total_lands - basics_needed,
        basics_needed,
        len(assignments),
    )

    return assignments


def _fill_basics_by_deficit(
    assignments: list[SlotAssignment],
    commander_colors: list[str],
    color_deficit: dict[str, float],
    count: int,
) -> None:
    """Add basic lands weighted by remaining color deficit.

    Colors with more remaining deficit get more basics. Never does
    even distribution — always proportional to actual need.

    Args:
        assignments: List to append basic SlotAssignments to.
        commander_colors: Commander's color identity.
        color_deficit: Remaining sources needed per color.
        count: Number of basics to add.
    """
    # Filter to commander colors that have basics
    available = [c for c in commander_colors if c in COLOR_TO_BASIC]
    if not available:
        return

    # Weight by deficit (floor at 0.5 so every color gets at least some)
    weights: dict[str, float] = {}
    for color in available:
        weights[color] = max(0.5, color_deficit.get(color, 0))

    total_weight = sum(weights.values())
    if total_weight <= 0:
        total_weight = len(available)
        weights = {c: 1.0 for c in available}

    # Distribute basics proportionally
    allocated: dict[str, int] = {}
    remaining = count
    for color in available:
        share = round(count * weights[color] / total_weight)
        allocated[color] = share

    # Adjust for rounding errors
    total_allocated = sum(allocated.values())
    diff = count - total_allocated
    if diff != 0:
        # Add/remove from the color with highest deficit
        sorted_colors = sorted(available, key=lambda c: weights[c], reverse=True)
        for i in range(abs(diff)):
            color = sorted_colors[i % len(sorted_colors)]
            allocated[color] += 1 if diff > 0 else -1

    # Create basic land assignments
    added = 0
    for color, num in allocated.items():
        name = COLOR_TO_BASIC[color]
        for i in range(max(0, num)):
            basic_card = {
                "id": f"basic-{name.lower()}-{added}",
                "name": name,
                "type_line": f"Basic Land — {name}",
                "oracle_text": "",
                "mana_cost": "",
                "cmc": 0.0,
                "color_identity": "[]",
                "price_usd": 0.0,
                "rarity": "common",
            }
            assignments.append(SlotAssignment(
                card=basic_card,
                slot_role="land",
                score=0.5,
                alternatives=[],
            ))
            added += 1

    logger.info(
        "Added %d basic lands: %s",
        added,
        {COLOR_TO_BASIC[c]: n for c, n in allocated.items() if n > 0},
    )
