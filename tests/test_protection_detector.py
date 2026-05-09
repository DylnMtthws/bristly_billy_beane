"""Tests for protection card detector."""

from sabermetrics.analytics.protection_detector import detect_protection_card


def test_swiftfoot_boots_detected() -> None:
    """Swiftfoot Boots grants hexproof, not board-wide."""
    card = {
        "oracle_text": "Equipped creature has hexproof and haste.\nEquip {1}",
        "type_line": "Artifact — Equipment",
        "cmc": 2,
    }
    result = detect_protection_card(card)
    assert result is not None
    assert result["protection_type"] == "hexproof"
    assert result["is_board_wide"] is False


def test_heroic_intervention_detected() -> None:
    """Heroic Intervention is board-wide hexproof + indestructible."""
    card = {
        "oracle_text": "Permanents you control gain hexproof and indestructible until end of turn.",
        "type_line": "Instant",
        "cmc": 2,
    }
    result = detect_protection_card(card)
    assert result is not None
    assert result["is_board_wide"] is True
    assert result["is_instant"] is True


def test_teferis_protection_detected() -> None:
    """Teferi's Protection is phasing type."""
    card = {
        "oracle_text": "Until your next turn, your life total can't change and you gain protection from everything. All permanents you control phase out.",
        "type_line": "Instant",
        "cmc": 3,
    }
    result = detect_protection_card(card)
    assert result is not None
    assert result["protection_type"] == "phasing"


def test_opponent_hexproof_excluded() -> None:
    """Granting opponent hexproof is not protection for us."""
    card = {
        "oracle_text": "Target creature an opponent controls gains hexproof until end of turn.",
        "type_line": "Instant",
        "cmc": 1,
    }
    result = detect_protection_card(card)
    assert result is None


def test_lightning_greaves_detected() -> None:
    """Lightning Greaves grants shroud (classified as hexproof)."""
    card = {
        "oracle_text": "Equipped creature has shroud and haste.\nEquip {0}",
        "type_line": "Artifact — Equipment",
        "cmc": 2,
    }
    result = detect_protection_card(card)
    assert result is not None
    assert result["protection_type"] == "hexproof"


def test_ward_detected() -> None:
    """Ward ability is detected as protection."""
    card = {
        "oracle_text": "Ward {2}",
        "type_line": "Creature — Human Wizard",
        "cmc": 3,
    }
    result = detect_protection_card(card)
    assert result is not None
    assert result["protection_type"] == "ward"


def test_totem_armor_detected() -> None:
    """Totem armor is detected as protection."""
    card = {
        "oracle_text": "Enchant creature\nEnchanted creature gets +2/+2.\nTotem armor",
        "type_line": "Enchantment — Aura",
        "cmc": 3,
    }
    result = detect_protection_card(card)
    assert result is not None
    assert result["protection_type"] == "totem_armor"


def test_deflecting_swat_detected() -> None:
    """Deflecting Swat is redirect type with free cast."""
    card = {
        "oracle_text": "If you control a commander, you may cast this spell without paying its mana cost.\nYou may choose new targets for target spell or ability.",
        "type_line": "Instant",
        "cmc": 3,
    }
    result = detect_protection_card(card)
    assert result is not None
    assert result["protection_type"] == "redirect"
    assert result["is_free_cast"] is True


def test_loses_hexproof_excluded() -> None:
    """'Loses hexproof' is removal of protection, not granting it."""
    card = {
        "oracle_text": "Target creature loses hexproof until end of turn.",
        "type_line": "Instant",
        "cmc": 1,
    }
    result = detect_protection_card(card)
    assert result is None


def test_protection_score_range() -> None:
    """Protection score should be in 0-1 range."""
    card = {
        "oracle_text": "Permanents you control gain hexproof and indestructible until end of turn.",
        "type_line": "Instant",
        "cmc": 2,
    }
    result = detect_protection_card(card)
    assert result is not None
    assert 0.0 <= result["protection_score"] <= 1.0


def test_reminder_text_ward_excluded() -> None:
    """Ward only in reminder text should not trigger detection."""
    card = {
        "oracle_text": "Create a 1/1 white Spirit creature token with flying. (Whenever this creature becomes the target of a spell or ability an opponent controls, counter it unless that player pays ward {2}.)",
        "type_line": "Instant",
        "cmc": 2,
    }
    result = detect_protection_card(card)
    # After stripping reminder text, "ward" only appears inside parens
    assert result is None
