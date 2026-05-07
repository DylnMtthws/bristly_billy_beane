"""Tests for infrastructure generators (6.5.4)."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.generators.ramp import RampPackageGenerator
from sabermetrics.pipeline.generators.draw import DrawPackageGenerator
from sabermetrics.pipeline.generators.removal import RemovalPackageGenerator
from sabermetrics.pipeline.generators.lands import LandPackageGenerator
from sabermetrics.pipeline.slot_assigner import SlotAssignment


def _make_template() -> DeckTemplate:
    return DeckTemplate(
        land_count=36,
        ramp_count=10,
        draw_count=8,
        removal_count=6,
        board_wipe_count=2,
        differentiator_slots=37,
        avg_cmc_target=3.0,
    )


def _make_ramp_pool() -> list[dict]:
    """Create test ramp candidates."""
    cards = [
        {"id": "sol-ring", "name": "Sol Ring", "type_line": "Artifact",
         "oracle_text": "{T}: Add {C}{C}.", "price_usd": 1.0, "cmc": 1,
         "_cvar_score": 0.9, "role_tags": '["ramp"]'},
        {"id": "arcane-signet", "name": "Arcane Signet", "type_line": "Artifact",
         "oracle_text": "{T}: Add one mana of any color in your commander's color identity.",
         "price_usd": 0.5, "cmc": 2, "_cvar_score": 0.85, "role_tags": '["ramp"]'},
        {"id": "cultivate", "name": "Cultivate", "type_line": "Sorcery",
         "oracle_text": "Search your library for up to two basic land cards, put one onto the battlefield tapped and the other into your hand.",
         "price_usd": 0.25, "cmc": 3, "_cvar_score": 0.7, "role_tags": '["ramp"]'},
    ]
    for i in range(10):
        cards.append({
            "id": f"signet-{i}", "name": f"Test Signet {i}",
            "type_line": "Artifact", "oracle_text": "{T}: Add {W} or {U}.",
            "price_usd": 0.5, "cmc": 2, "_cvar_score": 0.5 + i * 0.02,
            "role_tags": '["ramp"]',
        })
    return cards


def _make_draw_pool() -> list[dict]:
    """Create test draw candidates."""
    cards = []
    for i in range(15):
        is_repeatable = i < 5
        # Give all cards similar base CVAR so repeatable bonus matters
        cards.append({
            "id": f"draw-{i}", "name": f"Test Draw {i}",
            "type_line": "Enchantment" if is_repeatable else "Sorcery",
            "oracle_text": (
                "Whenever you cast a spell, draw a card."
                if is_repeatable else "Draw three cards."
            ),
            "price_usd": 1.0, "cmc": 3, "_cvar_score": 0.5,
            "role_tags": '["draw"]',
        })
    return cards


def _make_removal_pool() -> list[dict]:
    """Create test removal candidates."""
    cards = []
    targets = ["creature", "artifact", "enchantment", "permanent"]
    for i in range(12):
        target = targets[i % len(targets)]
        is_wipe = i < 3
        cards.append({
            "id": f"removal-{i}", "name": f"Test Removal {i}",
            "type_line": "Instant" if not is_wipe else "Sorcery",
            "oracle_text": (
                f"Destroy all creatures." if is_wipe
                else f"Destroy target {target}."
            ),
            "price_usd": 1.0, "cmc": 3, "_cvar_score": 0.5 + i * 0.02,
            "role_tags": '["board_wipe"]' if is_wipe else '["removal"]',
        })
    return cards


def _make_land_pool() -> list[dict]:
    """Create test land candidates."""
    cards = []
    for i in range(20):
        cards.append({
            "id": f"land-{i}", "name": f"Test Land {i}",
            "type_line": "Land",
            "oracle_text": "{T}: Add {W} or {U}.",
            "price_usd": 1.0, "cmc": 0, "_cvar_score": 0.5,
            "role_tags": '["land"]',
        })
    # Add Command Tower
    cards.append({
        "id": "command-tower", "name": "Command Tower",
        "type_line": "Land",
        "oracle_text": "{T}: Add one mana of any color in your commander's color identity.",
        "price_usd": 0.25, "cmc": 0, "_cvar_score": 0.8,
        "role_tags": '["land"]',
    })
    return cards


# --- Ramp Generator Tests ---


def test_ramp_generator_produces_assignments() -> None:
    """Ramp generator returns SlotAssignment list."""
    gen = RampPackageGenerator(Path("data/sabermetrics.db"))
    template = _make_template()
    result = gen.generate(
        color_identity=["W", "U"],
        target_count=10,
        budget_remaining=200.0,
        template=template,
        already_placed=[],
        role_tag_pool=_make_ramp_pool(),
    )
    assert len(result) > 0
    assert all(isinstance(a, SlotAssignment) for a in result)
    assert all(a.slot_role == "ramp" for a in result)


def test_ramp_generator_includes_sol_ring() -> None:
    """Sol Ring should always be auto-included."""
    gen = RampPackageGenerator(Path("data/sabermetrics.db"))
    result = gen.generate(
        color_identity=["W", "U"],
        target_count=10,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=_make_ramp_pool(),
    )
    names = [a.card["name"] for a in result]
    assert "Sol Ring" in names


def test_ramp_generator_respects_budget() -> None:
    """Ramp generator should not exceed budget."""
    gen = RampPackageGenerator(Path("data/sabermetrics.db"))
    result = gen.generate(
        color_identity=["W", "U"],
        target_count=10,
        budget_remaining=3.0,  # Very tight budget
        template=_make_template(),
        already_placed=[],
        role_tag_pool=_make_ramp_pool(),
    )
    total_price = sum(float(a.card.get("price_usd", 0) or 0) for a in result)
    assert total_price <= 3.0


def test_ramp_generator_no_duplicates() -> None:
    """No duplicate card names in ramp output."""
    gen = RampPackageGenerator(Path("data/sabermetrics.db"))
    result = gen.generate(
        color_identity=["W", "U"],
        target_count=10,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=_make_ramp_pool(),
    )
    names = [a.card["name"] for a in result]
    assert len(names) == len(set(names))


# --- Draw Generator Tests ---


def test_draw_generator_produces_assignments() -> None:
    """Draw generator returns valid assignments."""
    gen = DrawPackageGenerator(Path("data/sabermetrics.db"))
    result = gen.generate(
        color_identity=["U"],
        target_count=8,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=_make_draw_pool(),
    )
    assert len(result) > 0
    assert all(a.slot_role == "draw" for a in result)


def test_draw_generator_prefers_repeatable() -> None:
    """Repeatable draw should score higher than one-shot."""
    gen = DrawPackageGenerator(Path("data/sabermetrics.db"))
    result = gen.generate(
        color_identity=["U"],
        target_count=3,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=_make_draw_pool(),
    )
    # Most selected should be repeatable (enchantments with "whenever")
    repeatable = [
        a for a in result
        if "enchantment" in (a.card.get("type_line") or "").lower()
    ]
    assert len(repeatable) >= 1


# --- Removal Generator Tests ---


def test_removal_generator_produces_assignments() -> None:
    """Removal generator returns valid assignments."""
    gen = RemovalPackageGenerator(Path("data/sabermetrics.db"))
    result = gen.generate(
        color_identity=["B", "R"],
        target_count=6,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=_make_removal_pool(),
        board_wipe_target=2,
    )
    assert len(result) > 0
    assert all(a.slot_role == "removal" for a in result)


def test_removal_generator_includes_board_wipes() -> None:
    """Removal package should include board wipes."""
    gen = RemovalPackageGenerator(Path("data/sabermetrics.db"))
    result = gen.generate(
        color_identity=["B", "R"],
        target_count=6,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=_make_removal_pool(),
        board_wipe_target=2,
    )
    wipes = [
        a for a in result
        if "all" in (a.card.get("oracle_text") or "").lower()
    ]
    assert len(wipes) >= 1


# --- Land Generator Tests ---


def test_land_generator_produces_assignments() -> None:
    """Land generator returns valid assignments."""
    gen = LandPackageGenerator(Path("data/sabermetrics.db"))
    result = gen.generate(
        color_identity=["W", "U"],
        target_count=36,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[{"mana_cost": "{1}{W}{U}", "cmc": 3, "type_line": "Creature"}],
        role_tag_pool=_make_land_pool(),
    )
    assert len(result) > 0
    assert all(a.slot_role == "land" for a in result)


def test_land_generator_auto_includes_command_tower() -> None:
    """Command Tower should be auto-included for multicolor."""
    gen = LandPackageGenerator(Path("data/sabermetrics.db"))
    result = gen.generate(
        color_identity=["W", "U"],
        target_count=36,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[{"mana_cost": "{1}{W}{U}", "cmc": 3, "type_line": "Creature"}],
        role_tag_pool=_make_land_pool(),
    )
    names = [a.card["name"] for a in result]
    assert "Command Tower" in names
