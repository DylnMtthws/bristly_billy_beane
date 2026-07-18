"""Tests for Stage 3.5 empirical staple reservation.

Reservation is what actually lands the engine pieces the corpus validates but
the role scorers reject. These cover the selection rules: the inclusion floor,
the reliability gate, the cap, and the land/auto-include exclusions.
"""

from pathlib import Path

import pytest

from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.deck_builder import DeckBuildRequest, DeckBuilder
from sabermetrics.pipeline.trace import GenerationTracer


def _card(name, inclusion, reliable=True, type_line="Creature", price=1.0):
    return {
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "type_line": type_line,
        "price_usd": price,
        "_empirical_inclusion": inclusion,
        "_empirical_reliable": reliable,
    }


def _template(differentiator_slots=37):
    return DeckTemplate(
        land_count=36, ramp_count=10, draw_count=8, removal_count=6,
        board_wipe_count=2, differentiator_slots=differentiator_slots,
        avg_cmc_target=3.0,
    )


@pytest.fixture
def builder():
    b = DeckBuilder(Path("unused.db"))
    b._tracer = GenerationTracer(generation_id="test")
    return b


def _request(budget=200.0):
    return DeckBuildRequest(commander_id="x", budget_usd=budget)


def _reserved_names(builder, cards, template=None, request=None):
    out = builder._reserve_empirical_staples(
        cards, request or _request(), template or _template()
    )
    return [a.card["name"] for a in out]


def test_reserves_cards_above_the_floor(builder):
    """A reliable card at/above the inclusion floor is reserved."""
    cards = [_card("Staple", 0.65), _card("Fringe", 0.20)]
    names = _reserved_names(builder, cards)
    assert names == ["Staple"]


def test_absence_and_low_inclusion_are_never_reserved(builder):
    """Below-floor and zero-inclusion cards are excluded (absence neutral)."""
    cards = [_card("Low", 0.30), _card("Zero", 0.0), _card("Unseen", 0.0)]
    assert _reserved_names(builder, cards) == []


def test_unreliable_high_rate_is_not_reserved(builder):
    """A high rate with a wide CI is noise, not a reserved staple."""
    cards = [_card("Noisy", 0.90, reliable=False)]
    assert _reserved_names(builder, cards) == []


def test_reservation_is_ranked_and_capped(builder):
    """Reservation takes the highest-inclusion cards up to the cap."""
    # 20 eligible cards; default cap is min(12, int(37 * 0.5)) = 12.
    cards = [_card(f"C{i:02d}", 0.50 + i * 0.01) for i in range(20)]
    names = _reserved_names(builder, cards)
    assert len(names) == 12
    # Highest inclusion first: C19 (0.69) down to C08 (0.58).
    assert names[0] == "C19"
    assert "C00" not in names  # 0.50, ranked out by the cap


def test_fraction_cap_binds_on_small_decks(builder):
    """On a small differentiator budget the fraction cap wins over the count."""
    cards = [_card(f"C{i:02d}", 0.90 - i * 0.01) for i in range(20)]
    # differentiator_slots=10 -> int(10 * 0.5) = 5 < 12.
    names = _reserved_names(builder, cards, template=_template(10))
    assert len(names) == 5


def test_lands_are_never_reserved(builder):
    """Lands are placed by the land generator, never reserved here."""
    cards = [
        _card("Great Land", 0.80, type_line="Land"),
        _card("Real Staple", 0.60),
    ]
    assert _reserved_names(builder, cards) == ["Real Staple"]


def test_budget_ceiling_skips_unaffordable_staples(builder):
    """A staple that would blow the budget is skipped, not reserved."""
    cards = [_card("Cheap", 0.60, price=1.0), _card("Pricey", 0.70, price=500.0)]
    names = _reserved_names(builder, cards, request=_request(budget=10.0))
    assert names == ["Cheap"]


def test_generic_auto_includes_are_not_reserved(builder):
    """Cards the generators already auto-include don't spend a reserved slot.

    Sol Ring is a config auto-include; reserving it would waste capacity the
    mis-roled engine pieces need.
    """
    cards = [
        _card("Sol Ring", 0.95, type_line="Artifact"),
        _card("Real Staple", 0.60),
    ]
    assert _reserved_names(builder, cards) == ["Real Staple"]
