"""Tests for the macro-archetype signature library (Phase 1)."""

from sabermetrics.analytics.archetype_signatures import (
    classify_deck,
    load_library,
    normalize_name,
    score_deck,
    tags_to_archetypes,
)

LIB = load_library()


def test_library_loads_expected_archetypes() -> None:
    assert LIB.archetypes, "library should not be empty"
    for expected in ("aristocrats", "landfall", "stax", "voltron", "spellslinger"):
        assert expected in LIB.archetypes


def test_normalize_name_front_face_and_case() -> None:
    assert normalize_name("Blood Artist") == "blood artist"
    assert normalize_name("Malakir Rebirth // Malakir Mire") == "malakir rebirth"
    assert normalize_name("  Ephemerate  ") == "ephemerate"


def test_score_deck_sums_signature_weights() -> None:
    # Three aristocrats signatures: 1.0 + 1.0 + 0.8
    deck = ["Blood Artist", "Zulaport Cutthroat", "Viscera Seer", "Llanowar Elves"]
    scores = score_deck(deck, LIB)
    assert scores["aristocrats"] == 2.8
    assert scores["stax"] == 0.0


def test_score_deck_is_case_and_face_insensitive() -> None:
    deck = ["blood artist", "CRUEL CELEBRANT"]
    assert score_deck(deck, LIB)["aristocrats"] == 2.0


def test_classify_deck_multilabel_and_dominant() -> None:
    # Strong aristocrats + one sub-threshold token signal (Cathars' Crusade
    # is weight 0.8 for tokens, below the 1.0 min_score on its own).
    deck = [
        "Blood Artist", "Zulaport Cutthroat", "Cruel Celebrant",
        "Bastion of Remembrance", "Cathars' Crusade",
    ]
    result = classify_deck(deck, LIB)
    assert result.dominant == "aristocrats"
    assert "aristocrats" in result.labels
    assert "tokens" not in result.labels  # 0.8 < min_score


def test_classify_deck_unclassified_below_threshold() -> None:
    result = classify_deck(["Sol Ring", "Arcane Signet", "Llanowar Elves"], LIB)
    assert result.dominant is None
    assert result.labels == []


def test_tags_to_archetypes_maps_creator_tags() -> None:
    assert tags_to_archetypes(["Aristocrats"], LIB) == {"aristocrats"}
    assert tags_to_archetypes(["Sacrifice"], LIB) == {"aristocrats"}
    assert tags_to_archetypes(["Lands Matter"], LIB) == {"landfall"}
    # Unrecognized tags (by design) map to nothing.
    assert tags_to_archetypes(["Control", "Combo"], LIB) == set()


def test_tags_to_archetypes_multi_tag_union() -> None:
    got = tags_to_archetypes(["Tokens", "Sacrifice"], LIB)
    assert got == {"tokens", "aristocrats"}
