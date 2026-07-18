"""Tests for B2 (price out of quality scores) and the budget rebalancing pass.

The generator was maximizing per-card cost-to-impact ratio instead of total
deck impact under the budget: $200 requests produced ~$113 decks with premium
staples triple-punished (composite price drag, Pareto price axis, cheap-biased
upgrades). These tests pin the new stance: price is a constraint, and every
expensive pick must prove its price against splitting the money.
"""

import numpy as np

from sabermetrics.analytics.cvar import ScoringContext, compute_cvar
from sabermetrics.analytics.synergy_matrix import SynergyMatrix
from sabermetrics.models.deck import CVARWeights
from sabermetrics.pipeline.greedy_optimizer import (
    greedy_fill,
    rebalance_budget,
)
from sabermetrics.pipeline.slot_assigner import SlotAssignment


# --- B2: price is not a quality signal ---


def test_price_does_not_affect_composite_score(tmp_path) -> None:
    """Two identical cards at different prices get identical composites."""
    ctx = ScoringContext(
        commander_id="x", commander_name="Cmdr", commander_colors=["B"],
    )
    base = {"name": "Test", "oracle_text": "Draw a card.", "keywords": "[]",
            "color_identity": '["B"]', "type_line": "Sorcery", "cmc": 2}
    cheap = compute_cvar(base | {"price_usd": 0.25}, ctx, tmp_path / "x.db")
    pricey = compute_cvar(base | {"price_usd": 40.0}, ctx, tmp_path / "x.db")

    assert cheap.composite_score == pricey.composite_score
    # The subscore is still computed for observability.
    assert cheap.price_efficiency_score > pricey.price_efficiency_score


def test_default_weights_sum_to_one_with_zero_price() -> None:
    w = CVARWeights()
    assert w.price_efficiency == 0.0
    assert abs(w.synergy + w.replacement_value + w.mana_efficiency - 1.0) < 1e-9


# --- Greedy affordability floor ---


def _card(card_id, name, price, cvar=0.5):
    return {"id": card_id, "name": name, "price_usd": price,
            "_cvar_score": cvar, "type_line": "Creature",
            "role_tags": '["utility"]', "cmc": 2}


def _matrix(cards):
    n = len(cards)
    return SynergyMatrix(
        matrix=np.zeros((n, n), dtype=np.float32),
        card_id_to_index={c["id"]: i for i, c in enumerate(cards)},
        index_to_card_id={i: c["id"] for i, c in enumerate(cards)},
    )


def test_greedy_floor_reserves_budget_for_remaining_slots() -> None:
    """A pick that would starve the remaining slots is skipped.

    Budget $10, 3 slots, reserve $0.25/slot: the $9.90 card would leave
    $0.10 for two slots (< 2 x 0.25), so the floor forces cheaper picks.
    """
    expensive = _card("e", "Expensive", 9.90, cvar=0.9)
    cheap1 = _card("c1", "Cheap One", 0.30, cvar=0.5)
    cheap2 = _card("c2", "Cheap Two", 0.30, cvar=0.5)
    cheap3 = _card("c3", "Cheap Three", 0.30, cvar=0.5)
    cards = [expensive, cheap1, cheap2, cheap3]

    out = greedy_fill(
        shell=[], candidates=cards, synergy=_matrix(cards),
        role_targets={}, budget_remaining=10.0, slots_remaining=3,
    )

    names = {a.card["name"] for a in out}
    assert len(out) == 3
    assert "Expensive" not in names


def test_greedy_allows_expensive_pick_when_reserve_is_safe() -> None:
    """With enough headroom the same expensive card is allowed."""
    expensive = _card("e", "Expensive", 9.90, cvar=0.9)
    cheap1 = _card("c1", "Cheap One", 0.30, cvar=0.5)
    cheap2 = _card("c2", "Cheap Two", 0.30, cvar=0.5)
    cards = [expensive, cheap1, cheap2]

    out = greedy_fill(
        shell=[], candidates=cards, synergy=_matrix(cards),
        role_targets={}, budget_remaining=20.0, slots_remaining=3,
    )

    assert "Expensive" in {a.card["name"] for a in out}


# --- Rebalancing pass ---


def _assign(card, role="utility"):
    return SlotAssignment(card=card, slot_role=role,
                          score=card.get("_cvar_score", 0.5))


def _pair_matrix(cards, pairs):
    """Matrix with named synergy pairs (symmetric)."""
    n = len(cards)
    m = np.zeros((n, n), dtype=np.float32)
    idx = {c["id"]: i for i, c in enumerate(cards)}
    for a, b, v in pairs:
        m[idx[a], idx[b]] = v
        m[idx[b], idx[a]] = v
    return SynergyMatrix(
        matrix=m, card_id_to_index=idx,
        index_to_card_id={i: cid for cid, i in idx.items()},
    )


def test_upgrade_spends_slack_on_real_quality() -> None:
    """With budget slack, a clearly better expensive candidate is bought."""
    weak = _card("w", "Weak Filler", 0.20, cvar=0.20)
    solid = _card("s", "Solid", 0.30, cvar=0.60)
    premium = _card("p", "Premium Staple", 30.0, cvar=0.95)
    deck = [_assign(weak), _assign(solid)]
    cards = [weak, solid, premium]

    out, stats = rebalance_budget(
        deck, cards, _pair_matrix(cards, []), {}, budget=100.0,
    )

    assert stats["upgrades"] >= 1
    assert "Premium Staple" in {a.card["name"] for a in out}
    assert stats["final_total"] <= 100.0


def test_upgrade_stops_at_quality_threshold_not_utilization() -> None:
    """Marginal upgrades below min_gain are refused even with budget left.

    Unspent budget is real money: if the market has nothing worth buying,
    the pass must stop, not spend for its own sake.
    """
    a = _card("a", "Card A", 0.20, cvar=0.500)
    b = _card("b", "Card B", 0.20, cvar=0.500)
    # Nearly identical quality, much more expensive.
    barely = _card("x", "Barely Better", 40.0, cvar=0.5001)
    deck = [_assign(a), _assign(b)]
    cards = [a, b, barely]

    out, stats = rebalance_budget(
        deck, cards, _pair_matrix(cards, []), {}, budget=200.0,
    )

    assert stats["upgrades"] == 0
    assert "Barely Better" not in {a_.card["name"] for a_ in out}


def test_unbundle_sells_low_contribution_expensive_card() -> None:
    """A $40 card whose value splits better across slots gets sold.

    The deck holds one expensive card with NO synergy edges and several weak
    fillers. Selling it funds multiple strong upgrades whose combined gain
    beats the card's solo contribution.
    """
    forty = _card("f", "Forty Dollar Solo", 40.0, cvar=0.65)
    w1 = _card("w1", "Filler One", 0.20, cvar=0.20)
    w2 = _card("w2", "Filler Two", 0.20, cvar=0.20)
    w3 = _card("w3", "Filler Three", 0.20, cvar=0.20)
    sub = _card("sub", "Cheap Substitute", 0.50, cvar=0.60)
    up1 = _card("u1", "Upgrade One", 12.0, cvar=0.90)
    up2 = _card("u2", "Upgrade Two", 12.0, cvar=0.90)
    up3 = _card("u3", "Upgrade Three", 12.0, cvar=0.90)
    deck = [_assign(forty), _assign(w1), _assign(w2), _assign(w3)]
    cards = [forty, w1, w2, w3, sub, up1, up2, up3]

    # Budget exactly at current total: no slack, so only unbundling can act.
    budget = 40.0 + 0.20 * 3
    out, stats = rebalance_budget(
        deck, cards, _pair_matrix(cards, []), {}, budget=budget,
    )

    names = {a.card["name"] for a in out}
    assert stats["unbundles"] == 1
    assert "Forty Dollar Solo" not in names
    assert "Cheap Substitute" in names
    assert sum(float(a.card["price_usd"]) for a in out) <= budget + 1e-9


def test_unbundle_keeps_expensive_card_that_proves_its_price() -> None:
    """An expensive card carrying strong synergy edges survives the audit.

    Same shape as the sell case, but the $40 card has high pairwise synergy
    with the rest of the deck (combo-piece shape), so its contribution beats
    any reallocation.
    """
    forty = _card("f", "Forty Dollar Engine", 40.0, cvar=0.65)
    w1 = _card("w1", "Partner One", 0.20, cvar=0.40)
    w2 = _card("w2", "Partner Two", 0.20, cvar=0.40)
    sub = _card("sub", "Cheap Substitute", 0.50, cvar=0.45)
    up1 = _card("u1", "Upgrade One", 12.0, cvar=0.55)
    deck = [_assign(forty), _assign(w1), _assign(w2)]
    cards = [forty, w1, w2, sub, up1]
    synergy = _pair_matrix(cards, [("f", "w1", 1.0), ("f", "w2", 1.0)])

    budget = 40.0 + 0.20 * 2
    out, stats = rebalance_budget(deck, cards, synergy, {}, budget=budget)

    assert stats["unbundles"] == 0
    assert "Forty Dollar Engine" in {a.card["name"] for a in out}


def test_protected_names_are_never_sold() -> None:
    forty = _card("f", "Protected Forty", 40.0, cvar=0.30)
    w1 = _card("w1", "Filler", 0.20, cvar=0.20)
    up1 = _card("u1", "Upgrade", 15.0, cvar=0.95)
    deck = [_assign(forty), _assign(w1)]
    cards = [forty, w1, up1]

    out, stats = rebalance_budget(
        deck, cards, _pair_matrix(cards, []), {}, budget=40.2,
        protected_names={"Protected Forty"},
    )

    assert "Protected Forty" in {a.card["name"] for a in out}
    assert stats["unbundles"] == 0


def test_downgrade_safety_net_fixes_overrun() -> None:
    """A deck over budget on entry is brought under with minimal loss."""
    pricey = _card("p", "Pricey", 30.0, cvar=0.60)
    ok = _card("o", "Okay", 0.50, cvar=0.55)
    cheap_alt = _card("c", "Cheap Alt", 0.40, cvar=0.58)
    deck = [_assign(pricey), _assign(ok)]
    cards = [pricey, ok, cheap_alt]

    out, stats = rebalance_budget(
        deck, cards, _pair_matrix(cards, []), {}, budget=10.0,
    )

    assert sum(float(a.card["price_usd"]) for a in out) <= 10.0
    assert stats["downgrades"] >= 1
