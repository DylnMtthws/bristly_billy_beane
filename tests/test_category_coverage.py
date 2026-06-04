"""Tests for category coverage analysis (6.5.6)."""

import json
from datetime import datetime


from sabermetrics.models.profile import (
    BehavioralSignals,
    BuildPath,
    CardAnalysis,
    CommanderProfile,
    CommunitySignals,
    EngineDependency,
    EvidenceFreshness,
    PowerIndicators,
    ProfileSources,
    StrategicConstraints,
    StrategicProfile,
    TopCard,
    UserIntent,
    WinCondition,
)
from sabermetrics.models.template import SlotIntent
from sabermetrics.pipeline.category_coverage import (
    _infer_from_archetype,
    _trait_to_category,
    analyze_category_coverage,
)


def _make_profile(
    archetype: str = "aristocrats",
    synergy_priorities: dict | None = None,
    engine_dependencies: list[EngineDependency] | None = None,
) -> CommanderProfile:
    """Create a minimal CommanderProfile for testing."""
    return CommanderProfile(
        commander_id="test-cmdr",
        commander_name="Test Commander",
        generated_at=datetime.now(),
        set_version="TEST",
        card_analysis=CardAnalysis(
            mana_cost="{2}{B}{W}",
            color_identity=["W", "B"],
            core_mechanic="sacrifice",
            triggered_abilities=["death trigger"],
            activated_abilities=[],
            static_abilities=[],
        ),
        behavioral_signals=BehavioralSignals(
            total_decks_tracked=100,
            edhrec_themes=["aristocrats"],
            most_included_cards=[TopCard(card_name="Sol Ring", inclusion_pct=95.0)],
            average_deck_price_usd=80.0,
            average_cmc=3.0,
        ),
        community_signals=CommunitySignals(
            reddit_thread_count=10,
            named_archetypes=["aristocrats"],
            primer_articles_referenced=[],
        ),
        strategic_profile=StrategicProfile(
            primary_archetype=archetype,
            game_plan_summary="Sacrifice creatures for value.",
            win_conditions=[
                WinCondition(
                    description="Drain opponents",
                    key_cards=["Blood Artist"],
                    reliability="primary",
                )
            ],
            build_paths=[
                BuildPath(
                    name="Aristocrats",
                    description="Classic sacrifice",
                    consensus_status="mainstream",
                    key_card_categories=["sacrifice"],
                )
            ],
            synergy_priorities=synergy_priorities or {},
            anti_synergies=[],
            strategic_constraints=StrategicConstraints(
                mana_base_requirements="Standard",
                interaction_density="medium",
                speed_tier="midrange",
            ),
            power_indicators=PowerIndicators(
                estimated_ceiling_bracket=3,
                estimated_floor_bracket=2,
                notes="Mid-power",
            ),
            engine_dependencies=engine_dependencies or [],
        ),
        user_intent=UserIntent(provided=False),
        sources=ProfileSources(
            evidence_freshness=EvidenceFreshness(),
        ),
    )


def _make_partial_deck(categories_per_card: list[list[str]]) -> list[dict]:
    """Create a partial deck with functional_categories set."""
    deck = []
    for i, cats in enumerate(categories_per_card):
        deck.append({
            "id": f"card-{i}",
            "name": f"Test Card {i}",
            "functional_categories": json.dumps(cats),
            "role_tags": '["utility"]',
            "_cvar_score": 0.5,
        })
    return deck


# --- trait_to_category tests ---


def test_trait_to_category_direct_match() -> None:
    """Direct trait matches return correct category."""
    assert _trait_to_category("sacrifice") == "sacrifice_outlet"
    assert _trait_to_category("sac outlet") == "sacrifice_outlet"
    assert _trait_to_category("death trigger") == "death_trigger"
    assert _trait_to_category("etb") == "etb_payoff"
    assert _trait_to_category("aura") == "aura"
    assert _trait_to_category("equipment") == "equipment"
    assert _trait_to_category("token") == "token_generation"
    assert _trait_to_category("recursion") == "recursion"


def test_trait_to_category_case_insensitive() -> None:
    """Trait matching should be case-insensitive."""
    assert _trait_to_category("SACRIFICE") == "sacrifice_outlet"
    assert _trait_to_category("Sac Outlet") == "sacrifice_outlet"
    assert _trait_to_category("ETB") == "etb_payoff"


def test_trait_to_category_substring_match() -> None:
    """Substring matches work for partial trait names."""
    assert _trait_to_category("sacrifice themed") == "sacrifice_outlet"
    assert _trait_to_category("great etb synergy") == "etb_payoff"


def test_trait_to_category_unknown() -> None:
    """Unknown traits return None."""
    assert _trait_to_category("flying") is None
    assert _trait_to_category("trample") is None
    assert _trait_to_category("hexproof") is None


# --- infer_from_archetype tests ---


def test_infer_from_archetype_aristocrats() -> None:
    """Aristocrat archetype infers sacrifice + death triggers."""
    result = _infer_from_archetype("aristocrats")
    assert "sacrifice_outlet" in result
    assert "death_trigger" in result
    assert result["sacrifice_outlet"] >= 0.8


def test_infer_from_archetype_enchantress() -> None:
    """Enchantress archetype infers aura priority."""
    result = _infer_from_archetype("Aura Enchantress")
    assert "aura" in result
    assert result["aura"] >= 0.8


def test_infer_from_archetype_unknown() -> None:
    """Unknown archetype gets generic defaults."""
    result = _infer_from_archetype("random unknown")
    assert len(result) > 0  # Should still return something


# --- analyze_category_coverage tests ---


def test_coverage_with_synergy_priorities() -> None:
    """Synergy priorities drive category coverage."""
    profile = _make_profile(
        synergy_priorities={
            "sacrifice": ["sac outlet", "death trigger"],
            "tokens": ["token"],
        }
    )
    intents = analyze_category_coverage(
        profile=profile,
        partial_deck=[],
        remaining_slots=30,
        remaining_budget=100.0,
    )
    assert len(intents) > 0
    categories = [i.category for i in intents]
    assert "sacrifice_outlet" in categories
    assert "death_trigger" in categories


def test_coverage_with_engine_dependencies() -> None:
    """Engine dependencies create high-priority categories."""
    profile = _make_profile(
        engine_dependencies=[
            EngineDependency(
                engine="Auras you control",
                engine_card_traits=["aura", "enchant creature"],
                dependent_outputs=["life drain"],
                false_synergy_warning="Lifegain without Auras",
            )
        ]
    )
    intents = analyze_category_coverage(
        profile=profile,
        partial_deck=[],
        remaining_slots=30,
        remaining_budget=100.0,
    )
    categories = [i.category for i in intents]
    assert "aura" in categories
    # Engine dependencies should have high priority
    aura_intent = next(i for i in intents if i.category == "aura")
    assert aura_intent.priority >= 0.8


def test_coverage_counts_existing_cards() -> None:
    """Existing functional categories reduce slots_to_fill."""
    profile = _make_profile(
        synergy_priorities={"sacrifice": ["sac outlet"]},
    )
    # Deck already has 3 sacrifice outlets
    partial_deck = _make_partial_deck([
        ["sacrifice_outlet"],
        ["sacrifice_outlet"],
        ["sacrifice_outlet"],
    ])
    intents = analyze_category_coverage(
        profile=profile,
        partial_deck=partial_deck,
        remaining_slots=30,
        remaining_budget=100.0,
    )
    # With 3 already present, high-priority target (3-4) should be mostly satisfied
    sac_intents = [i for i in intents if i.category == "sacrifice_outlet"]
    if sac_intents:
        assert sac_intents[0].slots_to_fill <= 1


def test_coverage_archetype_fallback() -> None:
    """When no synergy_priorities or engine_deps, archetype inference kicks in."""
    profile = _make_profile(
        archetype="blink",
        synergy_priorities={},
        engine_dependencies=[],
    )
    intents = analyze_category_coverage(
        profile=profile,
        partial_deck=[],
        remaining_slots=30,
        remaining_budget=100.0,
    )
    categories = [i.category for i in intents]
    assert "flicker" in categories
    assert "etb_payoff" in categories


def test_coverage_does_not_exceed_remaining_slots() -> None:
    """Total allocated slots should not exceed remaining_slots."""
    profile = _make_profile(
        synergy_priorities={
            "sacrifice": ["sac outlet", "death trigger"],
            "tokens": ["token"],
            "recursion": ["recursion", "reanimate"],
        }
    )
    remaining = 10
    intents = analyze_category_coverage(
        profile=profile,
        partial_deck=[],
        remaining_slots=remaining,
        remaining_budget=100.0,
    )
    total_allocated = sum(i.slots_to_fill for i in intents)
    assert total_allocated <= remaining


def test_coverage_returns_slot_intents() -> None:
    """All returned items are SlotIntent instances."""
    profile = _make_profile(
        synergy_priorities={"sacrifice": ["sac outlet"]},
    )
    intents = analyze_category_coverage(
        profile=profile,
        partial_deck=[],
        remaining_slots=30,
        remaining_budget=100.0,
    )
    assert all(isinstance(i, SlotIntent) for i in intents)


def test_coverage_json_string_categories() -> None:
    """Functional categories stored as JSON strings are parsed correctly."""
    profile = _make_profile(
        synergy_priorities={"tokens": ["token"]},
    )
    partial_deck = [
        {
            "id": "token-maker",
            "name": "Token Maker",
            "functional_categories": '["token_generation", "sacrifice_outlet"]',
            "role_tags": '["utility"]',
        }
    ]
    intents = analyze_category_coverage(
        profile=profile,
        partial_deck=partial_deck,
        remaining_slots=30,
        remaining_budget=100.0,
    )
    token_intents = [i for i in intents if i.category == "token_generation"]
    if token_intents:
        assert token_intents[0].current_count == 1


def test_coverage_empty_partial_deck() -> None:
    """Empty partial deck means all categories start at 0."""
    profile = _make_profile(
        synergy_priorities={"sacrifice": ["sac outlet"]},
    )
    intents = analyze_category_coverage(
        profile=profile,
        partial_deck=[],
        remaining_slots=30,
        remaining_budget=100.0,
    )
    for intent in intents:
        assert intent.current_count == 0
