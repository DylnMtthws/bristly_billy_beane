"""Hypergeometric role-reliability targets for deck building.

Generalizes the Karsten mana base math (pipeline/mana_base.py) to
answer: "How many copies of role X do I need to reliably see one
by turn T?" Uses the same hypergeometric CDF:

    P(>=1 in hand) = 1 - C(N-k, n) / C(N, n)

where N=deck_size, k=copies, n=cards_seen_by_turn.
"""

import json
import logging
from math import comb

from pydantic import BaseModel, Field

from sabermetrics.models.profile import CommanderProfile
from sabermetrics.models.template import DeckTemplate

logger = logging.getLogger(__name__)

# Cards seen by turn T in Commander (99-card, 7-card opening hand):
#   Turn 1: 7, Turn 2: 8, ..., Turn T: 6 + T
OPENING_HAND = 7

# Default role timing — when you need to see >=1 of this role
ROLE_TIMING: dict[str, dict] = {
    "ramp":       {"need_by_turn": 3, "reliability": 0.80},
    "draw":       {"need_by_turn": 4, "reliability": 0.80},
    "removal":    {"need_by_turn": 5, "reliability": 0.75},
    "board_wipe": {"need_by_turn": 7, "reliability": 0.60},
    "wincon":     {"need_by_turn": 9, "reliability": 0.70},
    "tutor":      {"need_by_turn": 6, "reliability": 0.50},
    "protection": {"need_by_turn": 5, "reliability": 0.60},
    "recursion":  {"need_by_turn": 7, "reliability": 0.50},
}


class RoleTarget(BaseModel):
    """Reliability target for one functional role."""

    role: str
    target_count: int = Field(ge=0)
    min_count: int = Field(ge=0)
    max_count: int = Field(ge=0)
    need_by_turn: int = Field(ge=1)
    reliability: float = Field(ge=0.0, le=1.0)
    is_engine_critical: bool = False


def copies_for_reliability(
    cards_seen: int,
    target_probability: float,
    deck_size: int = 99,
) -> int:
    """Minimum copies needed to see >=1 with given probability.

    Uses: P(>=1) = 1 - C(deck_size - k, cards_seen) / C(deck_size, cards_seen)
    Same math as Karsten mana base (pipeline/mana_base.py:KARSTEN_SOURCES_99).

    Args:
        cards_seen: Number of cards drawn/seen by the target turn.
        target_probability: Desired probability of seeing at least 1 copy.
        deck_size: Total cards in deck (default 99 for Commander).

    Returns:
        Minimum number of copies needed.
    """
    if cards_seen <= 0 or target_probability <= 0:
        return 0
    if target_probability >= 1.0:
        return deck_size

    denom = comb(deck_size, cards_seen)
    if denom == 0:
        return 1

    for k in range(1, deck_size + 1):
        remaining = deck_size - k
        if remaining < cards_seen:
            return k
        miss_prob = comb(remaining, cards_seen) / denom
        hit_prob = 1.0 - miss_prob
        if hit_prob >= target_probability:
            return k

    return deck_size


def compute_role_targets(
    profile: CommanderProfile,
    template: DeckTemplate,
) -> dict[str, RoleTarget]:
    """Compute per-role reliability targets from profile + template.

    Base targets from ROLE_TIMING, adjusted by:
    - Template counts as floors (ramp_count, draw_count, etc.)
    - Profile engine_dependencies -> engine-critical roles get higher reliability
    - Commander built-in capabilities -> reduce target if commander provides the role

    Args:
        profile: Commander profile with strategic analysis.
        template: Derived deck template with composition targets.

    Returns:
        Dict mapping role name to RoleTarget.
    """
    sp = profile.strategic_profile
    oracle_text = (profile.card_analysis.core_mechanic or "").lower()

    # Identify engine-critical roles from engine_dependencies
    engine_critical_roles: set[str] = set()
    for dep in sp.engine_dependencies:
        for trait in dep.engine_card_traits:
            trait_lower = trait.lower()
            for role in ROLE_TIMING:
                if role in trait_lower:
                    engine_critical_roles.add(role)

    # Check if commander provides built-in capabilities
    commander_provides: set[str] = set()
    commander_text = (
        " ".join(profile.card_analysis.triggered_abilities)
        + " " + " ".join(profile.card_analysis.activated_abilities)
        + " " + " ".join(profile.card_analysis.static_abilities)
        + " " + oracle_text
    ).lower()

    if "draw" in commander_text and "card" in commander_text:
        commander_provides.add("draw")
    if any(w in commander_text for w in ["add {", "add one mana", "treasure"]):
        commander_provides.add("ramp")
    if any(w in commander_text for w in ["destroy", "exile target", "deals damage"]):
        commander_provides.add("removal")
    if any(w in commander_text for w in ["return", "from your graveyard"]):
        commander_provides.add("recursion")

    # Template floors
    template_floors: dict[str, int] = {
        "ramp": template.ramp_count,
        "draw": template.draw_count,
        "removal": template.removal_count,
        "board_wipe": template.board_wipe_count,
    }

    targets: dict[str, RoleTarget] = {}

    for role, timing in ROLE_TIMING.items():
        need_by_turn = timing["need_by_turn"]
        base_reliability = timing["reliability"]

        # Engine-critical roles get boosted reliability
        is_engine = role in engine_critical_roles
        if is_engine:
            base_reliability = min(0.95, base_reliability + 0.10)

        # Commander providing the role reduces target
        if role in commander_provides:
            base_reliability = max(0.30, base_reliability - 0.15)

        cards_seen = OPENING_HAND + need_by_turn - 1  # 7 + T - 1
        target_count = copies_for_reliability(
            cards_seen=cards_seen,
            target_probability=base_reliability,
            deck_size=99,
        )

        # Apply template floor
        floor = template_floors.get(role, 0)
        target_count = max(target_count, floor)

        # Compute bounds
        min_count = max(1, target_count - 2) if target_count > 0 else 0
        max_count = min(target_count + 4, 20)

        targets[role] = RoleTarget(
            role=role,
            target_count=target_count,
            min_count=min_count,
            max_count=max_count,
            need_by_turn=need_by_turn,
            reliability=base_reliability,
            is_engine_critical=is_engine,
        )

    return targets


def role_need_multiplier(current_count: int, target_count: int) -> float:
    """Sigmoid utility: how badly does the deck need another card in this role.

    Below target: multiplier > 1.0 (urgently needed)
    At target: ~1.0
    Above target: < 1.0 (diminishing returns)

    Args:
        current_count: How many cards of this role are currently in the deck.
        target_count: How many the deck wants.

    Returns:
        Multiplier for the role's urgency (0.3 to 1.8).
    """
    if target_count == 0:
        return 0.5

    ratio = current_count / target_count
    if ratio < 0.5:
        return 1.8   # Critically underserved
    if ratio < 0.75:
        return 1.4   # Underserved
    if ratio < 1.0:
        return 1.15  # Almost there
    if ratio < 1.25:
        return 0.85  # Slightly over
    if ratio < 1.5:
        return 0.6   # Redundant
    return 0.3        # Heavily over-committed
