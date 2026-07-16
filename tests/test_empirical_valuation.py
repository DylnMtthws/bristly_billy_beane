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
