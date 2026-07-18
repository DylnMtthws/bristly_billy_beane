"""Tests for the Pareto filter's empirical protection (Stage 2).

The filter drops a card when another in the same role has both higher CVAR and
a lower price. That rule is what removed proven staples from generated decks:
$0.03 jank price-dominates a $2 card that appears in most real decks of the
target variant. These cover the carve-out that protects them.
"""

from pathlib import Path

import pytest

from sabermetrics.pipeline.deck_builder import DeckBuilder
from sabermetrics.pipeline.trace import GenerationTracer


def _make_card(
    card_id: str,
    name: str,
    cvar_score: float,
    price: float,
    role: str = "utility",
    **extra: object,
) -> dict:
    card = {
        "id": card_id,
        "name": name,
        "role_tags": f'["{role}"]',
        "_cvar_score": cvar_score,
        "price_usd": price,
    }
    card.update(extra)
    return card


def _fillers(n: int = 210) -> list[dict]:
    """Mutually non-dominating filler to clear both of the filter's floors.

    The filter re-adds dominated cards twice over: per-role when a role's
    frontier drops below 30, and globally when total non-land survivors drop
    below max(structural_filter_target, max_candidates_for_llm_fit * 3), which
    is currently 200. So nothing is ever eliminated below ~200 non-land
    candidates, and a small test group would silently keep everything --
    passing whether or not the protection works.

    Each filler pairs a lower CVAR with a lower price, so none dominates
    another and all survive on their own merits. They are also priced far above
    the cards under test, so they never dominate them and serve only to clear
    the floors.
    """
    return [
        _make_card(
            f"filler-{i}", f"Filler {i}",
            cvar_score=0.99 - i * 0.001,
            price=500.0 - i * 2.0,
        )
        for i in range(n)
    ]


@pytest.fixture
def builder() -> DeckBuilder:
    b = DeckBuilder(Path("unused.db"))
    # build() normally installs the tracer; _pareto_filter records through it.
    b._tracer = GenerationTracer(generation_id="test")
    return b


def _names(cards: list[dict]) -> set[str]:
    return {c["name"] for c in cards}


def _names_under_test(cards: list[dict]) -> set[str]:
    return {c["name"] for c in cards if not c["name"].startswith("Filler")}


def test_dominated_card_without_empirical_support_is_dropped(builder) -> None:
    """Baseline: the domination rule still applies to unsupported cards."""
    jank = _make_card("jank", "Cheap Jank", cvar_score=0.60, price=0.03)
    loser = _make_card("loser", "Dominated Card", cvar_score=0.50, price=2.00)

    kept = builder._pareto_filter([jank, loser] + _fillers())

    assert _names_under_test(kept) == {"Cheap Jank"}


def test_empirical_staple_survives_price_domination(builder) -> None:
    """The Phase 6 failure: a staple in 90% of real decks beaten by $0.03 jank.

    Jank strictly dominates on both axes (higher CVAR, lower price), so without
    the carve-out the staple is eliminated before selection ever sees it.
    """
    jank = _make_card("jank", "Cheap Jank", cvar_score=0.60, price=0.03)
    staple = _make_card(
        "staple", "Pitiless Plunderer", cvar_score=0.50, price=2.00,
        _empirical_inclusion=0.90, _empirical_reliable=True,
    )

    kept = builder._pareto_filter([jank, staple] + _fillers())

    assert _names_under_test(kept) == {"Cheap Jank", "Pitiless Plunderer"}


def test_protection_requires_reliable_inclusion(builder) -> None:
    """A high rate that is not reliable must not protect.

    Wide Wilson bands mean the rate is noise; it should not be strong enough to
    override the domination rule.
    """
    jank = _make_card("jank", "Cheap Jank", cvar_score=0.60, price=0.03)
    noisy = _make_card(
        "noisy", "Noisy Card", cvar_score=0.50, price=2.00,
        _empirical_inclusion=0.90, _empirical_reliable=False,
    )

    kept = builder._pareto_filter([jank, noisy] + _fillers())

    assert _names_under_test(kept) == {"Cheap Jank"}


def test_complementary_staples_both_survive(builder) -> None:
    """A staple is protected even when its dominator is also a staple.

    This is the case that actually occurs, and the one an earlier gap-based
    rule got wrong. Real Korvold decks run Pitiless Plunderer (65%) and Deadly
    Dispute (55%) together -- they are complements, not substitutes, so the
    margin between them is small. Requiring the dominator to be meaningfully
    rarer meant Deadly Dispute eliminated Pitiless Plunderer outright.

    Both appear in most real decks, so both must survive.
    """
    rival = _make_card(
        "rival", "Deadly Dispute", cvar_score=0.775, price=0.35,
        _empirical_inclusion=0.55, _empirical_reliable=True,
    )
    staple = _make_card(
        "staple", "Pitiless Plunderer", cvar_score=0.475, price=3.15,
        _empirical_inclusion=0.65, _empirical_reliable=True,
    )

    kept = builder._pareto_filter([rival, staple] + _fillers())

    assert _names_under_test(kept) == {"Deadly Dispute", "Pitiless Plunderer"}


def test_protection_requires_clearing_the_inclusion_floor(builder) -> None:
    """A card below the inclusion floor is still eliminated normally.

    Protection keys on the card's own rate, so a card the corpus barely plays
    gets no exemption however common its dominator is.
    """
    jank = _make_card("jank", "Cheap Jank", cvar_score=0.60, price=0.03)
    fringe = _make_card(
        "fringe", "Fringe Card", cvar_score=0.50, price=2.00,
        _empirical_inclusion=0.10, _empirical_reliable=True,
    )

    kept = builder._pareto_filter([jank, fringe] + _fillers())

    assert _names_under_test(kept) == {"Cheap Jank"}


def test_no_corpus_data_leaves_filter_behaviour_unchanged(builder) -> None:
    """With no corpus at all, the filter degrades to the plain rule."""
    cards = [
        _make_card("a", "Best", cvar_score=0.90, price=0.10),
        _make_card("b", "Dominated", cvar_score=0.20, price=5.00),
        _make_card("c", "Expensive But Strong", cvar_score=0.95, price=9.00),
    ]

    kept = builder._pareto_filter(cards + _fillers())

    assert _names_under_test(kept) == {"Best", "Expensive But Strong"}
