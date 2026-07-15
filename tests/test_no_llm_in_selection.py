"""No per-card LLM in the selection hot path (Option A DoD criterion 4).

The deterministic synergy optimizer selects cards; the LLM is a narrator/auditor
only (profile synthesis + deck narrative). These tests prove candidate selection
makes zero card_fit-type calls.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

DB = Path("data/sabermetrics.db")
HAS_DB = DB.exists()


def test_selection_path_has_no_card_fit_calls() -> None:
    """Source guard: the builder no longer wires FitScorer / card_fit."""
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "sabermetrics" / "pipeline" / "deck_builder.py"
    ).read_text()
    for banned in ("FitScorer", "_llm_safety_check", "card_fit", "score_cards"):
        assert banned not in src, f"selection path still references {banned}"


def _canned_profile(commander_id: str, colors: list[str]):
    """Minimal valid CommanderProfile so a build needs no profile-synthesis call."""
    from sabermetrics.models.profile import (
        BehavioralSignals,
        CardAnalysis,
        CommanderProfile,
        CommunitySignals,
        EvidenceFreshness,
        PowerIndicators,
        ProfileSources,
        StrategicConstraints,
        StrategicProfile,
        TopCard,
        UserIntent,
        WinCondition,
    )

    return CommanderProfile(
        commander_id=commander_id,
        commander_name="Test Commander",
        generated_at=datetime.now(),
        set_version="test",
        card_analysis=CardAnalysis(
            mana_cost="",
            color_identity=colors,
            core_mechanic="",
            triggered_abilities=[],
            activated_abilities=[],
            static_abilities=[],
        ),
        behavioral_signals=BehavioralSignals(
            total_decks_tracked=0,
            edhrec_themes=[],
            most_included_cards=[TopCard(card_name="Sol Ring", inclusion_pct=90)],
            average_deck_price_usd=100.0,
            average_cmc=3.0,
        ),
        community_signals=CommunitySignals(
            reddit_thread_count=0, named_archetypes=[], primer_articles_referenced=[]
        ),
        strategic_profile=StrategicProfile(
            primary_archetype="midrange",
            game_plan_summary="Play good cards.",
            win_conditions=[WinCondition(
                description="Combat", key_cards=[], reliability="primary"
            )],
            build_paths=[],
            synergy_priorities={},
            anti_synergies=[],
            strategic_constraints=StrategicConstraints(
                mana_base_requirements="", interaction_density="medium",
                speed_tier="midrange",
            ),
            power_indicators=PowerIndicators(
                estimated_ceiling_bracket=3, estimated_floor_bracket=2, notes=""
            ),
            engine_dependencies=[],
        ),
        user_intent=UserIntent(provided=False),
        sources=ProfileSources(evidence_freshness=EvidenceFreshness()),
    )


@pytest.mark.skipif(not HAS_DB, reason="needs populated card DB")
def test_build_succeeds_with_card_fit_patched_to_raise() -> None:
    """A full build completes and is legal even if card_fit scoring would raise.

    Hermetic (no API): profile synthesis and the deck narrative are stubbed, so
    the only thing exercised is the deterministic selection path. FitScorer's
    card_fit entry point is patched to raise — if selection called it, the build
    would fail. It must return a legal 99-card deck with the mock never invoked.
    """
    from sabermetrics.pipeline.deck_builder import (
        DeckBuilder,
        DeckBuildRequest,
        _BASIC_LAND_NAMES,
    )
    from sabermetrics.reasoning.profiler import ProfileResult

    conn = sqlite3.connect(str(DB))
    row = conn.execute(
        "SELECT id, color_identity FROM cards WHERE is_legal_commander = 1 "
        "AND color_identity != '[]' LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None, "no legal commander in DB"
    import json
    commander_id = row[0]
    colors = json.loads(row[1])

    canned = ProfileResult(
        profile=_canned_profile(commander_id, colors),
        cache_hit=True, generation_cost_usd=0.0, generation_time_seconds=0.0,
    )

    with (
        patch(
            "sabermetrics.reasoning.profiler.ProfileManager.generate_profile",
            return_value=canned,
        ),
        patch(
            "sabermetrics.reasoning.synthesis.DeckSynthesizer.synthesize",
            side_effect=RuntimeError("narrative disabled for test"),
        ),
        patch(
            "sabermetrics.reasoning.fit.FitScorer.score_cards",
            side_effect=AssertionError("card_fit must not be called in selection"),
        ) as fit_mock,
    ):
        result = DeckBuilder(DB).build(
            DeckBuildRequest(commander_id=commander_id, budget_usd=200.0)
        )

    assert fit_mock.call_count == 0
    cards = result.deck.cards
    assert len(cards) == 99, f"expected 99 cards, got {len(cards)}"
    ci = set(result.deck.commander.color_identity)
    seen: set[str] = set()
    for dc in cards:
        nm = dc.card.name
        if nm in _BASIC_LAND_NAMES:
            continue
        assert nm not in seen, f"duplicate nonbasic: {nm}"
        seen.add(nm)
        assert set(dc.card.color_identity) <= ci
