"""Category coverage analysis for differentiator slot allocation (6.5.6).

Determines what functional categories remaining differentiator slots
should fill, based on profile synergy priorities and current deck coverage.
"""

import json
import logging

from sabermetrics.models.profile import CommanderProfile
from sabermetrics.models.template import SlotIntent

logger = logging.getLogger(__name__)


def analyze_category_coverage(
    profile: CommanderProfile,
    partial_deck: list[dict],
    remaining_slots: int,
    remaining_budget: float,
) -> list[SlotIntent]:
    """Analyze what categories the differentiator slots should target.

    1. Extract rewarded categories from profile synergy_priorities + engine_dependencies.
    2. Count current coverage in partial_deck using functional_categories tags.
    3. Compute slots_to_fill with redundancy bonus for commander-critical categories.
    4. Return ranked list of SlotIntents.

    Args:
        profile: Commander's strategic profile.
        partial_deck: Infrastructure cards already placed.
        remaining_slots: How many differentiator slots remain.
        remaining_budget: Budget remaining after infrastructure.

    Returns:
        Ranked list of SlotIntents describing what to fill.
    """
    sp = profile.strategic_profile

    # --- Step 1: Extract rewarded categories from profile ---
    category_priorities: dict[str, float] = {}

    # From synergy_priorities (e.g. {"sacrifice": ["sac outlet", "death trigger"]})
    for priority_name, traits in sp.synergy_priorities.items():
        # Map priority name and traits to functional categories
        for trait in traits:
            cat = _trait_to_category(trait)
            if cat:
                # Higher priority for categories explicitly named
                category_priorities[cat] = max(
                    category_priorities.get(cat, 0.0), 0.8
                )

    # From engine_dependencies
    for dep in sp.engine_dependencies:
        for trait in dep.engine_card_traits:
            cat = _trait_to_category(trait)
            if cat:
                category_priorities[cat] = max(
                    category_priorities.get(cat, 0.0), 0.9
                )

    # From win conditions
    for wc in sp.win_conditions:
        for card_name in wc.key_cards:
            # These are specific cards, not categories, but signal importance
            pass

    # If no explicit priorities found, infer from archetype
    if not category_priorities:
        category_priorities = _infer_from_archetype(sp.primary_archetype)

    # --- Step 2: Count current coverage ---
    current_counts: dict[str, int] = {}
    for card in partial_deck:
        cats_raw = card.get("functional_categories", "[]")
        if isinstance(cats_raw, str):
            try:
                cats = json.loads(cats_raw)
            except (json.JSONDecodeError, TypeError):
                cats = []
        else:
            cats = cats_raw or []

        for cat in cats:
            current_counts[cat] = current_counts.get(cat, 0) + 1

    # --- Step 3: Compute slots_to_fill with redundancy bonus ---
    intents: list[SlotIntent] = []
    total_allocated = 0

    for cat, priority in sorted(
        category_priorities.items(), key=lambda x: x[1], reverse=True
    ):
        current = current_counts.get(cat, 0)

        # Target count: 2-4 for high-priority categories (redundancy bonus),
        # 1-2 for medium priority
        if priority >= 0.8:
            target = max(3, min(4, remaining_slots // 4))
        elif priority >= 0.5:
            target = max(1, min(3, remaining_slots // 6))
        else:
            target = 1

        slots_needed = max(0, target - current)
        if slots_needed > 0 and total_allocated + slots_needed <= remaining_slots:
            intents.append(SlotIntent(
                category=cat,
                priority=priority,
                current_count=current,
                target_count=target,
                slots_to_fill=slots_needed,
            ))
            total_allocated += slots_needed

    logger.info(
        "Category coverage: %d intents, %d/%d slots allocated",
        len(intents), total_allocated, remaining_slots,
    )

    return intents


def _trait_to_category(trait: str) -> str | None:
    """Map a synergy trait string to a functional category name."""
    trait_lower = trait.lower().strip()

    mapping = {
        "sacrifice": "sacrifice_outlet",
        "sac outlet": "sacrifice_outlet",
        "sacrifice outlet": "sacrifice_outlet",
        "death trigger": "death_trigger",
        "dies trigger": "death_trigger",
        "etb": "etb_payoff",
        "enters the battlefield": "etb_payoff",
        "blink": "flicker",
        "flicker": "flicker",
        "aura": "aura",
        "enchant creature": "aura",
        "bestow": "aura",
        "equipment": "equipment",
        "equip": "equipment",
        "counter": "counter",
        "+1/+1 counter": "counter",
        "token": "token_generation",
        "tokens": "token_generation",
        "treasure": "treasure_generation",
        "graveyard": "graveyard_payoff",
        "recursion": "recursion",
        "reanimate": "recursion",
        "anthem": "anthem",
        "lord": "anthem",
        "lifegain": "lifegain_payoff",
        "life gain": "lifegain_payoff",
        "draw trigger": "draw_trigger",
        "damage trigger": "damage_trigger",
        "cast trigger": "cast_trigger",
        "magecraft": "cast_trigger",
        "storm": "cast_trigger",
        "evasion": "evasion",
        "unblockable": "evasion",
        "mana doubler": "mana_doubler",
    }

    # Direct match
    if trait_lower in mapping:
        return mapping[trait_lower]

    # Substring match
    for key, cat in mapping.items():
        if key in trait_lower:
            return cat

    return None


def _infer_from_archetype(archetype: str) -> dict[str, float]:
    """Infer category priorities from archetype name."""
    arch_lower = archetype.lower()
    priorities: dict[str, float] = {}

    if "aristocrat" in arch_lower or "sacrifice" in arch_lower:
        priorities = {"sacrifice_outlet": 0.9, "death_trigger": 0.8, "token_generation": 0.6}
    elif "aura" in arch_lower or "enchantress" in arch_lower:
        priorities = {"aura": 0.9, "draw_trigger": 0.7}
    elif "voltron" in arch_lower or "equipment" in arch_lower:
        priorities = {"equipment": 0.9, "evasion": 0.7, "aura": 0.6}
    elif "blink" in arch_lower or "flicker" in arch_lower:
        priorities = {"flicker": 0.9, "etb_payoff": 0.8}
    elif "token" in arch_lower or "go wide" in arch_lower:
        priorities = {"token_generation": 0.9, "anthem": 0.7}
    elif "counter" in arch_lower or "+1/+1" in arch_lower:
        priorities = {"counter": 0.9, "anthem": 0.5}
    elif "storm" in arch_lower or "spellslinger" in arch_lower:
        priorities = {"cast_trigger": 0.9, "draw_trigger": 0.7}
    elif "graveyard" in arch_lower or "reanimate" in arch_lower:
        priorities = {"graveyard_payoff": 0.9, "recursion": 0.8}
    elif "lifegain" in arch_lower:
        priorities = {"lifegain_payoff": 0.9}
    else:
        # Generic defaults
        priorities = {"etb_payoff": 0.5, "token_generation": 0.4}

    return priorities
