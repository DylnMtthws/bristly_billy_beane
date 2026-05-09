"""Slot-aware deck assembler (D6.1).

Fills exactly 99 cards (excluding commander) by distributing
candidates across functional slots: lands, ramp, draw, removal,
wincon, utility, other.

Target composition varies by power level and archetype.
"""

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SlotRole = Literal["ramp", "draw", "removal", "protection", "wincon", "utility", "land", "other"]

# Basic land names mapped to their color identity
BASIC_LANDS: dict[str, str] = {
    "Plains": "W",
    "Island": "U",
    "Swamp": "B",
    "Mountain": "R",
    "Forest": "G",
}

# Default target compositions by power bracket
# These represent ideal counts for each role in a 99-card deck
TARGET_COMPOSITIONS: dict[int, dict[str, int]] = {
    1: {  # Precon-level
        "land": 40,
        "ramp": 8,
        "draw": 6,
        "removal": 5,
        "wincon": 3,
        "utility": 15,
        "other": 22,
    },
    2: {  # Focused casual
        "land": 38,
        "ramp": 10,
        "draw": 7,
        "removal": 6,
        "wincon": 4,
        "utility": 14,
        "other": 20,
    },
    3: {  # Optimized casual
        "land": 36,
        "ramp": 12,
        "draw": 8,
        "removal": 7,
        "wincon": 5,
        "utility": 14,
        "other": 17,
    },
    4: {  # High power
        "land": 34,
        "ramp": 13,
        "draw": 9,
        "removal": 8,
        "wincon": 6,
        "utility": 13,
        "other": 16,
    },
    5: {  # cEDH
        "land": 30,
        "ramp": 15,
        "draw": 10,
        "removal": 9,
        "wincon": 7,
        "utility": 13,
        "other": 15,
    },
}


class SlotAssignment(BaseModel):
    """A card assigned to a specific slot in the deck."""

    card: dict
    slot_role: SlotRole
    score: float  # Combined CVAR + LLM fit score
    alternatives: list[str] = Field(default_factory=list)  # card_ids


class AssemblyResult(BaseModel):
    """Result of slot-aware deck assembly."""

    assignments: list[SlotAssignment]
    composition: dict[str, int]  # Actual counts per slot
    target_composition: dict[str, int]
    total_price: float
    warnings: list[str] = Field(default_factory=list)


def _classify_card_role(card: dict, llm_role: str | None = None) -> SlotRole:
    """Determine a card's primary functional role.

    Uses LLM-assigned role if available, otherwise heuristic detection.
    """
    # Trust LLM classification if provided
    if llm_role and llm_role in ("ramp", "draw", "removal", "wincon", "utility", "land", "other"):
        return llm_role

    type_line = (card.get("type_line") or "").lower()
    oracle_text = (card.get("oracle_text") or "").lower()

    # Land detection
    if "land" in type_line and "creature" not in type_line:
        return "land"

    # Ramp detection
    ramp_indicators = [
        "add" in oracle_text and ("mana" in oracle_text or "{" in oracle_text),
        "search your library for a" in oracle_text and "land" in oracle_text,
        "put" in oracle_text and "land" in oracle_text and "battlefield" in oracle_text,
    ]
    if any(ramp_indicators):
        return "ramp"

    # Draw detection
    draw_indicators = [
        "draw" in oracle_text and "card" in oracle_text,
        "look at the top" in oracle_text and "library" in oracle_text,
    ]
    if any(draw_indicators):
        return "draw"

    # Removal detection
    removal_indicators = [
        "destroy target" in oracle_text,
        "exile target" in oracle_text,
        "counter target spell" in oracle_text,
        "destroy all" in oracle_text,
        "exile all" in oracle_text,
    ]
    if any(removal_indicators):
        return "removal"

    # Win condition detection
    wincon_indicators = [
        "win the game" in oracle_text,
        "extra turn" in oracle_text,
        "infinite" in oracle_text,
        "each opponent loses" in oracle_text,
        "damage to each opponent" in oracle_text,
    ]
    if any(wincon_indicators):
        return "wincon"

    return "utility"


def get_target_composition(
    power_target: int,
    strategy: str | None = None,
) -> dict[str, int]:
    """Get the target card composition for a given power level.

    Args:
        power_target: Power bracket 1-5.
        strategy: Optional strategy hint to adjust composition.

    Returns:
        Dict mapping slot roles to target card counts (summing to 99).
    """
    composition = dict(TARGET_COMPOSITIONS.get(power_target, TARGET_COMPOSITIONS[3]))

    # Adjust for known strategies
    if strategy:
        strategy_lower = strategy.lower()
        if "aggro" in strategy_lower or "voltron" in strategy_lower:
            composition["wincon"] += 3
            composition["other"] -= 3
        elif "control" in strategy_lower:
            composition["removal"] += 3
            composition["draw"] += 2
            composition["other"] -= 5
        elif "combo" in strategy_lower:
            composition["wincon"] += 4
            composition["draw"] += 2
            composition["other"] -= 4
            composition["removal"] -= 2
        elif "stax" in strategy_lower:
            composition["utility"] += 4
            composition["other"] -= 4

    # Ensure total is exactly 99
    total = sum(composition.values())
    if total != 99:
        composition["other"] += 99 - total

    return composition


def fill_slots(
    scored_candidates: list[tuple[dict, dict]],
    target_composition: dict[str, int],
    max_budget: float | None = None,
    commander_colors: list[str] | None = None,
    alternatives_per_slot: int = 3,
) -> AssemblyResult:
    """Fill 99 deck slots from scored candidates.

    Distributes cards across functional roles to match the target
    composition as closely as possible while maximizing total score.

    Land slots are split between nonbasic lands (from candidates) and
    basic lands (generated), ensuring every deck has a functional mana base.

    Args:
        scored_candidates: List of (card_dict, scoring_dict) tuples.
            scoring_dict has keys: cvar_score, llm_fit_score, slot_role.
        target_composition: Target counts per role (must sum to 99).
        max_budget: Optional total budget constraint.
        commander_colors: Commander's color identity for land selection.
        alternatives_per_slot: Number of alternatives to track per card.

    Returns:
        AssemblyResult with 99 assigned cards.
    """
    from sabermetrics.pipeline.mana_base import build_mana_base

    warnings: list[str] = []
    colors = commander_colors or []

    land_target = target_composition.get("land", 36)

    # Separate land candidates from non-land candidates
    land_scored: list[tuple[dict, dict]] = []
    nonland_scored: list[tuple[dict, dict]] = []

    for card, scoring in scored_candidates:
        role = scoring.get("slot_role", "other")
        type_line = (card.get("type_line") or "").lower()
        is_land = role == "land" or ("land" in type_line and "creature" not in type_line)
        if is_land:
            land_scored.append((card, scoring))
        else:
            nonland_scored.append((card, scoring))

    # Group non-land candidates by role
    nonland_roles: dict[str, list[tuple[dict, float]]] = {
        role: [] for role in target_composition if role != "land"
    }

    for card, scoring in nonland_scored:
        role = scoring.get("slot_role", "other")
        if role == "land":
            role = "other"
        if role not in nonland_roles:
            role = "other"

        cvar = scoring.get("cvar_score", 0.0)
        llm_fit = scoring.get("llm_fit_score", 5) / 10.0
        combined = 0.6 * cvar + 0.4 * llm_fit
        nonland_roles[role].append((card, combined))

    for role in nonland_roles:
        nonland_roles[role].sort(key=lambda x: x[1], reverse=True)

    # Pass 1: Fill non-land roles up to target count
    assignments: list[SlotAssignment] = []
    used_names: set[str] = set()
    running_price = 0.0

    actual_composition: dict[str, int] = {role: 0 for role in target_composition}

    for role, target_count in target_composition.items():
        if role == "land":
            continue  # Lands handled by mana base builder

        candidates = nonland_roles.get(role, [])
        filled = 0

        for card, score in candidates:
            if filled >= target_count:
                break

            name = card.get("name", "")
            if name in used_names:
                continue

            price = float(card.get("price_usd", 0) or 0)
            if max_budget and running_price + price > max_budget:
                continue

            alts: list[str] = []
            for alt_card, _ in candidates:
                if len(alts) >= alternatives_per_slot:
                    break
                alt_name = alt_card.get("name", "")
                alt_id = alt_card.get("id", "")
                if alt_name != name and alt_name not in used_names:
                    alts.append(alt_id)

            assignments.append(SlotAssignment(
                card=card,
                slot_role=role,
                score=round(score, 4),
                alternatives=alts[:alternatives_per_slot],
            ))
            used_names.add(name)
            running_price += price
            filled += 1
            actual_composition[role] = filled

    # Pass 2: Fill remaining non-land slots from overflow
    nonland_slots_target = 99 - land_target
    remaining_needed = nonland_slots_target - len(assignments)
    if remaining_needed > 0:
        overflow: list[tuple[dict, float, str]] = []
        for role, candidates in nonland_roles.items():
            for card, score in candidates:
                name = card.get("name", "")
                if name not in used_names:
                    overflow.append((card, score, role))

        overflow.sort(key=lambda x: x[1], reverse=True)

        for card, score, role in overflow:
            if remaining_needed <= 0:
                break

            name = card.get("name", "")
            if name in used_names:
                continue

            price = float(card.get("price_usd", 0) or 0)
            if max_budget and running_price + price > max_budget:
                continue

            assignments.append(SlotAssignment(
                card=card,
                slot_role="other",
                score=round(score, 4),
                alternatives=[],
            ))
            used_names.add(name)
            running_price += price
            actual_composition["other"] = actual_composition.get("other", 0) + 1
            remaining_needed -= 1

    # Pass 3: Build mana base using Karsten-style calculator
    spells = [a.card for a in assignments]

    # Filter out land candidates whose names are already used
    available_lands = [
        (card, scoring) for card, scoring in land_scored
        if card.get("name", "") not in used_names
    ]

    land_assignments = build_mana_base(
        land_candidates=available_lands,
        spells=spells,
        commander_colors=colors,
        total_lands=land_target,
        max_budget=max_budget,
        running_price=running_price,
    )

    for la in land_assignments:
        assignments.append(la)
        used_names.add(la.card.get("name", ""))
        running_price += float(la.card.get("price_usd", 0) or 0)
    actual_composition["land"] = len(land_assignments)

    if len(assignments) < 99:
        warnings.append(
            f"Only {len(assignments)} cards assigned, need 99. "
            f"Insufficient candidates passed filters."
        )

    # Check composition health
    for role, target in target_composition.items():
        actual = actual_composition.get(role, 0)
        if actual < target * 0.5 and role != "other":
            warnings.append(
                f"Low {role} count: {actual}/{target} target"
            )

    return AssemblyResult(
        assignments=assignments,
        composition=actual_composition,
        target_composition=target_composition,
        total_price=round(running_price, 2),
        warnings=warnings,
    )


