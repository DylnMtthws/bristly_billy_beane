"""Tests for the engine-floor repair pass (Stage 5.5).

The soft type-need multiplier equilibrates selection at the corpus median
(build 10: 27 auras against a floor of 30), so floors above the median are
enforced by a deterministic repair pass -- weakest off-type picks swapped for
the best unplaced on-type candidates, before the LLM vet audits the result.
"""

from pathlib import Path

import pytest

from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.deck_builder import DeckBuilder
from sabermetrics.pipeline.slot_assigner import SlotAssignment
from sabermetrics.pipeline.trace import GenerationTracer


def _card(name, type_line="Creature", price=1.0, cvar=0.5, **extra):
    card = {
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "type_line": type_line,
        "price_usd": price,
        "_cvar_score": cvar,
    }
    card.update(extra)
    return card


def _aura(name, **kw):
    return _card(name, type_line="Enchantment — Aura", **kw)


def _assignment(card, role="utility", score=0.5):
    return SlotAssignment(card=card, slot_role=role, score=score)


def _template(floors=None):
    return DeckTemplate(
        land_count=36, ramp_count=8, draw_count=6, removal_count=6,
        board_wipe_count=2, differentiator_slots=37, avg_cmc_target=3.0,
        type_floors=floors,
    )


@pytest.fixture
def builder():
    b = DeckBuilder(Path("unused.db"))
    b._tracer = GenerationTracer(generation_id="test")
    return b


def _names(assignments):
    return [a.card["name"] for a in assignments]


def test_deficit_is_repaired_weakest_first(builder):
    """Two missing auras displace the two weakest off-type picks."""
    deck = [
        _assignment(_aura("Aura One"), score=0.9),
        _assignment(_card("Strong Creature"), score=0.8),
        _assignment(_card("Weak Sorcery", type_line="Sorcery"), score=0.2),
        _assignment(_card("Weaker Artifact", type_line="Artifact"), score=0.1),
    ]
    pool = [_aura("Best Aura", cvar=0.9), _aura("Good Aura", cvar=0.7)]

    out, swaps = builder._enforce_type_floors(
        deck, pool, _template({"aura": 3}), budget=200.0,
    )

    assert swaps == 2
    names = _names(out)
    assert "Best Aura" in names and "Good Aura" in names
    assert "Weak Sorcery" not in names and "Weaker Artifact" not in names
    assert "Strong Creature" in names  # strongest off-type survives


def test_no_floors_is_a_noop(builder):
    deck = [_assignment(_card("Anything"), score=0.1)]
    out, swaps = builder._enforce_type_floors(
        deck, [_aura("Aura")], _template(None), budget=200.0,
    )
    assert swaps == 0 and _names(out) == ["Anything"]


def test_met_floor_is_a_noop(builder):
    deck = [_assignment(_aura("Aura One")), _assignment(_card("Creature"))]
    out, swaps = builder._enforce_type_floors(
        deck, [_aura("Better Aura", cvar=0.99)],
        _template({"aura": 1}), budget=200.0,
    )
    assert swaps == 0 and "Better Aura" not in _names(out)


def test_protected_cards_are_never_swapped_out(builder):
    """A reserved staple below everyone's score still can't be removed."""
    deck = [
        _assignment(_card("Reserved Staple"), score=0.05),
        _assignment(_card("Weak Filler"), score=0.3),
    ]
    out, swaps = builder._enforce_type_floors(
        deck, [_aura("New Aura")], _template({"aura": 1}), budget=200.0,
        protected_names={"Reserved Staple"},
    )
    assert swaps == 1
    assert "Reserved Staple" in _names(out)
    assert "Weak Filler" not in _names(out)


def test_lands_are_never_swapped_out(builder):
    deck = [
        _assignment(_card("Some Plains", type_line="Basic Land — Plains",
                          price=0.0), role="land", score=0.0),
        _assignment(_card("Filler"), score=0.4),
    ]
    out, swaps = builder._enforce_type_floors(
        deck, [_aura("New Aura")], _template({"aura": 1}), budget=200.0,
    )
    assert swaps == 1 and "Some Plains" in _names(out)


def test_budget_delta_skips_to_affordable_candidate(builder):
    """An unaffordable on-type upgrade is skipped for a cheaper one."""
    deck = [_assignment(_card("Cheap Filler", price=0.10), score=0.2)]
    pool = [
        _aura("Expensive Aura", price=150.0, cvar=0.95),
        _aura("Budget Aura", price=0.50, cvar=0.60),
    ]
    # Deck total 0.10, budget 5 -> headroom 4.90: the $150 delta fails,
    # the $0.40 delta fits.
    out, swaps = builder._enforce_type_floors(
        deck, pool, _template({"aura": 1}), budget=5.0,
    )
    assert swaps == 1 and _names(out) == ["Budget Aura"]


def test_anti_engine_candidates_are_excluded(builder):
    deck = [_assignment(_card("Filler"), score=0.2)]
    pool = [
        _aura("Vetoed Aura", cvar=0.99, _anti_engine=True),
        _aura("Clean Aura", cvar=0.50),
    ]
    out, swaps = builder._enforce_type_floors(
        deck, pool, _template({"aura": 1}), budget=200.0,
    )
    assert swaps == 1 and _names(out) == ["Clean Aura"]


def test_unmet_floor_degrades_gracefully(builder):
    """Pool exhaustion leaves the deck valid and merely under-floor."""
    deck = [_assignment(_card("Filler"), score=0.2)]
    out, swaps = builder._enforce_type_floors(
        deck, [_aura("Only Aura")], _template({"aura": 5}), budget=200.0,
    )
    assert swaps == 1 and len(out) == 1
