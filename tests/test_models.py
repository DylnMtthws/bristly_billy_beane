"""Tests for Pydantic models (A1.4 acceptance gate)."""

import json
from datetime import datetime

from sabermetrics.models.card import Card, CardRuling
from sabermetrics.models.deck import CVARWeights, DeckParameters
from sabermetrics.models.llm_responses import CardFitResponse, RelevanceScreenResponse
from sabermetrics.models.profile import ValueInversion


def test_card_model_from_db_row() -> None:
    """A Card model can be created from data matching DB schema."""
    card = Card(
        id="abc-123",
        oracle_id="oracle-456",
        name="Sol Ring",
        mana_cost="{1}",
        cmc=1.0,
        type_line="Artifact",
        oracle_text="{T}: Add {C}{C}.",
        color_identity=[],
        keywords=[],
        is_legal_commander=False,
        is_legal_in_99=True,
        set_code="cmd",
        rarity="uncommon",
        image_uri="https://example.com/solring.jpg",
        last_updated=datetime.now(),
    )
    assert card.name == "Sol Ring"
    assert card.cmc == 1.0
    assert card.color_identity == []
    assert card.is_legal_in_99 is True
    assert card.is_legal_commander is False


def test_card_model_with_json_fields() -> None:
    """JSON array fields (color_identity, keywords) deserialize correctly."""
    raw_color_identity = json.dumps(["W", "U", "B"])
    raw_keywords = json.dumps(["Flying", "Lifelink"])

    card = Card(
        id="xyz-789",
        oracle_id="oracle-012",
        name="Test Commander",
        cmc=4.0,
        type_line="Legendary Creature — Angel",
        color_identity=json.loads(raw_color_identity),
        keywords=json.loads(raw_keywords),
        is_legal_commander=True,
        is_legal_in_99=True,
        set_code="tst",
        rarity="mythic",
        last_updated=datetime.now(),
    )
    assert card.color_identity == ["W", "U", "B"]
    assert card.keywords == ["Flying", "Lifelink"]
    assert card.is_legal_commander is True


def test_card_ruling_model() -> None:
    """CardRuling can be created with minimal fields."""
    ruling = CardRuling(
        ruling_text="This is a test ruling.",
    )
    assert ruling.source == "mtgapi"
    assert ruling.ruling_date is None


def test_card_with_optional_fields() -> None:
    """Card model handles None/missing optional fields."""
    card = Card(
        id="min-card",
        oracle_id="min-oracle",
        name="Minimal Card",
        cmc=0.0,
        type_line="Instant",
        color_identity=["R"],
        is_legal_commander=False,
        is_legal_in_99=True,
        set_code="min",
        rarity="common",
        last_updated=datetime.now(),
    )
    assert card.mana_cost is None
    assert card.oracle_text is None
    assert card.image_uri is None
    assert card.current_price_usd is None
    assert card.rulings == []


def test_cvar_weights_defaults() -> None:
    """CVARWeights has correct defaults summing to 1.0."""
    weights = CVARWeights()
    total = (
        weights.synergy
        + weights.replacement_value
        + weights.mana_efficiency
        + weights.price_efficiency
    )
    assert abs(total - 1.0) < 0.001


def test_card_fit_response() -> None:
    """CardFitResponse validates score range."""
    resp = CardFitResponse(
        fit_score=7,
        reasoning="Strong synergy with commander",
        slot_role="utility",
    )
    assert resp.fit_score == 7
    assert resp.slot_role == "utility"


def test_relevance_screen_response() -> None:
    """RelevanceScreenResponse parses correctly."""
    resp = RelevanceScreenResponse(
        card_name="New Card",
        affects_strategy=True,
        reason="Enables a new combo line",
    )
    assert resp.affects_strategy is True


def test_value_inversion_with_undesired_characteristics() -> None:
    """ValueInversion supports the undesired_characteristics field."""
    inv = ValueInversion(
        normal_heuristic="Higher power is better",
        inverted_value="Toughness deals damage instead",
        desired_characteristics=["high toughness", "defender"],
        undesired_characteristics=["high power", "power-based triggers"],
        evaluation_guidance="Rate by toughness-to-mana, not power-to-mana",
    )
    assert inv.undesired_characteristics == ["high power", "power-based triggers"]
    assert inv.desired_characteristics == ["high toughness", "defender"]


def test_value_inversion_undesired_defaults_empty() -> None:
    """ValueInversion defaults undesired_characteristics to empty list."""
    inv = ValueInversion(
        normal_heuristic="Higher power is better",
        inverted_value="Toughness deals damage instead",
        desired_characteristics=["high toughness"],
        evaluation_guidance="Rate by toughness-to-mana",
    )
    assert inv.undesired_characteristics == []
