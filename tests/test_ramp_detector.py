"""Tests for ramp detection with reminder text stripping."""

from sabermetrics.analytics.ramp_detector import (
    _strip_reminder_text,
    detect_ramp_card,
)


# --- Reminder text stripping ---


def test_strip_removes_parentheticals() -> None:
    """Treasure reminder text should be stripped."""
    oracle = (
        'Whenever Kitesail Larcenist enters, each opponent creates a Treasure token. '
        '(It\'s an artifact with "{T}, Sacrifice this artifact: Add one mana of any color.")'
    )
    stripped = _strip_reminder_text(oracle)
    assert "Sacrifice this artifact" not in stripped
    assert "Add one mana" not in stripped
    assert "Kitesail Larcenist" in stripped


def test_strip_preserves_non_reminder() -> None:
    """Non-parenthetical mana abilities like '{T}: Add {G}' should be preserved."""
    oracle = "{T}: Add {G}."
    stripped = _strip_reminder_text(oracle)
    assert stripped == oracle


def test_strip_removes_multiple_parentheticals() -> None:
    """Multiple parenthetical blocks are all removed."""
    oracle = "Do X (reminder 1) and Y (reminder 2)."
    stripped = _strip_reminder_text(oracle)
    assert "reminder 1" not in stripped
    assert "reminder 2" not in stripped
    assert "Do X" in stripped


# --- False positive prevention ---


def test_kitesail_larcenist_not_ramp() -> None:
    """Kitesail Larcenist creates Treasure for opponents — not ramp for us."""
    card = {
        "name": "Kitesail Larcenist",
        "type_line": "Creature — Human Pirate",
        "oracle_text": (
            "Flying\n"
            "Whenever Kitesail Larcenist enters, each opponent creates a Treasure token. "
            '(It\'s an artifact with "{T}, Sacrifice this artifact: Add one mana of any color.")\n'
            "Whenever an opponent sacrifices a token, you draw a card."
        ),
        "cmc": 3,
    }
    result = detect_ramp_card(card)
    assert result is None, f"Kitesail Larcenist should not be detected as ramp: {result}"


def test_treasure_only_in_reminder_not_ramp() -> None:
    """Card where 'Treasure' only appears inside reminder text should not be ramp."""
    # Fabricated card: refers to a Treasure token in reminder text only,
    # with the actual ability being something else entirely.
    card = {
        "name": "Fake Card With Reminder",
        "type_line": "Creature — Human",
        "oracle_text": (
            "When this creature dies, target opponent sacrifices a Treasure. "
            '(It\'s an artifact with "{T}, Sacrifice this artifact: Add one mana of any color.")'
        ),
        "cmc": 2,
    }
    result = detect_ramp_card(card)
    assert result is None, (
        f"Card with Treasure only in reminder text should not be ramp: {result}"
    )


def test_shell_shield_not_ramp() -> None:
    """Shell Shield is a protection spell — no ramp indicators."""
    card = {
        "name": "Shell Shield",
        "type_line": "Instant",
        "oracle_text": (
            "Kicker {1}\n"
            "Target creature you control gains hexproof until end of turn. "
            "If this spell was kicked, that creature also gets +0/+3 until end of turn."
        ),
        "cmc": 1,
    }
    result = detect_ramp_card(card)
    assert result is None, "Shell Shield should not be detected as ramp"


# --- True positive detection ---


def test_sol_ring_detected() -> None:
    """Sol Ring: classic mana rock, net_mana_rate=2.0."""
    card = {
        "name": "Sol Ring",
        "type_line": "Artifact",
        "oracle_text": "{T}: Add {C}{C}.",
        "cmc": 1,
    }
    result = detect_ramp_card(card)
    assert result is not None
    assert result["ramp_type"] == "rock"
    assert result["net_mana_rate"] == 2.0
    assert result["mana_output"] == 2.0


def test_cultivate_detected() -> None:
    """Cultivate: land ramp."""
    card = {
        "name": "Cultivate",
        "type_line": "Sorcery",
        "oracle_text": (
            "Search your library for up to two basic land cards, reveal those cards, "
            "put one onto the battlefield tapped and the other into your hand, "
            "then shuffle."
        ),
        "cmc": 3,
    }
    result = detect_ramp_card(card)
    assert result is not None
    assert result["ramp_type"] == "land_ramp"
    assert result["produces_colored"] is True


def test_llanowar_elves_detected() -> None:
    """Llanowar Elves: mana dork, produces colored."""
    card = {
        "name": "Llanowar Elves",
        "type_line": "Creature — Elf Druid",
        "oracle_text": "{T}: Add {G}.",
        "cmc": 1,
    }
    result = detect_ramp_card(card)
    assert result is not None
    assert result["ramp_type"] == "dork"
    assert result["produces_colored"] is True


def test_smothering_tithe_detected() -> None:
    """Smothering Tithe: treasure generator."""
    card = {
        "name": "Smothering Tithe",
        "type_line": "Enchantment",
        "oracle_text": (
            "Whenever an opponent draws a card, that player may pay {2}. "
            "If the player doesn't, you create a Treasure token."
        ),
        "cmc": 4,
    }
    result = detect_ramp_card(card)
    assert result is not None
    assert result["ramp_type"] == "treasure_gen"


# --- Restricted mana exclusion ---


def test_restricted_mana_excluded() -> None:
    """'Spend this mana only' should return None."""
    card = {
        "name": "Shrine of Burning Rage",
        "type_line": "Artifact",
        "oracle_text": (
            "{T}: Add {R}{R}. Spend this mana only to cast instant or sorcery spells."
        ),
        "cmc": 2,
    }
    result = detect_ramp_card(card)
    assert result is None, "Restricted mana should be excluded"


def test_pit_automaton_restricted() -> None:
    """Pit Automaton with restricted mana excluded."""
    card = {
        "name": "Pit Automaton",
        "type_line": "Artifact Creature",
        "oracle_text": (
            "{T}: Add {C}. Spend this mana only on colorless spells."
        ),
        "cmc": 2,
    }
    result = detect_ramp_card(card)
    assert result is None, "Pit Automaton restricted mana should be excluded"


def test_arcane_signet_detected() -> None:
    """Arcane Signet: any-color rock."""
    card = {
        "name": "Arcane Signet",
        "type_line": "Artifact",
        "oracle_text": "{T}: Add one mana of any color in your commander's color identity.",
        "cmc": 2,
    }
    result = detect_ramp_card(card)
    assert result is not None
    assert result["ramp_type"] == "rock"
    assert result["produces_colored"] is True
