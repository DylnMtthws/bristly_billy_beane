"""Removal package generator (6.5.4).

Deterministic removal + board wipe selection with role-specific quality scoring
and target-type diversity.
"""

import json
import logging
import re
from pathlib import Path

from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.slot_assigner import SlotAssignment

logger = logging.getLogger(__name__)

# --- Removal quality regexes ---

_FREE_CAST = re.compile(
    r"without paying (?:its|their) mana cost|if you control a commander",
    re.IGNORECASE,
)
_EXILE_EFFECT = re.compile(
    r"exile target|exiles target|exile all|exile each",
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
_SELF_DRAWBACK = re.compile(
    r"you (?:lose|pay|sacrifice|discard)",
    re.IGNORECASE,
)
_COUNTER_SPELL = re.compile(
    r"counter target (?:spell|activated|triggered)",
    re.IGNORECASE,
)


def _classify_removal_target(oracle: str) -> str:
    """Classify what type of permanent the removal targets."""
    if "target permanent" in oracle or "target nonland" in oracle:
        return "any"
    if "target creature" in oracle:
        return "creature"
    if "target artifact" in oracle:
        return "artifact"
    if "target enchantment" in oracle:
        return "enchantment"
    if "target planeswalker" in oracle:
        return "planeswalker"
    if "counter target spell" in oracle:
        return "any"
    return "any"


def _flexibility_score(oracle: str) -> float:
    """Score how many permanent types the removal can hit (1.0-3.0).

    "Any permanent" or "nonland permanent" scores highest.
    Hitting multiple named types scores in between.
    Single-type scores lowest.
    """
    oracle_lower = oracle.lower()

    # Broadest: any permanent / nonland permanent
    if "target permanent" in oracle_lower or "target nonland" in oracle_lower:
        return 3.0

    # Counterspells hit anything on the stack
    if _COUNTER_SPELL.search(oracle_lower):
        return 2.5

    # Count distinct types mentioned
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
    # "Destroy target" without specific type — treat as flexible
    if "target" in oracle_lower and ("destroy" in oracle_lower or "exile" in oracle_lower):
        return 2.0
    return 1.0


def _permanence_score(oracle: str) -> float:
    """Score removal permanence: exile > destroy > bounce (0.0-1.0)."""
    if _EXILE_EFFECT.search(oracle):
        return 1.0
    if _BOUNCE_EFFECT.search(oracle):
        return 0.2
    # Default "destroy" is middle ground
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


def _score_removal(
    card: dict,
    commander_colors: list[str],
    avg_cmc: float,
) -> float:
    """Score a removal card on role-specific quality.

    Signals:
    - Flexibility (permanent types hit): 1.0-3.0
    - Speed (instant/flash vs sorcery): 0.0-1.5
    - Permanence (exile > destroy > bounce): 0.0-1.0
    - Mana efficiency (CMC): 0.0-2.0
    - Free-cast potential: +2.0
    - Drawback scaling: -0.5 to 0
    - CVAR blend at 40%

    Args:
        card: Card dict with oracle_text, cmc, type_line, _cvar_score.
        commander_colors: Commander's color identity.
        avg_cmc: Target average CMC for the deck.

    Returns:
        Combined quality score (higher is better).
    """
    oracle = card.get("oracle_text") or ""
    cmc = float(card.get("cmc", 3) or 3)
    type_line = (card.get("type_line") or "").lower()
    cvar = float(card.get("_cvar_score", 0.3) or 0.3)

    role_score = 0.0

    # --- Flexibility ---
    role_score += _flexibility_score(oracle)

    # --- Speed ---
    if "instant" in type_line or "flash" in oracle.lower():
        role_score += 1.5
    elif "sorcery" in type_line:
        role_score += 0.0
    else:
        # Creatures/enchantments with removal ETBs — moderate speed
        role_score += 0.5

    # --- Permanence ---
    role_score += _permanence_score(oracle)

    # --- Mana efficiency ---
    role_score += _mana_efficiency_score(cmc)

    # --- Free-cast potential ---
    if _FREE_CAST.search(oracle):
        role_score += 2.0

    # --- Drawback scaling ---
    if _OPPONENT_DRAWBACK.search(oracle):
        role_score -= 0.5
    elif _SELF_DRAWBACK.search(oracle):
        role_score -= 0.2

    # --- Blend with CVAR (40% CVAR, 60% role-specific) ---
    # Max theoretical role_score ~9.5; normalize to 0-1
    normalized_role = min(role_score / 9.5, 1.0)
    final_score = 0.60 * normalized_role + 0.40 * cvar

    return final_score


class RemovalPackageGenerator:
    """Generate the removal + board wipe package for a deck."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def generate(
        self,
        color_identity: list[str],
        target_count: int,
        budget_remaining: float,
        template: DeckTemplate,
        already_placed: list[dict],
        role_tag_pool: list[dict],
        board_wipe_target: int = 2,
        commander_colors: list[str] | None = None,
        avg_cmc: float | None = None,
    ) -> list[SlotAssignment]:
        """Generate removal package with role-specific scoring and diversity.

        Args:
            color_identity: Commander's color identity.
            target_count: Target single-target removal count.
            budget_remaining: Remaining deck budget.
            template: Deck template for context.
            already_placed: Cards already in the deck.
            role_tag_pool: Pre-filtered cards with removal/board_wipe role_tags.
            board_wipe_target: Target number of board wipes.
            commander_colors: Commander's color identity (defaults to color_identity).
            avg_cmc: Target average CMC (defaults to template value).

        Returns:
            List of SlotAssignment for removal + board wipe cards.
        """
        colors = commander_colors or color_identity
        deck_avg_cmc = avg_cmc or template.avg_cmc_target

        used_names = {c.get("name", "") for c in already_placed}
        assignments: list[SlotAssignment] = []
        running_price = 0.0

        # Separate board wipes from single-target removal
        board_wipe_candidates: list[tuple[dict, float]] = []
        single_removal_candidates: list[tuple[dict, float]] = []

        for card in role_tag_pool:
            name = card.get("name", "")
            if name in used_names:
                continue

            oracle = (card.get("oracle_text") or "").lower()

            # Determine card's role_tags
            role_tags_raw = card.get("role_tags", "[]")
            if isinstance(role_tags_raw, str):
                try:
                    role_tags = json.loads(role_tags_raw)
                except (json.JSONDecodeError, TypeError):
                    role_tags = []
            else:
                role_tags = role_tags_raw or []

            is_board_wipe = "board_wipe" in role_tags or (
                "destroy all" in oracle or "exile all" in oracle
            )

            # Score with role-specific function
            score = _score_removal(card, colors, deck_avg_cmc)

            # Budget preference
            price = float(card.get("price_usd", 0) or 0)
            if price <= 2.0:
                score += 0.01

            if is_board_wipe:
                board_wipe_candidates.append((card, score))
            else:
                single_removal_candidates.append((card, score))

        # Sort both pools
        board_wipe_candidates.sort(key=lambda x: x[1], reverse=True)
        single_removal_candidates.sort(key=lambda x: x[1], reverse=True)

        # Fill board wipes first
        for card, score in board_wipe_candidates:
            if len([a for a in assignments if a.slot_role == "removal"
                    and "all" in (a.card.get("oracle_text") or "").lower()]) >= board_wipe_target:
                break

            name = card.get("name", "")
            if name in used_names:
                continue

            price = float(card.get("price_usd", 0) or 0)
            if budget_remaining > 0 and running_price + price > budget_remaining:
                continue

            assignments.append(SlotAssignment(
                card=card,
                slot_role="removal",
                score=round(score, 4),
                alternatives=[],
            ))
            used_names.add(name)
            running_price += price

        # Fill single-target removal
        # Track target diversity
        target_types = {"creature": 0, "artifact": 0, "enchantment": 0,
                        "planeswalker": 0, "any": 0}

        for card, score in single_removal_candidates:
            if len(assignments) >= target_count + board_wipe_target:
                break

            name = card.get("name", "")
            if name in used_names:
                continue

            price = float(card.get("price_usd", 0) or 0)
            if budget_remaining > 0 and running_price + price > budget_remaining:
                continue

            # Classify removal target
            oracle = (card.get("oracle_text") or "").lower()
            target = _classify_removal_target(oracle)

            # Soft diversity cap
            cap = max(2, (target_count + board_wipe_target) // 3)
            if target != "any" and target_types.get(target, 0) >= cap:
                continue

            assignments.append(SlotAssignment(
                card=card,
                slot_role="removal",
                score=round(score, 4),
                alternatives=[],
            ))
            used_names.add(name)
            running_price += price
            target_types[target] = target_types.get(target, 0) + 1

        logger.info(
            "Removal generator: %d cards (target %d removal + %d wipes), targets: %s",
            len(assignments), target_count, board_wipe_target, target_types,
        )
        return assignments
