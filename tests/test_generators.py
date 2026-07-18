"""Tests for infrastructure generators (6.5.4)."""

from pathlib import Path


from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.generators.ramp import (
    RampPackageGenerator,
    _score_ramp,
)
from sabermetrics.pipeline.generators.draw import DrawPackageGenerator
from sabermetrics.pipeline.generators.removal import (
    RemovalPackageGenerator,
    _score_removal,
)
from sabermetrics.pipeline.generators.protection import (
    ProtectionPackageGenerator,
    _score_protection,
)
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
                "Destroy all creatures." if is_wipe
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


def _make_protection_pool() -> list[dict]:
    """Create test protection candidates."""
    return [
        {
            "id": "tef-prot", "name": "Teferi's Protection",
            "type_line": "Instant",
            "oracle_text": "Until your next turn, your life total can't change and you gain protection from everything. All permanents you control phase out.",
            "price_usd": 5.0, "cmc": 3, "_cvar_score": 0.9,
            "role_tags": '["protection"]',
        },
        {
            "id": "heroic-int", "name": "Heroic Intervention",
            "type_line": "Instant",
            "oracle_text": "Permanents you control gain hexproof and indestructible until end of turn.",
            "price_usd": 3.0, "cmc": 2, "_cvar_score": 0.8,
            "role_tags": '["protection"]',
        },
        {
            "id": "swiftfoot", "name": "Swiftfoot Boots",
            "type_line": "Artifact — Equipment",
            "oracle_text": "Equipped creature has hexproof and haste. Equip {1}.",
            "price_usd": 0.5, "cmc": 2, "_cvar_score": 0.6,
            "role_tags": '["protection"]',
        },
        {
            "id": "shalai", "name": "Shalai, Voice of Plenty",
            "type_line": "Legendary Creature — Angel",
            "oracle_text": "Flying. You, planeswalkers you control, and other creatures you control have hexproof.",
            "price_usd": 1.0, "cmc": 4, "_cvar_score": 0.5,
            "role_tags": '["protection"]',
        },
        {
            "id": "defl-swat", "name": "Deflecting Swat",
            "type_line": "Instant",
            "oracle_text": "If you control a commander, you may cast this spell without paying its mana cost. You may choose new targets for target spell or ability.",
            "price_usd": 8.0, "cmc": 3, "_cvar_score": 0.85,
            "role_tags": '["protection"]',
        },
        {
            "id": "boros-charm", "name": "Boros Charm",
            "type_line": "Instant",
            "oracle_text": "Choose one — Boros Charm deals 4 damage to target player or planeswalker; or permanents you control gain indestructible until end of turn; or target creature gains double strike until end of turn.",
            "price_usd": 0.5, "cmc": 2, "_cvar_score": 0.65,
            "role_tags": '["protection"]',
        },
        {
            "id": "shelter", "name": "Shelter",
            "type_line": "Instant",
            "oracle_text": "Target creature you control gains protection from the color of your choice until end of turn. Draw a card.",
            "price_usd": 0.10, "cmc": 2, "_cvar_score": 0.3,
            "role_tags": '["protection"]',
        },
        {
            "id": "unbreakable", "name": "Unbreakable Formation",
            "type_line": "Instant",
            "oracle_text": "Creatures you control gain indestructible until end of turn.",
            "price_usd": 0.25, "cmc": 3, "_cvar_score": 0.4,
            "role_tags": '["protection"]',
        },
    ]


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


# --- Ramp Scoring Tests ---


def test_score_ramp_adds_empirical_bonus() -> None:
    """A card common in the target variant scores above an identical card that
    is absent from the corpus, and absence is never a penalty."""
    base = {
        "oracle_text": "{T}: Add {G}.",
        "cmc": 2, "type_line": "Artifact", "_cvar_score": 0.5,
    }
    grounded = base | {"_empirical_inclusion": 0.65, "_empirical_reliable": True}

    score_base = _score_ramp(base, ["G"], 3.0)
    score_grounded = _score_ramp(grounded, ["G"], 3.0)

    # No corpus data -> identical to the pre-grounding score (absence neutral).
    assert _score_ramp(base | {"_empirical_inclusion": 0.0}, ["G"], 3.0) == score_base
    assert score_grounded > score_base


def test_score_ramp_penalizes_conditional() -> None:
    """Conditional mana (discard/sacrifice) scores much lower than unconditional."""
    unconditional = {
        "oracle_text": "{T}: Add {G}.",
        "cmc": 2, "type_line": "Artifact", "_cvar_score": 0.5,
    }
    conditional = {
        "oracle_text": "Sacrifice a creature: Add {G}.",
        "cmc": 2, "type_line": "Artifact", "_cvar_score": 0.5,
    }
    score_good = _score_ramp(unconditional, ["G"], 3.0)
    score_bad = _score_ramp(conditional, ["G"], 3.0)
    assert score_good > score_bad, (
        f"Unconditional ({score_good:.3f}) should beat conditional ({score_bad:.3f})"
    )


def test_score_ramp_penalizes_restricted() -> None:
    """'Spend this mana only on...' heavily penalized."""
    normal = {
        "oracle_text": "{T}: Add {C}{C}.",
        "cmc": 3, "type_line": "Artifact", "_cvar_score": 0.5,
    }
    restricted = {
        "oracle_text": "{T}: Add {C}{C}. Spend this mana only to cast artifact spells.",
        "cmc": 3, "type_line": "Artifact", "_cvar_score": 0.5,
    }
    score_normal = _score_ramp(normal, ["W", "U"], 3.0)
    score_restricted = _score_ramp(restricted, ["W", "U"], 3.0)
    assert score_normal > score_restricted, (
        f"Normal ({score_normal:.3f}) should beat restricted ({score_restricted:.3f})"
    )


def test_score_ramp_prefers_low_cmc() -> None:
    """2-mana ramp > 3-mana ramp for same output."""
    cheap = {
        "oracle_text": "{T}: Add {G}.",
        "cmc": 2, "type_line": "Artifact", "_cvar_score": 0.5,
    }
    expensive = {
        "oracle_text": "{T}: Add {G}.",
        "cmc": 3, "type_line": "Artifact", "_cvar_score": 0.5,
    }
    score_cheap = _score_ramp(cheap, ["G"], 3.0)
    score_expensive = _score_ramp(expensive, ["G"], 3.0)
    assert score_cheap > score_expensive, (
        f"Cheap ({score_cheap:.3f}) should beat expensive ({score_expensive:.3f})"
    )


def test_score_ramp_land_ramp_resilience_bonus() -> None:
    """Land ramp gets durability bonus over artifact ramp."""
    land_ramp = {
        "oracle_text": "Search your library for a basic land card, put it onto the battlefield tapped.",
        "cmc": 2, "type_line": "Sorcery", "_cvar_score": 0.5,
    }
    rock = {
        "oracle_text": "{T}: Add {G}.",
        "cmc": 2, "type_line": "Artifact", "_cvar_score": 0.5,
    }
    score_land = _score_ramp(land_ramp, ["G"], 3.0)
    score_rock = _score_ramp(rock, ["G"], 3.0)
    assert score_land > score_rock, (
        f"Land ramp ({score_land:.3f}) should beat rock ({score_rock:.3f})"
    )


def test_score_ramp_powerstone_prodigy_scores_low() -> None:
    """Powerstone Prodigy: conditional + restricted = near-zero role score."""
    prodigy = {
        "oracle_text": "Discard a card: Create a Powerstone token. Spend this mana only to cast nonartifact spells.",
        "cmc": 3, "type_line": "Creature — Human Wizard", "_cvar_score": 0.6,
    }
    sol_ring = {
        "oracle_text": "{T}: Add {C}{C}.",
        "cmc": 1, "type_line": "Artifact", "_cvar_score": 0.9,
    }
    score_prodigy = _score_ramp(prodigy, ["W", "U", "G"], 3.0)
    score_sol = _score_ramp(sol_ring, ["W", "U", "G"], 3.0)
    assert score_sol > score_prodigy * 1.5, (
        f"Sol Ring ({score_sol:.3f}) should vastly beat Prodigy ({score_prodigy:.3f})"
    )


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
    # Robust to both selection paths: the candidate-table path tags wipes with
    # removal_type; the role-tag fallback carries a board_wipe role tag. Don't
    # match an oracle substring -- a real wipe like Blasphemous Act reads "each
    # creature", never "all".
    def _is_wipe(card: dict) -> bool:
        return (
            card.get("removal_type") == "board_wipe"
            or "board_wipe" in (card.get("role_tags") or "")
        )

    wipes = [a for a in result if _is_wipe(a.card)]
    assert len(wipes) >= 1


# --- Removal Scoring Tests ---


def test_score_removal_flexibility() -> None:
    """'Any permanent' scores much higher than 'creature only'."""
    flexible = {
        "oracle_text": "Exile target permanent.",
        "cmc": 3, "type_line": "Instant", "_cvar_score": 0.5,
    }
    narrow = {
        "oracle_text": "Destroy target creature.",
        "cmc": 3, "type_line": "Instant", "_cvar_score": 0.5,
    }
    score_flex = _score_removal(flexible, ["W"], 3.0)
    score_narrow = _score_removal(narrow, ["W"], 3.0)
    assert score_flex > score_narrow, (
        f"Flexible ({score_flex:.3f}) should beat narrow ({score_narrow:.3f})"
    )


def test_score_removal_instant_over_sorcery() -> None:
    """Instant speed removal scores higher than sorcery speed."""
    instant = {
        "oracle_text": "Destroy target creature.",
        "cmc": 2, "type_line": "Instant", "_cvar_score": 0.5,
    }
    sorcery = {
        "oracle_text": "Destroy target creature.",
        "cmc": 2, "type_line": "Sorcery", "_cvar_score": 0.5,
    }
    score_instant = _score_removal(instant, ["B"], 3.0)
    score_sorcery = _score_removal(sorcery, ["B"], 3.0)
    assert score_instant > score_sorcery, (
        f"Instant ({score_instant:.3f}) should beat sorcery ({score_sorcery:.3f})"
    )


def test_score_removal_exile_over_destroy() -> None:
    """Exile effects score higher than destroy effects."""
    exile = {
        "oracle_text": "Exile target creature.",
        "cmc": 3, "type_line": "Instant", "_cvar_score": 0.5,
    }
    destroy = {
        "oracle_text": "Destroy target creature.",
        "cmc": 3, "type_line": "Instant", "_cvar_score": 0.5,
    }
    score_exile = _score_removal(exile, ["W"], 3.0)
    score_destroy = _score_removal(destroy, ["W"], 3.0)
    assert score_exile > score_destroy, (
        f"Exile ({score_exile:.3f}) should beat destroy ({score_destroy:.3f})"
    )


def test_score_removal_free_cast_bonus() -> None:
    """Free-cast spells (Deadly Rollick pattern) get large bonus."""
    free = {
        "oracle_text": "If you control a commander, you may cast this spell without paying its mana cost. Exile target creature.",
        "cmc": 4, "type_line": "Instant", "_cvar_score": 0.7,
    }
    paid = {
        "oracle_text": "Exile target creature.",
        "cmc": 2, "type_line": "Instant", "_cvar_score": 0.7,
    }
    score_free = _score_removal(free, ["B"], 3.0)
    score_paid = _score_removal(paid, ["B"], 3.0)
    assert score_free > score_paid, (
        f"Free ({score_free:.3f}) should beat paid ({score_paid:.3f})"
    )


# --- Protection Scoring Tests ---


def test_score_protection_phasing_best() -> None:
    """Phasing scores highest among protection types."""
    phasing = {
        "oracle_text": "All permanents you control phase out.",
        "cmc": 3, "type_line": "Instant", "_cvar_score": 0.5,
    }
    hexproof = {
        "oracle_text": "Target creature gains hexproof until end of turn.",
        "cmc": 1, "type_line": "Instant", "_cvar_score": 0.5,
    }
    score_phasing = _score_protection(phasing, ["W"], 3.0)
    score_hexproof = _score_protection(hexproof, ["W"], 3.0)
    assert score_phasing > score_hexproof, (
        f"Phasing ({score_phasing:.3f}) should beat hexproof ({score_hexproof:.3f})"
    )


def test_score_protection_instant_required() -> None:
    """Sorcery-speed protection is actively penalized."""
    instant = {
        "oracle_text": "Target creature gains indestructible until end of turn.",
        "cmc": 2, "type_line": "Instant", "_cvar_score": 0.5,
    }
    sorcery = {
        "oracle_text": "Target creature gains indestructible until end of turn.",
        "cmc": 2, "type_line": "Sorcery", "_cvar_score": 0.5,
    }
    score_instant = _score_protection(instant, ["W"], 3.0)
    score_sorcery = _score_protection(sorcery, ["W"], 3.0)
    assert score_instant > score_sorcery, (
        f"Instant ({score_instant:.3f}) should beat sorcery ({score_sorcery:.3f})"
    )


def test_score_protection_free_cast_premium() -> None:
    """Free-cast protection spells score highest."""
    free = {
        "oracle_text": "If you control a commander, you may cast this spell without paying its mana cost. You may choose new targets for target spell or ability.",
        "cmc": 3, "type_line": "Instant", "_cvar_score": 0.7,
    }
    paid = {
        "oracle_text": "You may choose new targets for target spell or ability.",
        "cmc": 3, "type_line": "Instant", "_cvar_score": 0.7,
    }
    score_free = _score_protection(free, ["R"], 3.0)
    score_paid = _score_protection(paid, ["R"], 3.0)
    assert score_free > score_paid, (
        f"Free ({score_free:.3f}) should beat paid ({score_paid:.3f})"
    )


def test_score_protection_board_wide_bonus() -> None:
    """Board-wide protection scores higher than single-target."""
    board = {
        "oracle_text": "Permanents you control gain hexproof and indestructible until end of turn.",
        "cmc": 2, "type_line": "Instant", "_cvar_score": 0.5,
    }
    single = {
        "oracle_text": "Target creature gains hexproof and indestructible until end of turn.",
        "cmc": 2, "type_line": "Instant", "_cvar_score": 0.5,
    }
    score_board = _score_protection(board, ["G"], 3.0)
    score_single = _score_protection(single, ["G"], 3.0)
    assert score_board > score_single, (
        f"Board ({score_board:.3f}) should beat single ({score_single:.3f})"
    )


# --- Protection Generator Tests ---


def test_protection_generator_fills_slots() -> None:
    """Protection generator produces target count of cards."""
    gen = ProtectionPackageGenerator(Path("data/sabermetrics.db"))
    result = gen.generate(
        color_identity=["W", "G"],
        target_count=4,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=_make_protection_pool(),
    )
    assert len(result) > 0
    assert len(result) <= 4
    assert all(isinstance(a, SlotAssignment) for a in result)
    assert all(a.slot_role == "protection" for a in result)


def test_protection_generator_no_duplicates() -> None:
    """No duplicate card names in protection output."""
    gen = ProtectionPackageGenerator(Path("data/sabermetrics.db"))
    result = gen.generate(
        color_identity=["W", "G"],
        target_count=4,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=_make_protection_pool(),
    )
    names = [a.card["name"] for a in result]
    assert len(names) == len(set(names))


def test_protection_generator_respects_budget() -> None:
    """Protection generator should not exceed budget."""
    gen = ProtectionPackageGenerator(Path("data/sabermetrics.db"))
    result = gen.generate(
        color_identity=["W", "G"],
        target_count=4,
        budget_remaining=2.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=_make_protection_pool(),
    )
    total_price = sum(float(a.card.get("price_usd", 0) or 0) for a in result)
    assert total_price <= 2.0


def test_protection_generator_excludes_already_placed() -> None:
    """Cards already in deck should not be placed again."""
    gen = ProtectionPackageGenerator(Path("data/sabermetrics.db"))
    pool = _make_protection_pool()
    already = [{"name": "Teferi's Protection"}, {"name": "Heroic Intervention"}]
    result = gen.generate(
        color_identity=["W", "G"],
        target_count=4,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=already,
        role_tag_pool=pool,
    )
    names = [a.card["name"] for a in result]
    assert "Teferi's Protection" not in names
    assert "Heroic Intervention" not in names


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


# --- Ramp Generator: ramp_candidates table preference ---


def test_ramp_generator_exposes_protected_names() -> None:
    """After generate(), protected_names should contain staple cards."""
    gen = RampPackageGenerator(Path("data/sabermetrics.db"))
    gen.generate(
        color_identity=["W", "U"],
        target_count=10,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=_make_ramp_pool(),
    )
    # Sol Ring and Arcane Signet should be protected (from auto_include_cards.yaml)
    assert "Sol Ring" in gen.protected_names
    assert "Arcane Signet" in gen.protected_names


def test_ramp_generator_includes_green_staples_when_green() -> None:
    """Green ramp staples (Cultivate, etc.) should be auto-included for green decks."""
    pool = _make_ramp_pool()
    # Add green staples to pool
    pool.extend([
        {"id": "natures-lore", "name": "Nature's Lore", "type_line": "Sorcery",
         "oracle_text": "Search your library for a Forest card, put it onto the battlefield, then shuffle.",
         "price_usd": 0.5, "cmc": 2, "_cvar_score": 0.7, "role_tags": '["ramp"]'},
        {"id": "kodamas-reach", "name": "Kodama's Reach", "type_line": "Sorcery — Arcane",
         "oracle_text": "Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle.",
         "price_usd": 0.25, "cmc": 3, "_cvar_score": 0.65, "role_tags": '["ramp"]'},
    ])
    gen = RampPackageGenerator(Path("data/sabermetrics.db"))
    result = gen.generate(
        color_identity=["W", "U", "G"],
        target_count=12,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=pool,
    )
    names = [a.card["name"] for a in result]
    # At least some of the green staples should be auto-included
    green_staples = {"Nature's Lore", "Cultivate", "Kodama's Reach"}
    included_green = green_staples & set(names)
    assert len(included_green) >= 1, f"Expected green staples but got: {names}"


# --- Removal Generator: auto-includes and protected_names ---


def _make_removal_pool_with_staples() -> list[dict]:
    """Create removal pool including known staples."""
    return [
        {"id": "stp", "name": "Swords to Plowshares", "type_line": "Instant",
         "oracle_text": "Exile target creature. Its controller gains life equal to its power.",
         "price_usd": 1.0, "cmc": 1, "_cvar_score": 0.9, "role_tags": '["removal"]',
         "color_identity": '["W"]'},
        {"id": "beast", "name": "Beast Within", "type_line": "Instant",
         "oracle_text": "Destroy target permanent. Its controller creates a 3/3 green Beast creature token.",
         "price_usd": 0.5, "cmc": 3, "_cvar_score": 0.8, "role_tags": '["removal"]',
         "color_identity": '["G"]'},
        {"id": "counter", "name": "Counterspell", "type_line": "Instant",
         "oracle_text": "Counter target spell.",
         "price_usd": 0.5, "cmc": 2, "_cvar_score": 0.85, "role_tags": '["removal"]',
         "color_identity": '["U"]'},
    ] + _make_removal_pool()


def test_removal_generator_exposes_protected_names() -> None:
    """After generate(), protected_names should contain staple cards."""
    gen = RemovalPackageGenerator(Path("data/sabermetrics.db"))
    pool = _make_removal_pool_with_staples()
    gen.generate(
        color_identity=["W", "U", "G"],
        target_count=6,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=pool,
        board_wipe_target=2,
    )
    # Swords, Beast Within, and Counterspell should be protected (from auto_include_cards.yaml)
    assert "Swords to Plowshares" in gen.protected_names
    assert "Beast Within" in gen.protected_names
    assert "Counterspell" in gen.protected_names


def test_removal_auto_includes_swords_for_white() -> None:
    """White deck gets Swords to Plowshares auto-included."""
    gen = RemovalPackageGenerator(Path("data/sabermetrics.db"))
    pool = _make_removal_pool_with_staples()
    result = gen.generate(
        color_identity=["W", "U"],
        target_count=6,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=pool,
        board_wipe_target=2,
    )
    names = [a.card["name"] for a in result]
    assert "Swords to Plowshares" in names


# --- Protection Generator: auto-includes and protected_names ---


def test_protection_generator_exposes_protected_names() -> None:
    """After generate(), protected_names should contain Lightning Greaves."""
    gen = ProtectionPackageGenerator(Path("data/sabermetrics.db"))
    pool = _make_protection_pool()
    # Add Lightning Greaves to pool
    pool.append({
        "id": "greaves", "name": "Lightning Greaves",
        "type_line": "Artifact — Equipment",
        "oracle_text": "Equipped creature has shroud and haste.\nEquip {0}",
        "price_usd": 1.0, "cmc": 2, "_cvar_score": 0.7,
        "role_tags": '["protection"]',
    })
    gen.generate(
        color_identity=["W", "G"],
        target_count=4,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=pool,
    )
    assert "Lightning Greaves" in gen.protected_names


def test_protection_auto_includes_boots() -> None:
    """All decks get Swiftfoot Boots auto-included."""
    gen = ProtectionPackageGenerator(Path("data/sabermetrics.db"))
    pool = _make_protection_pool()
    result = gen.generate(
        color_identity=["W", "G"],
        target_count=4,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=pool,
    )
    names = [a.card["name"] for a in result]
    assert "Swiftfoot Boots" in names


# --- Land-count invariant and type-need (SME review follow-ups) ---


def test_ramp_generator_never_places_lands() -> None:
    """Land-typed 'ramp' (Krosan Verge) belongs to the land package.

    Two such placements caused the deck to build 38 lands against a 36-land
    template target.
    """
    pool = _make_ramp_pool() + [{
        "id": "kv", "name": "Krosan Verge",
        "type_line": "Land",
        "oracle_text": "{2}, {T}, Sacrifice Krosan Verge: Search your library "
                       "for a Forest card and a Plains card.",
        "price_usd": 0.25, "cmc": 0, "_cvar_score": 0.99,
        "role_tags": '["ramp"]',
    }]
    gen = RampPackageGenerator(Path("/nonexistent.db"))  # forces role_tag_pool
    result = gen.generate(
        color_identity=["G", "W"],
        target_count=10,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=pool,
        commander_colors=["G", "W"],
        avg_cmc=3.0,
    )
    assert all(
        "land" not in (a.card.get("type_line") or "").lower() for a in result
    )


def test_land_generator_subtracts_already_placed_lands() -> None:
    """Total lands equal the target even if another stage placed lands."""
    gen = LandPackageGenerator(Path("data/sabermetrics.db"))
    placed_lands = [
        {"name": f"Stray Land {i}", "type_line": "Land", "cmc": 0}
        for i in range(2)
    ]
    result = gen.generate(
        color_identity=["W", "U"],
        target_count=36,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=placed_lands
        + [{"mana_cost": "{1}{W}{U}", "cmc": 3, "type_line": "Creature"}],
        role_tag_pool=_make_land_pool(),
    )
    assert len(result) <= 34   # 36 target - 2 already placed


def test_draw_generator_type_need_prefers_on_type() -> None:
    """With enchantments under target, enchantment draw beats an equal
    creature draw; without targets, ranking is unchanged."""
    ench = {
        "id": "e", "name": "Enchant Draw", "type_line": "Enchantment",
        "oracle_text": "Whenever you cast an enchantment spell, draw a card.",
        "price_usd": 1.0, "cmc": 2, "_cvar_score": 0.50, "role_tags": '["draw"]',
    }
    creature = {
        "id": "c", "name": "Creature Draw", "type_line": "Creature",
        "oracle_text": "Whenever you attack, draw a card.",
        "price_usd": 1.0, "cmc": 2, "_cvar_score": 0.55, "role_tags": '["draw"]',
    }

    gen = DrawPackageGenerator(Path("data/sabermetrics.db"))
    template = _make_template()
    template = template.model_copy(update={"type_targets": {"enchantment": 30}})
    result = gen.generate(
        color_identity=["W"],
        target_count=1,
        budget_remaining=200.0,
        template=template,
        already_placed=[],
        role_tag_pool=[ench, creature],
    )
    assert result[0].card["name"] == "Enchant Draw"

    # Without targets, the higher-CVAR creature wins as before.
    plain = gen.generate(
        color_identity=["W"],
        target_count=1,
        budget_remaining=200.0,
        template=_make_template(),
        already_placed=[],
        role_tag_pool=[ench, creature],
    )
    assert plain[0].card["name"] == "Creature Draw"


def test_unmet_type_targets_counts_and_empties() -> None:
    template = _make_template().model_copy(
        update={"type_targets": {"enchantment": 2, "creature": 1}}
    )
    placed = [
        {"type_line": "Enchantment — Aura"},
        {"type_line": "Creature — Human"},
        {"type_line": "Land"},
    ]
    assert template.unmet_type_targets(placed) == {"enchantment"}
    assert _make_template().unmet_type_targets(placed) == set()
