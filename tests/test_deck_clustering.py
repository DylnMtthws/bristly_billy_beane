"""Tests for Phase 3 macro-archetype deck clustering."""

import warnings

import numpy as np

from sabermetrics.analytics.archetype_signatures import load_library
from sabermetrics.analytics.deck_clustering import (
    DeckRecord,
    bootstrap_stability,
    build_feature_matrix,
    cluster_decks,
    name_clusters,
    suggest_k,
)

LIB = load_library()

ARISTO = ["Blood Artist", "Zulaport Cutthroat", "Cruel Celebrant",
          "Bastion of Remembrance", "Viscera Seer"]
LANDFALL = ["Lotus Cobra", "Scute Swarm", "Rampaging Baloths",
            "Avenger of Zendikar", "Felidar Retreat"]


def _decks(card_lists) -> list[DeckRecord]:
    return [
        DeckRecord(deck_id=f"d{i}", card_names=cards)
        for i, cards in enumerate(card_lists)
    ]


def test_build_feature_matrix_shape_and_content() -> None:
    decks = _decks([ARISTO, LANDFALL])
    feats, names = build_feature_matrix(decks, LIB, normalize=False)
    assert feats.shape == (2, len(names))
    ai = names.index("aristocrats")
    li = names.index("landfall")
    assert feats[0, ai] > 0 and feats[0, li] == 0   # aristocrats deck
    assert feats[1, li] > 0 and feats[1, ai] == 0   # landfall deck


def test_build_feature_matrix_normalizes_rows() -> None:
    feats, _ = build_feature_matrix(_decks([ARISTO]), LIB, normalize=True)
    assert np.isclose(np.linalg.norm(feats[0]), 1.0)


def test_empty_deck_row_is_all_zero_and_safe() -> None:
    feats, _ = build_feature_matrix(_decks([["Sol Ring", "Island"]]), LIB, True)
    assert np.allclose(feats[0], 0.0)  # no signatures -> zero, no div-by-zero


def test_wellseparated_decks_cluster_and_name_correctly() -> None:
    decks = _decks([ARISTO] * 8 + [LANDFALL] * 8)
    feats, names = build_feature_matrix(decks, LIB, normalize=True)
    labels, model = cluster_decks(feats, k=2, seed=0)
    # Two archetypes -> two clean clusters
    assert len(set(labels)) == 2
    named = {v[0][0] for v in name_clusters(model, names).values()}
    assert named == {"aristocrats", "landfall"}


def test_bootstrap_stability_high_for_separated_clusters() -> None:
    decks = _decks([ARISTO] * 8 + [LANDFALL] * 8)
    feats, _ = build_feature_matrix(decks, LIB, normalize=True)
    result = bootstrap_stability(feats, k=2, n_bootstrap=40, seed=0)
    assert result.mean_ari >= 0.75
    assert result.verdict == "stable"


def test_bootstrap_stability_flags_degenerate_pool() -> None:
    # All identical decks: no real 2-way split exists.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        feats, _ = build_feature_matrix(_decks([ARISTO] * 10), LIB, True)
        result = bootstrap_stability(feats, k=2, n_bootstrap=40, seed=0)
    assert result.verdict == "not real at this N"


def test_suggest_k_clamps_to_range() -> None:
    decks = _decks([ARISTO] * 6 + [LANDFALL] * 6)
    assert 2 <= suggest_k(decks, LIB) <= 6


def test_select_k_is_floor_aware() -> None:
    # Two real groups; a floor that k=2 satisfies but k>=3 cannot (would split
    # a real group below floor). Floor-aware selection must pick k=2.
    from sabermetrics.analytics.deck_clustering import select_k

    decks = _decks([ARISTO] * 15 + [LANDFALL] * 15)
    feats, _ = build_feature_matrix(decks, LIB, normalize=True)
    k, rationale = select_k(feats, floor=12, k_min=2, k_max=5, n_bootstrap=30)
    assert k == 2
    assert "floor" in rationale
