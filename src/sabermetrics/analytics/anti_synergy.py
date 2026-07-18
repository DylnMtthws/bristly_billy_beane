"""Anti-synergy detection: cards that mass-remove the deck's own engine type.

SME review found the synergy layer counting *mentions* of "enchantment" as
positive with no polarity: Nova Cleric, Paraselene and Austere Command (all
"destroy all enchantments" effects) were placed in an enchantress deck whose
engine they erase. Mentioning the engine type is not the same as supporting it.

Scope is deliberate: only non-creature engine types (enchantment, artifact)
are vetoed. Creature board wipes are normal Commander tech even in
creature-heavy decks (they rebuild; wipes answer opposing boards), and the
board_wipe_target already governs their count. Mass enchantment/artifact
removal in a deck built ON that type is asymmetric self-harm with no such
justification.
"""

from __future__ import annotations

import re

# "Destroy all enchantments", "exile all artifacts and enchantments",
# "destroy all other enchantments", "each player sacrifices all enchantments".
# Captures the whole clause (to the sentence end) so multi-type sweeps like
# "all artifacts and enchantments" report every type they hit.
_MASS_CLAUSE = re.compile(
    r"(?:destroy|exile|sacrifice)s? all ([^.\n]*)",
    re.IGNORECASE,
)
_TYPE_WORD = {
    t: re.compile(rf"\b{t}s?\b", re.IGNORECASE)
    for t in ("enchantment", "artifact")
}

# Engine types eligible for the veto (see module docstring for why creatures
# are excluded), and the target count above which a type is "the engine".
VETOABLE_TYPES = frozenset({"enchantment", "artifact"})
ENGINE_TYPE_MIN_TARGET = 25


def engine_types(type_targets: dict[str, int] | None) -> set[str]:
    """Non-creature types the deck's engine is built on.

    Args:
        type_targets: Corpus-derived type targets from the template.

    Returns:
        Targeted types in VETOABLE_TYPES at or above the engine threshold.
    """
    if not type_targets:
        return set()
    return {
        t for t, target in type_targets.items()
        if t in VETOABLE_TYPES and target >= ENGINE_TYPE_MIN_TARGET
    }


def mass_removal_types(oracle_text: str | None) -> set[str]:
    """Types this card mass-removes ('destroy all enchantments' -> {enchantment})."""
    if not oracle_text:
        return set()
    found: set[str] = set()
    for m in _MASS_CLAUSE.finditer(oracle_text):
        clause = m.group(1)
        for t, pattern in _TYPE_WORD.items():
            if pattern.search(clause):
                found.add(t)
    return found


def is_anti_engine(card: dict, engine: set[str]) -> bool:
    """Whether the card mass-removes one of the deck's engine types.

    Args:
        card: Card dict with oracle_text.
        engine: The deck's engine types (from :func:`engine_types`).

    Returns:
        True when including this card would let it erase the deck's own engine.
    """
    if not engine:
        return False
    return bool(mass_removal_types(card.get("oracle_text")) & engine)
