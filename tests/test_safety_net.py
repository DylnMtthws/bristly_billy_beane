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


def test_corroboration_tier_beats_raw_score_when_corpus_active():
    """The Eiganjo case: a high-CVAR zero-corpus text-matcher must lose to
    any corroborated candidate when a reliable corpus exists."""
    candidates = [
        {"name": "Eiganjo Dynastorian", "type_line": "Creature // Sorcery",
         "_cvar_score": 0.90, "_empirical_inclusion": 0.0},
        {"name": "Real Deck Aura", "type_line": "Enchantment — Aura",
         "_cvar_score": 0.45, "_empirical_inclusion": 0.55,
         "_empirical_reliable": True},
    ]
    best = DeckBuilder._best_replacement(
        candidates, set(), corpus_active=True, corroboration_threshold=0.10,
    )
    assert best["name"] == "Real Deck Aura"


def test_uncorroborated_still_eligible_when_nothing_corroborated_fits():
    """Preference, not penalty: with no corroborated candidate affordable,
    the best uncorroborated card is still chosen (absence-neutrality)."""
    candidates = [
        {"name": "Pricey Staple", "type_line": "Creature", "_cvar_score": 0.50,
         "_empirical_inclusion": 0.70, "price_usd": 80.0},
        {"name": "Unknown But Cheap", "type_line": "Creature",
         "_cvar_score": 0.40, "_empirical_inclusion": 0.0, "price_usd": 1.0},
    ]
    best = DeckBuilder._best_replacement(
        candidates, set(), max_price=5.0,
        corpus_active=True, corroboration_threshold=0.10,
    )
    assert best["name"] == "Unknown But Cheap"


def test_without_corpus_tier_is_inert():
    """No corpus -> pure merit ranking, exactly as before."""
    candidates = [
        {"name": "High CVAR Unknown", "type_line": "Creature",
         "_cvar_score": 0.90, "_empirical_inclusion": 0.0},
        {"name": "Low CVAR Unknown", "type_line": "Creature",
         "_cvar_score": 0.30, "_empirical_inclusion": 0.0},
    ]
    best = DeckBuilder._best_replacement(
        candidates, set(), corpus_active=False, corroboration_threshold=0.10,
    )
    assert best["name"] == "High CVAR Unknown"


class _FakeScorer:
    """Stands in for FitScorer; scores by a name->score table and counts calls."""

    calls: list[list[str]] = []
    scores: dict[str, int] = {}

    def __init__(self, db_path):
        pass

    def score_cards_batch(self, cards, **kwargs):
        from types import SimpleNamespace
        _FakeScorer.calls.append([c["name"] for c in cards])
        return [
            (c, SimpleNamespace(
                fit_score=_FakeScorer.scores.get(c["name"], 8),
                reasoning="test",
            ))
            for c in cards
        ]


def test_vet_swap_ins_are_re_vetted_once(monkeypatch, tmp_path):
    """The Eiganjo door: replacements face one re-vet round, not zero, not N.

    Bad Pick (score 2) is replaced by Trap Replacement, which the re-vet also
    fails (score 2) -> replaced again by Safe Aura, accepted without a third
    call. Exactly two batched calls total.
    """
    from types import SimpleNamespace

    import sabermetrics.reasoning.fit as fit_mod
    from sabermetrics.pipeline.deck_builder import DeckBuildRequest
    from sabermetrics.pipeline.trace import GenerationTracer

    _FakeScorer.calls = []
    _FakeScorer.scores = {"Bad Pick": 2, "Trap Replacement": 2}
    monkeypatch.setattr(fit_mod, "FitScorer", _FakeScorer)

    b = DeckBuilder(tmp_path / "unused.db")
    b._tracer = GenerationTracer(generation_id="test")
    b._build_profile_summary = lambda pr: "profile"
    b._empirical = SimpleNamespace(reliable={"anything"})  # corpus active

    deck = [
        SlotAssignment(
            card={"id": "bad", "name": "Bad Pick", "type_line": "Creature",
                  "price_usd": 1.0, "_empirical_inclusion": 0.0},
            slot_role="utility", score=0.2,
        ),
        SlotAssignment(
            card={"id": "good", "name": "Good Pick", "type_line": "Creature",
                  "price_usd": 1.0, "_empirical_inclusion": 0.5},
            slot_role="utility", score=0.8,
        ),
    ]
    # Trap outranks Safe on merit but both are corroborated, so the tier
    # doesn't decide -- the re-vet must catch the trap.
    candidates = [
        {"id": "trap", "name": "Trap Replacement", "type_line": "Creature",
         "price_usd": 1.0, "_cvar_score": 0.9, "_empirical_inclusion": 0.5},
        {"id": "safe", "name": "Safe Aura", "type_line": "Enchantment — Aura",
         "price_usd": 1.0, "_cvar_score": 0.5, "_empirical_inclusion": 0.5},
    ]
    profile_result = SimpleNamespace(profile=SimpleNamespace(
        strategic_profile=SimpleNamespace(primary_archetype="voltron"),
    ))
    request = DeckBuildRequest(commander_id="x", budget_usd=200.0)

    out, _cost = b._llm_safety_check(
        deck, candidates, synergy=None, role_targets=None,
        profile_result=profile_result, request=request, n_weakest=99,
    )

    names = {a.card["name"] for a in out}
    assert "Bad Pick" not in names
    assert "Trap Replacement" not in names  # caught by the re-vet
    assert "Safe Aura" in names
    assert len(_FakeScorer.calls) == 2      # initial vet + one re-vet, no third
    assert _FakeScorer.calls[1] == ["Trap Replacement"]
