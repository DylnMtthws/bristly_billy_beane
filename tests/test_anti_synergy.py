"""Tests for the anti-synergy veto (mass removal of the deck's engine type)."""

from sabermetrics.analytics.anti_synergy import (
    engine_types,
    is_anti_engine,
    mass_removal_types,
)


def test_detects_enchantment_mass_removal():
    assert mass_removal_types("Destroy all enchantments.") == {"enchantment"}
    # Nova Cleric
    assert mass_removal_types(
        "{2}{W}, {T}, Sacrifice Nova Cleric: Destroy all enchantments."
    ) == {"enchantment"}
    # Austere Command (modal, mentions both)
    assert "enchantment" in mass_removal_types(
        "Choose two — • Destroy all artifacts. • Destroy all enchantments. "
        "• Destroy all creatures with mana value 3 or less."
    )
    assert mass_removal_types("Exile all artifacts and enchantments.") == {
        "artifact", "enchantment",
    }


def test_positive_enchantment_text_is_not_flagged():
    """Mentioning the engine type is not the same as removing it."""
    assert mass_removal_types(
        "Whenever an enchantment you control enters, draw a card."
    ) == set()
    assert mass_removal_types("Destroy target enchantment.") == set()
    assert mass_removal_types("Enchant creature. Enchanted creature can't attack.") == set()


def test_engine_types_thresholds_and_creature_exclusion():
    # Eriette: enchantment 36 is the engine; creature wipes stay normal tech.
    assert engine_types({"enchantment": 36, "creature": 21, "artifact": 5}) == {
        "enchantment"
    }
    assert engine_types({"creature": 40}) == set()   # creatures never vetoed
    assert engine_types(None) == set()


def test_is_anti_engine():
    paraselene = {"oracle_text": "Destroy all enchantments. You gain 1 life for each."}
    wrath = {"oracle_text": "Destroy all creatures."}
    engine = {"enchantment"}
    assert is_anti_engine(paraselene, engine)
    assert not is_anti_engine(wrath, engine)      # creature wipes untouched
    assert not is_anti_engine(paraselene, set())  # no engine -> no veto
