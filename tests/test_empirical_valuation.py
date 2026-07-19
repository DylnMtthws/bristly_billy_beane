"""Tests for Phase 6: per-variant empirical grounding of the generator."""

from sabermetrics.analytics.cvar import ScoringContext, compute_synergy_score
from sabermetrics.analytics.deck_clustering import DeckRecord

ARISTO = ["Blood Artist", "Zulaport Cutthroat", "Cruel Celebrant",
          "Bastion of Remembrance", "Viscera Seer", "Sol Ring"]
LANDFALL = ["Lotus Cobra", "Scute Swarm", "Rampaging Baloths",
            "Avenger of Zendikar", "Felidar Retreat", "Sol Ring"]


def _base_ctx(**kw) -> ScoringContext:
    return ScoringContext(
        commander_id="x", commander_name="Cmdr", commander_colors=["B"], **kw
    )


def test_annotate_empirical_carries_fields_by_name() -> None:
    """Candidate-table cards inherit empirical fields from the source by name.

    The Stage 4 generators load candidate-table cards fresh from SQL, so they
    lack the fields _structural_score set on the deck pool. annotate_empirical
    carries them over; unmatched cards stay neutral.
    """
    from sabermetrics.analytics.empirical_valuation import annotate_empirical

    source = [
        {"name": "Pitiless Plunderer", "_empirical_inclusion": 0.65,
         "_empirical_reliable": True},
    ]
    cards = [
        {"name": "Pitiless Plunderer", "ramp_score": 0.5},   # matched
        {"name": "Random Jank", "ramp_score": 0.5},          # unmatched -> neutral
    ]
    annotate_empirical(cards, source)

    assert cards[0]["_empirical_inclusion"] == 0.65
    assert cards[0]["_empirical_reliable"] is True
    assert cards[1]["_empirical_inclusion"] == 0.0
    assert cards[1]["_empirical_reliable"] is False


def test_empirical_boost_raises_score_and_absence_is_neutral() -> None:
    card = {"name": "Pitiless Plunderer", "oracle_text": "", "keywords": "[]",
            "color_identity": '["B"]', "type_line": "Creature"}
    base = compute_synergy_score(card, _base_ctx())
    boosted = compute_synergy_score(
        card, _base_ctx(empirical_inclusion={"pitiless plunderer": 0.85},
                        empirical_reliable={"pitiless plunderer"}),
    )
    assert boosted > base                       # corroboration boosts
    # A card ABSENT from the corpus is not penalized (same as no signal).
    absent = compute_synergy_score(
        card, _base_ctx(empirical_inclusion={"some other card": 0.9}),
    )
    assert absent == base


def test_reliable_inclusion_boosts_more_than_noisy() -> None:
    card = {"name": "Deadly Dispute", "oracle_text": "", "keywords": "[]",
            "color_identity": '["B"]', "type_line": "Instant"}
    reliable = compute_synergy_score(
        card, _base_ctx(empirical_inclusion={"deadly dispute": 0.6},
                        empirical_reliable={"deadly dispute"}))
    noisy = compute_synergy_score(
        card, _base_ctx(empirical_inclusion={"deadly dispute": 0.6}))
    assert reliable > noisy


def test_get_target_cluster_inclusion_selects_and_degrades(tmp_path, monkeypatch) -> None:
    from sabermetrics.analytics import empirical_valuation as ev

    decks = (
        [DeckRecord(deck_id=f"a{i}", card_names=ARISTO, popularity_rank=i)
         for i in range(30)]
        + [DeckRecord(deck_id=f"l{i}", card_names=LANDFALL, popularity_rank=i)
           for i in range(20)]
    )
    monkeypatch.setattr(ev, "load_commander_decks", lambda *a, **k: decks)

    # Default: largest variant (aristocrats, n=30) with Blood Artist ~100%.
    inc = ev.get_target_cluster_inclusion(tmp_path / "x.db", "Cmdr")
    assert inc is not None and inc.variant == "aristocrats"
    assert inc.rate("Blood Artist") > 0.9
    assert inc.rate("Lotus Cobra") == 0.0        # not in this variant — neutral

    # Strategy hint routes to the landfall variant.
    inc2 = ev.get_target_cluster_inclusion(tmp_path / "x.db", "Cmdr", strategy="landfall")
    assert inc2 is not None and inc2.variant == "landfall"
    assert inc2.rate("Lotus Cobra") > 0.9

    # Too few decks -> no signal (clean fallback).
    monkeypatch.setattr(ev, "load_commander_decks", lambda *a, **k: decks[:5])
    assert ev.get_target_cluster_inclusion(tmp_path / "x.db", "Cmdr") is None


def test_reliability_is_gated_on_variant_sample_size(tmp_path, monkeypatch) -> None:
    """Reliability is a property of the variant's size, not per-card CI width.

    A variant whose worst-case (p=0.5) margin exceeds the bar is too small to
    trust any mid-range rate, so nothing is reliable even though inclusion is
    still populated. A large-enough variant trusts all its rates.
    """
    from sabermetrics.analytics import empirical_valuation as ev

    def _decks(n):
        return [
            DeckRecord(deck_id=f"a{i}", card_names=ARISTO, popularity_rank=i)
            for i in range(n)
        ]

    # 30 decks: worst-case margin ~0.168 > 0.15 bar -> not reliable.
    monkeypatch.setattr(ev, "load_commander_decks", lambda *a, **k: _decks(30))
    small = ev.get_target_cluster_inclusion(tmp_path / "x.db", "Cmdr")
    assert small is not None
    assert small.rate("Blood Artist") > 0.9      # inclusion still computed
    assert small.reliable == set()               # but nothing is reliable

    # 45 decks: worst-case margin < 0.15 bar -> all cards reliable.
    monkeypatch.setattr(ev, "load_commander_decks", lambda *a, **k: _decks(45))
    big = ev.get_target_cluster_inclusion(tmp_path / "x.db", "Cmdr")
    assert big is not None
    assert "blood artist" in big.reliable
    assert big.reliable == set(big.inclusion)


def test_compute_composition_medians_and_type_rules() -> None:
    """Medians are quantity-aware; lands are excluded from other type counts."""
    from sabermetrics.analytics.empirical_valuation import compute_composition

    deck_a = (
        [("Basic Land — Swamp", 0.0, 10)]
        + [("Land", 0.0, 26)]
        + [("Enchantment — Aura", 1.0, 20)]
        + [("Enchantment", 2.0, 16)]
        + [("Creature — Human", 2.0, 15)]
        + [("Artifact", 2.0, 5)]
        + [("Instant", 3.0, 7)]
    )
    deck_b = (
        [("Land", 0.0, 34)]
        + [("Enchantment — Aura", 1.0, 30)]
        + [("Creature — Human", 3.0, 20)]
        + [("Sorcery", 4.0, 15)]
    )
    comp = compute_composition({"a": deck_a, "b": deck_b})

    assert comp is not None
    assert comp.lands == 35            # median(36, 34)
    assert comp.enchantments == 33     # median(36, 30)
    assert comp.auras == 25            # median(20, 30)
    assert comp.creatures == 18        # median(15, 20)
    # Per-deck avg CMC is weighted by quantity and excludes lands.
    assert 1.5 < comp.avg_cmc < 3.0


def test_compute_composition_empty_returns_none() -> None:
    from sabermetrics.analytics.empirical_valuation import compute_composition

    assert compute_composition({}) is None


def test_pooled_fallback_merges_same_archetype_clusters(tmp_path, monkeypatch) -> None:
    """The Agatha cascade: k-means over-splits one archetype into clusters
    that each fail the reliability bar; pooling the same-archetype clusters
    passes it. Two near-identical aristocrats populations (36+36, forced
    k=2) must pool to n=72 and come back fully reliable."""
    from sabermetrics.analytics import empirical_valuation as ev

    decks = (
        [DeckRecord(deck_id=f"a{i}", card_names=ARISTO + ["Filler A"],
                    popularity_rank=i) for i in range(36)]
        + [DeckRecord(deck_id=f"b{i}", card_names=ARISTO + ["Filler B"],
                      popularity_rank=i) for i in range(36)]
    )
    monkeypatch.setattr(ev, "load_commander_decks", lambda *a, **k: decks)
    monkeypatch.setattr(ev, "select_k", lambda *a, **k: (2, "forced"))

    inc = ev.get_target_cluster_inclusion(tmp_path / "x.db", "Cmdr")

    assert inc is not None
    # If k-means split the two populations, each cluster (36) fails the bar
    # and pooling restores reliability at n=72; if it kept one cluster of 72,
    # the variant is reliable directly. Either way nothing may come back
    # unreliable, and Blood Artist is a trusted staple.
    assert inc.variant_size == 72
    assert inc.reliable == set(inc.inclusion)
    assert inc.rate("blood artist") > 0.9


def test_pooling_never_crosses_archetype_labels(tmp_path, monkeypatch) -> None:
    """A genuinely mixed small corpus still degrades to unreliable.

    36 aristocrats + 36 landfall: the aristocrats target (36) fails the bar
    and there is no second aristocrats cluster to pool with -- landfall decks
    must NOT corroborate cards in an aristocrats build, so reliable stays
    empty rather than contaminating rates across archetypes."""
    from sabermetrics.analytics import empirical_valuation as ev

    decks = (
        [DeckRecord(deck_id=f"a{i}", card_names=ARISTO, popularity_rank=i)
         for i in range(36)]
        + [DeckRecord(deck_id=f"l{i}", card_names=LANDFALL, popularity_rank=i)
           for i in range(36)]
    )
    monkeypatch.setattr(ev, "load_commander_decks", lambda *a, **k: decks)

    inc = ev.get_target_cluster_inclusion(tmp_path / "x.db", "Cmdr")

    assert inc is not None
    assert inc.variant_size == 36            # stayed variant-level
    assert inc.reliable == set()             # too small, and no safe pool
    assert inc.rate("Blood Artist") > 0.9    # sharp in-variant rate kept
