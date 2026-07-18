"""Tests for LLM safety-net targeting and replacement selection.

SME review of the Eriette build found four bad creatures (Tallowisp, Yiazmat,
Lost Auramancers at 0.00 corpus inclusion; Underworld Coinsmith at 0.14) that
the weakest-N review never examined because their greedy scores were decent.
"""

from sabermetrics.pipeline.deck_builder import DeckBuilder
from sabermetrics.pipeline.slot_assigner import SlotAssignment


def _pair(i, name, score, inclusion):
    card = {"name": name, "_empirical_inclusion": inclusion}
    return (i, SlotAssignment(card=card, slot_role="utility", score=score))


def test_uncorroborated_picks_reviewed_before_weak_ones():
    """A high-scoring zero-corpus card outranks a weak corroborated one."""
    indexed = [
        _pair(0, "Weak But Corroborated", 0.30, 0.55),
        _pair(1, "Tallowisp", 0.70, 0.00),       # strong score, no corpus
        _pair(2, "Yiazmat", 0.60, 0.00),
        _pair(3, "Strong Staple", 0.90, 0.80),
    ]
    ordered = DeckBuilder._safety_review_order(
        indexed, corpus_active=True, threshold=0.10
    )
    names = [a.card["name"] for _, a in ordered]
    # Uncorroborated first (weakest of them first), then corroborated by score.
    assert names == [
        "Yiazmat", "Tallowisp", "Weak But Corroborated", "Strong Staple",
    ]


def test_without_corpus_order_is_plain_weakest_first():
    indexed = [
        _pair(0, "B", 0.70, 0.0),
        _pair(1, "A", 0.30, 0.0),
    ]
    ordered = DeckBuilder._safety_review_order(
        indexed, corpus_active=False, threshold=0.10
    )
    assert [a.card["name"] for _, a in ordered] == ["A", "B"]


def test_best_replacement_prefers_corroborated_quality():
    """Replacement is chosen on merit, not list position."""
    candidates = [
        {"name": "First In List", "type_line": "Creature", "_cvar_score": 0.40},
        {"name": "Corpus Staple", "type_line": "Creature", "_cvar_score": 0.50,
         "_empirical_inclusion": 0.70, "_empirical_reliable": True},
        {"name": "Already In Deck", "type_line": "Creature", "_cvar_score": 0.99},
        {"name": "A Land", "type_line": "Land", "_cvar_score": 0.99},
    ]
    best = DeckBuilder._best_replacement(candidates, {"Already In Deck"})
    assert best["name"] == "Corpus Staple"
