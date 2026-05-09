"""Tests for removal card detector."""

from sabermetrics.analytics.removal_detector import detect_removal_card


def test_swords_to_plowshares_detected() -> None:
    """Swords to Plowshares is single_target, creature, exile."""
    card = {
        "oracle_text": "Exile target creature. Its controller gains life equal to its power.",
        "type_line": "Instant",
        "cmc": 1,
    }
    result = detect_removal_card(card)
    assert result is not None
    assert result["removal_type"] == "single_target"
    assert result["target_type"] == "creature"
    assert result["is_exile"] is True
    assert result["is_instant"] is True


def test_beast_within_detected() -> None:
    """Beast Within targets any permanent."""
    card = {
        "oracle_text": "Destroy target permanent. Its controller creates a 3/3 green Beast creature token.",
        "type_line": "Instant",
        "cmc": 3,
    }
    result = detect_removal_card(card)
    assert result is not None
    assert result["removal_type"] == "single_target"
    assert result["target_type"] == "any"


def test_counterspell_detected() -> None:
    """Counterspell is a counterspell type."""
    card = {
        "oracle_text": "Counter target spell.",
        "type_line": "Instant",
        "cmc": 2,
    }
    result = detect_removal_card(card)
    assert result is not None
    assert result["removal_type"] == "counterspell"
    assert result["target_type"] == "any"


def test_blasphemous_act_detected() -> None:
    """Blasphemous Act is a board_wipe type."""
    card = {
        "oracle_text": "This spell costs {1} less to cast for each creature on the battlefield.\nBlasmphemous Act deals 13 damage to each creature.",
        "type_line": "Sorcery",
        "cmc": 9,
    }
    result = detect_removal_card(card)
    assert result is not None
    assert result["removal_type"] == "board_wipe"


def test_self_sacrifice_not_removal() -> None:
    """A self-sacrifice card without targeting is not removal."""
    card = {
        "oracle_text": "Sacrifice ~this: Draw a card.",
        "type_line": "Creature — Human Wizard",
        "cmc": 2,
    }
    result = detect_removal_card(card)
    assert result is None


def test_reminder_text_destruction_excluded() -> None:
    """Destruction keyword only in reminder text should not detect as removal."""
    card = {
        "oracle_text": "Create a Treasure token. (It's an artifact with \"{T}, Sacrifice this artifact: Add one mana of any color.\")\nDestroy target creature you control.",
        "type_line": "Sorcery",
        "cmc": 2,
    }
    # Has "destroy target...you control" which is negative pattern
    result = detect_removal_card(card)
    assert result is None


def test_generous_gift_detected() -> None:
    """Generous Gift destroys any permanent."""
    card = {
        "oracle_text": "Destroy target permanent. Its controller creates a 3/3 green Elephant creature token.",
        "type_line": "Instant",
        "cmc": 3,
    }
    result = detect_removal_card(card)
    assert result is not None
    assert result["removal_type"] == "single_target"
    assert result["target_type"] == "any"


def test_go_for_the_throat_detected() -> None:
    """Go for the Throat targets creatures."""
    card = {
        "oracle_text": "Destroy target nonartifact creature.",
        "type_line": "Instant",
        "cmc": 2,
    }
    result = detect_removal_card(card)
    assert result is not None
    assert result["removal_type"] == "single_target"
    assert result["target_type"] == "creature"
    assert result["is_instant"] is True


def test_day_of_judgment_is_board_wipe() -> None:
    """Day of Judgment is a board wipe."""
    card = {
        "oracle_text": "Destroy all creatures.",
        "type_line": "Sorcery",
        "cmc": 4,
    }
    result = detect_removal_card(card)
    assert result is not None
    assert result["removal_type"] == "board_wipe"


def test_bounce_detected() -> None:
    """Bounce spells are detected as removal."""
    card = {
        "oracle_text": "Return target nonland permanent to its owner's hand.",
        "type_line": "Instant",
        "cmc": 1,
    }
    result = detect_removal_card(card)
    assert result is not None
    assert result["removal_type"] == "single_target"


def test_removal_score_range() -> None:
    """Removal score should be in 0-1 range."""
    card = {
        "oracle_text": "Exile target creature.",
        "type_line": "Instant",
        "cmc": 1,
    }
    result = detect_removal_card(card)
    assert result is not None
    assert 0.0 <= result["removal_score"] <= 1.0
