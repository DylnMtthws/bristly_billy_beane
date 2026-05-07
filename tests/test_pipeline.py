"""Tests for Phase 6 Pipeline Integration (D6.1-D6.4).

Tests slot assigner, formatters, deck builder request validation,
and CLI wiring.
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from sabermetrics.pipeline.slot_assigner import (
    AssemblyResult,
    SlotAssignment,
    _classify_card_role,
    fill_slots,
    get_target_composition,
)
from sabermetrics.pipeline.mana_base import (
    LandInfo,
    build_mana_base,
    compute_color_targets,
    count_color_pips,
    load_karsten_config,
    parse_land_colors,
    target_land_count,
)
from sabermetrics.pipeline.formatters import (
    format_archidekt,
    format_deck,
    format_moxfield,
    format_text,
)
from sabermetrics.pipeline.deck_builder import DeckBuildRequest

DB_PATH = Path("data/sabermetrics.db")
HAS_DB = DB_PATH.exists()
HAS_API_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))


# --- Slot Assigner Tests ---


def test_target_composition_sums_to_99() -> None:
    """All power-level compositions must sum to exactly 99."""
    for power in range(1, 6):
        comp = get_target_composition(power)
        total = sum(comp.values())
        assert total == 99, f"Power {power} sums to {total}, not 99"


def test_target_composition_strategy_adjustment() -> None:
    """Strategy adjustments should preserve 99-card total."""
    for strategy in ["aggro", "control", "combo", "stax"]:
        comp = get_target_composition(3, strategy=strategy)
        total = sum(comp.values())
        assert total == 99, f"Strategy '{strategy}' sums to {total}"


def test_classify_card_role_land() -> None:
    """Lands should be classified as 'land'."""
    card = {"type_line": "Land", "oracle_text": ""}
    assert _classify_card_role(card) == "land"


def test_classify_card_role_ramp() -> None:
    """Mana-producing cards should be classified as 'ramp'."""
    card = {
        "type_line": "Artifact",
        "oracle_text": "Add {G} to your mana pool.",
    }
    assert _classify_card_role(card) == "ramp"


def test_classify_card_role_draw() -> None:
    """Card draw should be classified as 'draw'."""
    card = {
        "type_line": "Sorcery",
        "oracle_text": "Draw three cards.",
    }
    assert _classify_card_role(card) == "draw"


def test_classify_card_role_removal() -> None:
    """Removal should be classified correctly."""
    card = {
        "type_line": "Instant",
        "oracle_text": "Destroy target creature.",
    }
    assert _classify_card_role(card) == "removal"


def test_classify_card_role_wincon() -> None:
    """Win conditions should be identified."""
    card = {
        "type_line": "Sorcery",
        "oracle_text": "You win the game.",
    }
    assert _classify_card_role(card) == "wincon"


def test_classify_card_role_llm_override() -> None:
    """LLM-provided role should override heuristic."""
    card = {"type_line": "Creature", "oracle_text": "Flying"}
    assert _classify_card_role(card, llm_role="wincon") == "wincon"


def test_fill_slots_basic() -> None:
    """fill_slots produces assignments up to available candidates."""
    # Create simple scored candidates
    candidates = []
    for i in range(120):
        is_land = i < 40
        card = {
            "id": f"card-{i}",
            "name": f"Test Card {i}",
            "type_line": "Land" if is_land else "Creature",
            "oracle_text": "{T}: Add {W} or {U}." if is_land else "",
            "mana_cost": "" if is_land else "{1}{W}",
            "price_usd": 1.0,
            "cmc": 0 if is_land else 3,
        }
        scoring = {
            "cvar_score": 0.5 + (i % 10) * 0.05,
            "llm_fit_score": 6,
            "slot_role": "land" if is_land else "utility",
        }
        candidates.append((card, scoring))

    target = get_target_composition(3)
    result = fill_slots(
        scored_candidates=candidates,
        target_composition=target,
        max_budget=200.0,
        commander_colors=["W", "U"],
    )

    assert isinstance(result, AssemblyResult)
    assert len(result.assignments) == 99
    assert result.total_price <= 200.0


def test_fill_slots_budget_enforcement() -> None:
    """Budget constraint should be respected."""
    candidates = []
    for i in range(120):
        card = {
            "id": f"card-{i}",
            "name": f"Expensive Card {i}",
            "type_line": "Land" if i < 40 else "Creature",
            "oracle_text": "",
            "price_usd": 5.0,  # $5 each -> 99 cards = $495
            "cmc": 3,
        }
        scoring = {
            "cvar_score": 0.5,
            "llm_fit_score": 6,
            "slot_role": "land" if i < 40 else "utility",
        }
        candidates.append((card, scoring))

    target = get_target_composition(3)
    result = fill_slots(
        scored_candidates=candidates,
        target_composition=target,
        max_budget=100.0,
    )

    # Should respect budget (at $5/card, max 20 cards)
    assert result.total_price <= 100.0


def test_fill_slots_singleton() -> None:
    """No duplicate card names should appear."""
    candidates = []
    for i in range(120):
        card = {
            "id": f"card-{i}",
            "name": f"Unique Card {i % 60}",  # Duplicate names
            "type_line": "Creature",
            "oracle_text": "",
            "price_usd": 1.0,
            "cmc": 3,
        }
        scoring = {
            "cvar_score": 0.5,
            "llm_fit_score": 6,
            "slot_role": "utility",
        }
        candidates.append((card, scoring))

    target = get_target_composition(3)
    result = fill_slots(
        scored_candidates=candidates,
        target_composition=target,
    )

    names = [a.card["name"] for a in result.assignments]
    assert len(names) == len(set(names)), "Duplicate card names found"


# --- Mana Base Tests ---


def test_parse_land_dual() -> None:
    """Dual land oracle text produces both colors."""
    info = parse_land_colors(
        oracle_text="{T}: Add {W} or {B}.",
        type_line="Land",
    )
    assert sorted(info.colors_produced) == ["B", "W"]
    assert not info.enters_tapped
    assert not info.is_fetch


def test_parse_land_fetch() -> None:
    """Fetch land is detected with correct targets."""
    info = parse_land_colors(
        oracle_text=(
            "{T}, Pay 1 life, Sacrifice this: "
            "Search your library for a Plains or Island card, "
            "put it onto the battlefield, then shuffle."
        ),
        type_line="Land",
        commander_colors=["W", "U"],
    )
    assert info.is_fetch
    assert "W" in info.fetch_targets
    assert "U" in info.fetch_targets


def test_parse_land_etb_tapped() -> None:
    """ETB tapped lands are detected."""
    info = parse_land_colors(
        oracle_text=(
            "Swiftwater Cliffs enters the battlefield tapped.\n"
            "{T}: Add {U} or {R}."
        ),
        type_line="Land",
    )
    assert info.enters_tapped
    assert sorted(info.colors_produced) == ["R", "U"]


def test_parse_land_any_color() -> None:
    """'Any color' lands produce all commander colors."""
    info = parse_land_colors(
        oracle_text="{T}: Add one mana of any color.",
        type_line="Land",
        commander_colors=["W", "U", "G"],
    )
    assert info.produces_any_color
    assert "W" in info.colors_produced
    assert "U" in info.colors_produced
    assert "G" in info.colors_produced


def test_parse_land_basic_types() -> None:
    """Basic land types in type line produce correct colors."""
    info = parse_land_colors(
        oracle_text="({T}: Add {W} or {U}.)",
        type_line="Land — Plains Island",
    )
    assert "W" in info.colors_produced
    assert "U" in info.colors_produced


def test_count_color_pips() -> None:
    """Pip counting from mana costs."""
    cards = [
        {"mana_cost": "{2}{W}{U}{B}", "cmc": 5, "type_line": "Sorcery"},
        {"mana_cost": "{W}{W}", "cmc": 2, "type_line": "Creature"},
        {"mana_cost": "{3}{U}", "cmc": 4, "type_line": "Instant"},
    ]
    result = count_color_pips(cards)
    assert result["W"]["total_pips"] == 3  # 1 + 2
    assert result["W"]["max_pips"] == 2    # {W}{W}
    assert result["U"]["total_pips"] == 2  # 1 + 1
    assert result["B"]["total_pips"] == 1


def test_color_targets_karsten() -> None:
    """Karsten lookup produces reasonable targets for a 3-color deck."""
    spells = [
        {"mana_cost": "{1}{W}{W}", "cmc": 3, "type_line": "Creature"},
        {"mana_cost": "{2}{U}", "cmc": 3, "type_line": "Instant"},
        {"mana_cost": "{3}{G}{G}", "cmc": 5, "type_line": "Sorcery"},
    ]
    targets = compute_color_targets(spells, ["W", "U", "G"], total_lands=36)
    # WW at CMC 3 → 23 sources; single U at CMC 3 → 17; GG at CMC 5 → 18
    assert targets["W"] >= 15
    assert targets["U"] >= 10
    assert targets["G"] >= 15


def test_mana_base_includes_basics() -> None:
    """Mana base output includes basic lands."""
    land_candidates = [
        (
            {"id": f"land-{i}", "name": f"Nonbasic {i}",
             "type_line": "Land", "oracle_text": "{T}: Add {W} or {U}.",
             "price_usd": 1.0},
            {"cvar_score": 0.5, "slot_role": "land"},
        )
        for i in range(10)
    ]
    spells = [
        {"mana_cost": "{1}{W}", "cmc": 2, "type_line": "Creature"},
        {"mana_cost": "{1}{U}", "cmc": 2, "type_line": "Creature"},
        {"mana_cost": "{1}{G}", "cmc": 2, "type_line": "Creature"},
    ]
    result = build_mana_base(
        land_candidates=land_candidates,
        spells=spells,
        commander_colors=["W", "U", "G"],
        total_lands=36,
    )
    names = [a.card["name"] for a in result]
    basics = [n for n in names if n in ("Plains", "Island", "Forest")]
    assert len(basics) > 0, "Expected basic lands in mana base"


def test_mana_base_color_coverage() -> None:
    """All commander colors have at least some sources."""
    spells = [
        {"mana_cost": "{W}", "cmc": 1, "type_line": "Creature"},
        {"mana_cost": "{U}", "cmc": 1, "type_line": "Creature"},
        {"mana_cost": "{B}", "cmc": 1, "type_line": "Creature"},
    ]
    result = build_mana_base(
        land_candidates=[],
        spells=spells,
        commander_colors=["W", "U", "B"],
        total_lands=36,
    )
    # With no nonbasic candidates, all lands should be basics
    names = [a.card["name"] for a in result]
    assert "Plains" in names
    assert "Island" in names
    assert "Swamp" in names


def test_mana_base_prefers_untapped() -> None:
    """Untapped lands should be preferred over ETB-tapped lands."""
    untapped = (
        {"id": "untapped-1", "name": "Good Dual",
         "type_line": "Land", "oracle_text": "{T}: Add {W} or {U}.",
         "price_usd": 5.0},
        {"cvar_score": 0.5, "slot_role": "land"},
    )
    tapped = (
        {"id": "tapped-1", "name": "Bad Dual",
         "type_line": "Land",
         "oracle_text": "Bad Dual enters the battlefield tapped.\n{T}: Add {W} or {U}.",
         "price_usd": 0.5},
        {"cvar_score": 0.8, "slot_role": "land"},
    )
    spells = [
        {"mana_cost": "{1}{W}{U}", "cmc": 3, "type_line": "Creature"},
    ]
    result = build_mana_base(
        land_candidates=[untapped, tapped],
        spells=spells,
        commander_colors=["W", "U"],
        total_lands=36,
    )
    # Both should be included, but untapped should come first (higher score)
    nonbasic_names = [
        a.card["name"] for a in result
        if a.card["name"] not in ("Plains", "Island")
    ]
    assert nonbasic_names[0] == "Good Dual"


def test_target_land_count_from_karsten() -> None:
    """target_land_count returns reasonable values for various avg CMC."""
    assert target_land_count(2.5) == 35
    assert target_land_count(3.0) == 36
    assert target_land_count(4.0) == 39
    # Out of range should still give reasonable result
    assert 33 <= target_land_count(1.5) <= 41
    assert 33 <= target_land_count(6.0) <= 41


def test_load_karsten_config() -> None:
    """Karsten config loads from YAML with expected keys."""
    config = load_karsten_config()
    assert "land_count_targets" in config
    assert "reference_land_count" in config
    assert config["reference_land_count"] == 36


# --- Formatter Tests ---


def _make_mock_deck():
    """Create a minimal GeneratedDeck for formatter testing."""
    from sabermetrics.models.card import Card
    from sabermetrics.models.deck import (
        CardSubScores,
        ComponentCounts,
        DeckCard,
        DeckClassification,
        DeckComposition,
        DeckNarrative,
        DeckParameters,
        CVARWeights,
        GeneratedDeck,
        GenerationMeta,
        LLMFit,
    )

    commander = Card(
        id="cmdr-1",
        oracle_id="oracle-1",
        name="Test Commander",
        cmc=4.0,
        type_line="Legendary Creature",
        color_identity=["B", "R"],
        is_legal_commander=True,
        is_legal_in_99=True,
        set_code="TST",
        rarity="mythic",
        last_updated=datetime.now(),
    )

    cards = []
    for i in range(5):
        card = Card(
            id=f"card-{i}",
            oracle_id=f"oracle-{i}",
            name=f"Test Card {i}",
            cmc=float(i + 1),
            type_line="Creature" if i < 3 else "Instant",
            color_identity=["B"],
            is_legal_commander=False,
            is_legal_in_99=True,
            set_code="TST",
            rarity="rare",
            last_updated=datetime.now(),
            current_price_usd=2.50,
        )
        cards.append(DeckCard(
            card=card,
            slot_role="utility" if i < 3 else "removal",
            cvar_score=0.7,
            sub_scores=CardSubScores(
                synergy=0.5, mana_efficiency=0.6,
                replacement_value=0.4, price_efficiency=0.8,
            ),
            llm_fit=LLMFit(score=7, reasoning="Good fit."),
            alternatives=[],
        ))

    return GeneratedDeck(
        id="deck-test-1",
        commander=commander,
        generated_at=datetime.now(),
        parameters=DeckParameters(
            budget_usd=200.0,
            power_target=3,
            weights=CVARWeights(),
        ),
        cards=cards,
        composition=DeckComposition(
            total_price_usd=12.50,
            average_cmc=3.0,
            color_distribution={"B": 4, "R": 1},
            type_distribution={"Creature": 3, "Instant": 2},
            mana_curve=[0, 1, 1, 1, 1, 1, 0, 0],
            component_counts=ComponentCounts(
                ramp=2, draw=1, removal=2,
                board_wipes=0, tutors=0, win_conditions=1,
            ),
            game_changers_present=[],
            detected_combos=[],
        ),
        classification=DeckClassification(
            estimated_bracket=3,
            bracket_reasoning="Optimized casual",
        ),
        narrative=DeckNarrative(
            game_plan="Win through combat.",
            key_synergies=["Card A + Card B"],
            weaknesses=["Weak to flyers"],
            suggested_play_pattern="Play aggressively.",
        ),
        meta=GenerationMeta(
            generation_time_seconds=5.0,
            llm_cost_usd=0.05,
            source_profile_id="cmdr-1",
        ),
    )


def test_format_json() -> None:
    """JSON formatter produces valid JSON."""
    deck = _make_mock_deck()
    output = format_deck(deck, "json")
    parsed = json.loads(output)
    assert parsed["commander"]["name"] == "Test Commander"
    assert len(parsed["cards"]) == 5


def test_format_text() -> None:
    """Text formatter includes key sections."""
    deck = _make_mock_deck()
    output = format_text(deck)
    assert "Test Commander" in output
    assert "Game Plan" in output
    assert "Mana Curve" in output
    assert "Card List" in output


def test_format_moxfield() -> None:
    """Moxfield format is importable."""
    deck = _make_mock_deck()
    output = format_moxfield(deck)
    assert "// Commander" in output
    assert "1 Test Commander" in output
    assert "// Deck" in output
    # Each card should be "1 Name"
    for dc in deck.cards:
        assert f"1 {dc.card.name}" in output


def test_format_archidekt() -> None:
    """Archidekt format uses 1x prefix."""
    deck = _make_mock_deck()
    output = format_archidekt(deck)
    assert "// Commander" in output
    assert "1x Test Commander" in output
    for dc in deck.cards:
        assert f"1x {dc.card.name}" in output


def test_format_unknown_raises() -> None:
    """Unknown format raises ValueError."""
    deck = _make_mock_deck()
    with pytest.raises(ValueError, match="Unknown format"):
        format_deck(deck, "xml")


# --- DeckBuildRequest Tests ---


def test_deck_build_request_defaults() -> None:
    """DeckBuildRequest has sensible defaults."""
    req = DeckBuildRequest(commander_id="test-id")
    assert req.budget_usd == 200.0
    assert req.power_target == 3
    assert req.strategy is None


def test_deck_build_request_power_validation() -> None:
    """Power target must be 1-5."""
    with pytest.raises(Exception):
        DeckBuildRequest(commander_id="test", power_target=0)
    with pytest.raises(Exception):
        DeckBuildRequest(commander_id="test", power_target=6)


# --- Integration Tests (require DB) ---


@pytest.mark.skipif(not HAS_DB, reason="No database available")
def test_deck_builder_validates_commander() -> None:
    """DeckBuilder validates commander exists."""
    from sabermetrics.pipeline.deck_builder import DeckBuilder

    builder = DeckBuilder(DB_PATH)
    req = DeckBuildRequest(commander_id="nonexistent-id")

    with pytest.raises(Exception, match="not found"):
        builder._validate_request(req)


@pytest.mark.skipif(not HAS_DB, reason="No database available")
def test_deck_builder_loads_commander() -> None:
    """DeckBuilder can load a real commander."""
    from sabermetrics.pipeline.deck_builder import DeckBuilder

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute(
        "SELECT id FROM cards WHERE is_legal_commander = 1 LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        pytest.skip("No commanders in DB")

    builder = DeckBuilder(DB_PATH)
    req = DeckBuildRequest(commander_id=row[0])
    commander = builder._validate_request(req)

    assert commander.id == row[0]
    assert commander.is_legal_commander


@pytest.mark.skipif(not HAS_DB, reason="No database available")
def test_deck_builder_filters_candidates() -> None:
    """DeckBuilder filter step produces candidates."""
    from sabermetrics.pipeline.deck_builder import DeckBuilder

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute(
        "SELECT id FROM cards WHERE is_legal_commander = 1 LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        pytest.skip("No commanders in DB")

    builder = DeckBuilder(DB_PATH)
    req = DeckBuildRequest(commander_id=row[0], budget_usd=200.0)
    commander = builder._validate_request(req)
    candidates = builder._filter_candidates(req, commander)

    assert len(candidates) > 100, f"Expected >100 candidates, got {len(candidates)}"
