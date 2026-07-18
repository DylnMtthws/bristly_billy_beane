"""Tests for the combat-gated payoff discount (Eiganjo class).

A payoff locked behind "attack with two or more creatures" (prepared MDFCs,
battalion, raid) rarely fires in a deck whose real lists run few attackers.
The synergy layer scored Eiganjo Dynastorian's "return all enchantments"
back half at 0.7675, making it the top vet replacement in three consecutive
builds; the discount is the numeric-layer fix.
"""

from sabermetrics.analytics.oracle_patterns import is_combat_gated


def test_detects_prepared_attack_condition():
    # Eiganjo Dynastorian's front face.
    assert is_combat_gated(
        "Vigilance\nWhenever you attack with two or more creatures, "
        "this creature becomes prepared."
    )


def test_detects_battalion_and_raid():
    assert is_combat_gated(
        "Battalion — Whenever this creature and at least two other "
        "creatures attack, draw a card."
    )
    assert is_combat_gated(
        "Raid — At the beginning of your end step, if you attacked "
        "this turn, draw a card."
    )


def test_unconditional_text_is_not_flagged():
    assert not is_combat_gated("Return all enchantment cards from your graveyard to the battlefield.")
    assert not is_combat_gated("Whenever you attack, draw a card.")  # single attacker fine
    assert not is_combat_gated("Whenever an enchantment you control enters, draw a card.")
    assert not is_combat_gated(None)


def test_afraid_of_the_dark_is_not_raid():
    """The \\b guard: words containing 'raid' must not match."""
    assert not is_combat_gated("This braid-related text — mentions nothing relevant.")
