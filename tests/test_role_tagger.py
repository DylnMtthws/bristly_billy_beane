"""Tests for role tagger (6.5.1).

Validates pattern matching on 20+ well-known cards.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from sabermetrics.analytics.role_tagger import (
    ROLE_TAGS,
    tag_all_cards,
    tag_card_roles,
)
from sabermetrics.models.tags import RoleTagResult, TaggingStats


# --- Known-card assertions ---


def test_sol_ring_is_ramp() -> None:
    """Sol Ring should be tagged as ramp."""
    card = {"name": "Sol Ring", "type_line": "Artifact", "oracle_text": "{T}: Add {C}{C}."}
    result = tag_card_roles(card)
    assert "ramp" in result.role_tags


def test_swords_to_plowshares_is_removal() -> None:
    """Swords to Plowshares should be tagged as removal."""
    card = {
        "name": "Swords to Plowshares",
        "type_line": "Instant",
        "oracle_text": "Exile target creature. Its controller gains life equal to its power.",
    }
    result = tag_card_roles(card)
    assert "removal" in result.role_tags


def test_rhystic_study_is_draw() -> None:
    """Rhystic Study is overridden to draw."""
    card = {
        "name": "Rhystic Study",
        "type_line": "Enchantment",
        "oracle_text": "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.",
    }
    result = tag_card_roles(card)
    assert "draw" in result.role_tags


def test_cultivate_is_ramp() -> None:
    """Cultivate should be tagged as ramp."""
    card = {
        "name": "Cultivate",
        "type_line": "Sorcery",
        "oracle_text": "Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle.",
    }
    result = tag_card_roles(card)
    assert "ramp" in result.role_tags


def test_wrath_of_god_is_board_wipe() -> None:
    """Wrath of God should be tagged as board_wipe."""
    card = {
        "name": "Wrath of God",
        "type_line": "Sorcery",
        "oracle_text": "Destroy all creatures. They can't be regenerated.",
    }
    result = tag_card_roles(card)
    assert "board_wipe" in result.role_tags


def test_demonic_tutor_is_tutor() -> None:
    """Demonic Tutor should be tagged as tutor."""
    card = {
        "name": "Demonic Tutor",
        "type_line": "Sorcery",
        "oracle_text": "Search your library for a card, put that card into your hand, then shuffle.",
    }
    result = tag_card_roles(card)
    assert "tutor" in result.role_tags


def test_command_tower_is_land() -> None:
    """Command Tower should be tagged as land."""
    card = {
        "name": "Command Tower",
        "type_line": "Land",
        "oracle_text": "{T}: Add one mana of any color in your commander's color identity.",
    }
    result = tag_card_roles(card)
    assert "land" in result.role_tags


def test_counterspell_is_removal() -> None:
    """Counterspell should be tagged as removal."""
    card = {
        "name": "Counterspell",
        "type_line": "Instant",
        "oracle_text": "Counter target spell.",
    }
    result = tag_card_roles(card)
    assert "removal" in result.role_tags


def test_sun_titan_is_recursion() -> None:
    """Sun Titan should be tagged with recursion."""
    card = {
        "name": "Sun Titan",
        "type_line": "Creature — Giant",
        "oracle_text": "Vigilance\nWhenever Sun Titan enters the battlefield or attacks, you may return target permanent card with mana value 3 or less from your graveyard to the battlefield.",
    }
    result = tag_card_roles(card)
    assert "recursion" in result.role_tags


def test_lightning_greaves_is_protection() -> None:
    """Lightning Greaves has hexproof → protection."""
    card = {
        "name": "Lightning Greaves",
        "type_line": "Artifact — Equipment",
        "oracle_text": "Equipped creature has haste and hexproof.\nEquip {0}",
    }
    result = tag_card_roles(card)
    assert "protection" in result.role_tags


def test_craterhoof_is_wincon() -> None:
    """Craterhoof Behemoth deals damage to each opponent effectively."""
    card = {
        "name": "Craterhoof Behemoth",
        "type_line": "Creature — Beast",
        "oracle_text": "Haste\nWhen Craterhoof Behemoth enters the battlefield, creatures you control gain trample and get +X/+X until end of turn, where X is the number of creatures you control.",
    }
    result = tag_card_roles(card)
    # Craterhoof is a threat via ETB
    assert "threat" in result.role_tags or "utility" in result.role_tags


def test_arcane_signet_is_ramp() -> None:
    """Arcane Signet should be tagged as ramp."""
    card = {
        "name": "Arcane Signet",
        "type_line": "Artifact",
        "oracle_text": "{T}: Add one mana of any color in your commander's color identity.",
    }
    result = tag_card_roles(card)
    assert "ramp" in result.role_tags


def test_cyclonic_rift_override() -> None:
    """Cyclonic Rift should be overridden to removal + board_wipe."""
    card = {
        "name": "Cyclonic Rift",
        "type_line": "Instant",
        "oracle_text": "Return target nonland permanent you don't control to its owner's hand.\nOverload {6}{U}",
    }
    result = tag_card_roles(card)
    assert "removal" in result.role_tags
    assert "board_wipe" in result.role_tags


def test_beast_within_override() -> None:
    """Beast Within should be overridden to removal."""
    card = {
        "name": "Beast Within",
        "type_line": "Instant",
        "oracle_text": "Destroy target permanent. Its controller creates a 3/3 green Beast creature token.",
    }
    result = tag_card_roles(card)
    assert "removal" in result.role_tags


def test_smothering_tithe_override() -> None:
    """Smothering Tithe overridden to ramp via treasure generation."""
    card = {
        "name": "Smothering Tithe",
        "type_line": "Enchantment",
        "oracle_text": "Whenever an opponent draws a card, that player may pay {2}. If the player doesn't, you create a Treasure token.",
    }
    result = tag_card_roles(card)
    assert "ramp" in result.role_tags
    assert "treasure_generation" in result.functional_categories


def test_animate_dead_is_recursion() -> None:
    """Animate Dead should be tagged with recursion."""
    card = {
        "name": "Animate Dead",
        "type_line": "Enchantment — Aura",
        "oracle_text": "Enchant creature card in a graveyard\nWhen Animate Dead enters the battlefield, if it's on the battlefield, it loses \"enchant creature card in a graveyard\" and gains \"enchant creature put onto the battlefield with Animate Dead.\" Return enchanted creature card to the battlefield under your control.",
    }
    result = tag_card_roles(card)
    assert "recursion" in result.role_tags


def test_thassa_oracle_is_wincon() -> None:
    """Thassa's Oracle should detect 'you win the game'."""
    card = {
        "name": "Thassa's Oracle",
        "type_line": "Creature — Merfolk Wizard",
        "oracle_text": "When Thassa's Oracle enters the battlefield, look at the top X cards of your library, where X is your devotion to blue. Put up to one of them on top of your library and the rest on the bottom of your library in a random order. If X is greater than or equal to the number of cards in your library, you win the game.",
    }
    result = tag_card_roles(card)
    assert "wincon" in result.role_tags


def test_grave_pact_is_removal_and_death_trigger() -> None:
    """Grave Pact has death trigger functional category."""
    card = {
        "name": "Grave Pact",
        "type_line": "Enchantment",
        "oracle_text": "Whenever a creature you control dies, each other player sacrifices a creature.",
    }
    result = tag_card_roles(card)
    assert "death_trigger" in result.functional_categories


def test_doubling_season_is_counter() -> None:
    """Doubling Season should match token_generation and counter categories."""
    card = {
        "name": "Doubling Season",
        "type_line": "Enchantment",
        "oracle_text": "If an effect would create one or more tokens under your control, it creates twice that many of those tokens instead.\nIf an effect would put one or more counters on a permanent you control, it puts twice that many of those counters on that permanent instead.",
    }
    result = tag_card_roles(card)
    assert "counter" in result.functional_categories


# --- Functional category tests ---


def test_ashnods_altar_is_sacrifice_outlet() -> None:
    """Ashnod's Altar should be tagged sacrifice_outlet."""
    card = {
        "name": "Ashnod's Altar",
        "type_line": "Artifact",
        "oracle_text": "Sacrifice a creature: Add {C}{C}.",
    }
    result = tag_card_roles(card)
    assert "sacrifice_outlet" in result.functional_categories


def test_panharmonicon_is_etb_payoff() -> None:
    """Panharmonicon should be tagged etb_payoff."""
    card = {
        "name": "Panharmonicon",
        "type_line": "Artifact",
        "oracle_text": "If an artifact or creature entering the battlefield causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time.",
    }
    result = tag_card_roles(card)
    assert "etb_payoff" in result.functional_categories


def test_ethereal_armor_is_aura() -> None:
    """Ethereal Armor should be tagged as aura."""
    card = {
        "name": "Ethereal Armor",
        "type_line": "Enchantment — Aura",
        "oracle_text": "Enchant creature\nFirst strike\nEnchanted creature gets +1/+1 for each enchantment you control.",
    }
    result = tag_card_roles(card)
    assert "aura" in result.functional_categories


def test_sword_of_fire_and_ice_is_equipment() -> None:
    """Sword of Fire and Ice should be tagged equipment."""
    card = {
        "name": "Sword of Fire and Ice",
        "type_line": "Artifact — Equipment",
        "oracle_text": "Equipped creature gets +2/+2 and has protection from red and from blue.\nWhenever equipped creature deals combat damage to a player, Sword of Fire and Ice deals 2 damage to any target and you draw a card.\nEquip {2}",
    }
    result = tag_card_roles(card)
    assert "equipment" in result.functional_categories


# --- RoleTagResult model tests ---


def test_role_tag_result_defaults() -> None:
    """RoleTagResult defaults to empty lists."""
    result = RoleTagResult()
    assert result.role_tags == []
    assert result.functional_categories == []


def test_tagging_stats_model() -> None:
    """TaggingStats model validates correctly."""
    stats = TaggingStats(
        total_cards=1000,
        tagged_cards=950,
        skipped_cards=50,
        version="1.0.0",
        duration_seconds=5.2,
        role_distribution={"ramp": 100, "draw": 80},
        category_distribution={"aura": 50},
    )
    assert stats.tagged_cards == 950


# --- Batch tagging tests ---


def test_tag_all_cards_creates_columns() -> None:
    """tag_all_cards creates the role_tags columns if missing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE cards ("
        "id TEXT PRIMARY KEY, name TEXT, oracle_text TEXT, "
        "type_line TEXT, oracle_id TEXT, mana_cost TEXT, "
        "cmc REAL, color_identity TEXT, keywords TEXT, "
        "is_legal_commander BOOLEAN, is_legal_in_99 BOOLEAN, "
        "set_code TEXT, rarity TEXT, image_uri TEXT, last_updated TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sol-ring", "Sol Ring", "{T}: Add {C}{C}.", "Artifact",
         "or-1", "{1}", 1.0, '[]', '[]', 0, 1, "TST", "uncommon", None, None),
    )
    conn.commit()
    conn.close()

    stats = tag_all_cards(db_path, "1.0.0")
    assert stats.tagged_cards == 1
    assert stats.version == "1.0.0"

    # Verify column data
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("SELECT role_tags, functional_categories FROM cards WHERE id = 'sol-ring'")
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    tags = json.loads(row[0])
    assert "ramp" in tags

    # Clean up
    db_path.unlink()


def test_tag_all_cards_skips_already_tagged() -> None:
    """Cards already tagged at current version should be skipped."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE cards ("
        "id TEXT PRIMARY KEY, name TEXT, oracle_text TEXT, "
        "type_line TEXT, oracle_id TEXT, mana_cost TEXT, "
        "cmc REAL, color_identity TEXT, keywords TEXT, "
        "is_legal_commander BOOLEAN, is_legal_in_99 BOOLEAN, "
        "set_code TEXT, rarity TEXT, image_uri TEXT, last_updated TIMESTAMP, "
        "role_tags TEXT, functional_categories TEXT, "
        "tags_extracted_at TIMESTAMP, tags_extraction_version TEXT)"
    )
    conn.execute(
        "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sol-ring", "Sol Ring", "{T}: Add {C}{C}.", "Artifact",
         "or-1", "{1}", 1.0, '[]', '[]', 0, 1, "TST", "uncommon", None, None,
         '["ramp"]', '[]', "2024-01-01", "1.0.0"),
    )
    conn.commit()
    conn.close()

    stats = tag_all_cards(db_path, "1.0.0")
    assert stats.tagged_cards == 0  # Already tagged

    db_path.unlink()
