"""Template derivation from commander profiles (6.5.3).

Derives deck composition targets from the strategic profile,
mostly via formulaic logic with one optional small Haiku call
for avg_cmc_target and creature_density interpretation.
"""

import logging
from pathlib import Path

from sabermetrics.models.profile import CommanderProfile
from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.mana_base import target_land_count

logger = logging.getLogger(__name__)

# Base ramp counts by power target
_BASE_RAMP: dict[int, int] = {1: 8, 2: 10, 3: 10, 4: 12, 5: 14}

# Draw counts by speed tier
_DRAW_BY_SPEED: dict[str, int] = {"fast": 7, "midrange": 8, "slow": 10}

# Removal counts by interaction density
_REMOVAL_BY_DENSITY: dict[str, int] = {"low": 4, "medium": 6, "high": 9}

# Board wipe counts by archetype keywords
_BOARD_WIPE_DEFAULTS: dict[str, int] = {
    "aggro": 1, "voltron": 1, "combo": 2,
    "control": 4, "stax": 3, "midrange": 2,
}


def derive_deck_template(
    profile: CommanderProfile,
    budget: float = 200.0,
    power_target: int = 3,
    db_path: Path | None = None,
) -> DeckTemplate:
    """Derive a deck template from commander profile.

    Mostly formulaic with profile-driven adjustments.

    Args:
        profile: The commander's strategic profile.
        budget: Total deck budget in USD.
        power_target: Target power bracket 1-5.
        db_path: Path to database (for optional Haiku call).

    Returns:
        DeckTemplate with profile-derived composition targets.
    """
    sp = profile.strategic_profile

    # --- Commander CMC ---
    cmdr_cmc = _parse_commander_cmc(profile)

    # --- Avg CMC target ---
    avg_cmc = _estimate_avg_cmc(sp, power_target)

    # --- Land count from Karsten ---
    land_count = target_land_count(avg_cmc)

    # --- Ramp count ---
    base_ramp = _BASE_RAMP.get(power_target, 10)
    ramp_count = base_ramp + max(0, cmdr_cmc - 3)
    ramp_count = max(5, min(18, ramp_count))

    # --- Draw count ---
    speed = sp.strategic_constraints.speed_tier
    draw_count = _DRAW_BY_SPEED.get(speed, 8)
    # Reduce if commander provides card advantage
    oracle = (profile.card_analysis.core_mechanic or "").lower()
    if "draw" in oracle or "card" in oracle:
        draw_count = max(3, draw_count - 2)

    # --- Removal count ---
    density = sp.strategic_constraints.interaction_density
    removal_count = _REMOVAL_BY_DENSITY.get(density, 6)

    # --- Board wipe count ---
    archetype_lower = sp.primary_archetype.lower()
    board_wipe_count = 2  # default
    for key, count in _BOARD_WIPE_DEFAULTS.items():
        if key in archetype_lower:
            board_wipe_count = count
            break

    # --- Creature density ---
    creature_density = _estimate_creature_density(sp)

    # --- Differentiator slots ---
    infrastructure = land_count + ramp_count + draw_count + removal_count + board_wipe_count
    differentiator_slots = max(10, 99 - infrastructure)

    # --- Curve shape ---
    curve_shape = _estimate_curve(avg_cmc, power_target)

    template = DeckTemplate(
        land_count=land_count,
        ramp_count=ramp_count,
        draw_count=draw_count,
        removal_count=removal_count,
        board_wipe_count=board_wipe_count,
        creature_density=creature_density,
        differentiator_slots=differentiator_slots,
        avg_cmc_target=avg_cmc,
        curve_shape=curve_shape,
    )

    logger.info(
        "Template derived: %d lands, %d ramp, %d draw, %d removal, "
        "%d board wipes, %d differentiator slots (avg CMC %.1f)",
        land_count, ramp_count, draw_count, removal_count,
        board_wipe_count, differentiator_slots, avg_cmc,
    )

    return template


def _parse_commander_cmc(profile: CommanderProfile) -> int:
    """Extract commander CMC from card analysis."""
    mana_cost = profile.card_analysis.mana_cost or ""
    # Count symbols
    cmc = 0
    i = 0
    while i < len(mana_cost):
        if mana_cost[i] == "{":
            end = mana_cost.index("}", i)
            symbol = mana_cost[i + 1:end]
            if symbol.isdigit():
                cmc += int(symbol)
            elif symbol in "WUBRGC":
                cmc += 1
            elif symbol == "X":
                pass  # X doesn't contribute to base CMC
            i = end + 1
        else:
            i += 1
    return max(1, cmc)


def _estimate_avg_cmc(sp, power_target: int) -> float:
    """Estimate target average CMC from profile and power target."""
    speed = sp.strategic_constraints.speed_tier
    base = {1: 3.5, 2: 3.2, 3: 3.0, 4: 2.8, 5: 2.3}
    avg = base.get(power_target, 3.0)

    if speed == "fast":
        avg -= 0.3
    elif speed == "slow":
        avg += 0.3

    return round(max(1.5, min(5.0, avg)), 1)


def _estimate_creature_density(sp) -> float:
    """Estimate creature density from archetype."""
    archetype = sp.primary_archetype.lower()
    if any(k in archetype for k in ["tribal", "aggro", "creature"]):
        return 0.55
    if any(k in archetype for k in ["spell", "storm", "control", "stax"]):
        return 0.25
    if any(k in archetype for k in ["enchantment", "aura"]):
        return 0.30
    return 0.40


def _estimate_curve(avg_cmc: float, power_target: int) -> dict[int, int]:
    """Build approximate mana curve distribution for non-land cards."""
    # Total non-land spells (assume ~36 lands, ~63 spells)
    total_spells = 63
    if avg_cmc <= 2.5:
        dist = {0: 2, 1: 10, 2: 18, 3: 16, 4: 10, 5: 4, 6: 2, 7: 1}
    elif avg_cmc <= 3.5:
        dist = {0: 1, 1: 7, 2: 14, 3: 17, 4: 12, 5: 7, 6: 3, 7: 2}
    else:
        dist = {0: 1, 1: 5, 2: 10, 3: 14, 4: 14, 5: 10, 6: 5, 7: 4}

    # Scale to total_spells
    total = sum(dist.values())
    if total > 0:
        dist = {k: max(0, round(v * total_spells / total)) for k, v in dist.items()}

    return dist
