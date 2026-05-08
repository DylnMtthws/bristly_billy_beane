"""Tests for hypergeometric role targets (Step 1 of synergy optimizer)."""

from sabermetrics.analytics.role_targets import (
    ROLE_TIMING,
    RoleTarget,
    copies_for_reliability,
    compute_role_targets,
    role_need_multiplier,
)
from sabermetrics.models.profile import (
    BehavioralSignals,
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
from sabermetrics.models.template import DeckTemplate
from datetime import datetime


def _make_profile(
    engine_deps=None,
    core_mechanic="",
    triggered="",
    activated="",
    static="",
) -> CommanderProfile:
    """Helper to build a minimal CommanderProfile for testing."""
    return CommanderProfile(
        commander_id="test-id",
        commander_name="Test Commander",
        generated_at=datetime.now(),
        set_version="test",
        card_analysis=CardAnalysis(
            mana_cost="{2}{B}{R}{G}",
            color_identity=["B", "R", "G"],
            core_mechanic=core_mechanic,
            triggered_abilities=[triggered] if triggered else [],
            activated_abilities=[activated] if activated else [],
            static_abilities=[static] if static else [],
        ),
        behavioral_signals=BehavioralSignals(
            total_decks_tracked=100,
            edhrec_themes=["sacrifice"],
            most_included_cards=[TopCard(card_name="Sol Ring", inclusion_pct=90)],
            average_deck_price_usd=100.0,
            average_cmc=3.5,
        ),
        community_signals=CommunitySignals(
            reddit_thread_count=10,
            named_archetypes=["aristocrats"],
            primer_articles_referenced=[],
        ),
        strategic_profile=StrategicProfile(
            primary_archetype="aristocrats",
            game_plan_summary="Sacrifice creatures for value",
            win_conditions=[
                WinCondition(
                    description="Drain opponents",
                    key_cards=["Blood Artist"],
                    reliability="primary",
                )
            ],
            build_paths=[],
            synergy_priorities={"sacrifice": ["sac outlet", "death trigger"]},
            anti_synergies=[],
            strategic_constraints=StrategicConstraints(
                mana_base_requirements="3 colors",
                interaction_density="medium",
                speed_tier="midrange",
            ),
            power_indicators=PowerIndicators(
                estimated_ceiling_bracket=3,
                estimated_floor_bracket=2,
                notes="Solid midrange",
            ),
            engine_dependencies=engine_deps or [],
        ),
        user_intent=UserIntent(provided=False),
        sources=ProfileSources(
            evidence_freshness=EvidenceFreshness(),
        ),
    )


def _make_template(**kwargs) -> DeckTemplate:
    """Helper to build a DeckTemplate with defaults."""
    defaults = {
        "land_count": 36,
        "ramp_count": 10,
        "draw_count": 8,
        "removal_count": 7,
        "board_wipe_count": 2,
        "differentiator_slots": 30,
        "curve_shape": {0: 1, 1: 8, 2: 14, 3: 12, 4: 8, 5: 5, 6: 3, 7: 2},
    }
    defaults.update(kwargs)
    return DeckTemplate(**defaults)


# --- copies_for_reliability ---

def test_copies_for_reliability_known_values() -> None:
    """Verify against hand-calculated hypergeometric values.

    For a 99-card deck, seeing 10 cards (turn 4):
    P(>=1 of k copies) = 1 - C(99-k, 10) / C(99, 10)
    k=8 → P ≈ 0.588
    k=10 → P ≈ 0.694
    k=12 → P ≈ 0.779
    k=14 → P ≈ 0.844
    So for 80% reliability at 10 cards seen, need ~13 copies.
    """
    result = copies_for_reliability(cards_seen=10, target_probability=0.80)
    assert 11 <= result <= 15, f"Expected ~13, got {result}"


def test_copies_for_reliability_edge_zero() -> None:
    """Zero probability needs zero copies."""
    assert copies_for_reliability(cards_seen=10, target_probability=0.0) == 0


def test_copies_for_reliability_high_probability() -> None:
    """Near-certain requires many copies."""
    result = copies_for_reliability(cards_seen=7, target_probability=0.99)
    assert result > 20


def test_copies_for_reliability_many_cards_seen() -> None:
    """Seeing many cards requires fewer copies."""
    few = copies_for_reliability(cards_seen=7, target_probability=0.80)
    many = copies_for_reliability(cards_seen=15, target_probability=0.80)
    assert many < few, "More cards seen should require fewer copies"


# --- role_need_multiplier ---

def test_role_need_multiplier_curve() -> None:
    """Verify the sigmoid-like step function shape."""
    # Critically underserved
    assert role_need_multiplier(0, 10) == 1.8
    assert role_need_multiplier(3, 10) == 1.8

    # Underserved
    assert role_need_multiplier(6, 10) == 1.4

    # Almost there
    assert role_need_multiplier(8, 10) == 1.15

    # At target
    assert role_need_multiplier(10, 10) == 0.85

    # Redundant
    assert role_need_multiplier(13, 10) == 0.6

    # Heavily over-committed
    assert role_need_multiplier(20, 10) == 0.3

    # Zero target
    assert role_need_multiplier(5, 0) == 0.5


# --- compute_role_targets ---

def test_compute_role_targets_uses_template_floors() -> None:
    """Template ramp_count should be the floor for ramp target."""
    profile = _make_profile()
    template = _make_template(ramp_count=15)
    targets = compute_role_targets(profile, template)
    assert targets["ramp"].target_count >= 15


def test_engine_critical_roles_get_higher_target() -> None:
    """Engine dependencies should boost reliability → higher target."""
    # Without engine dep
    profile_base = _make_profile()
    template = _make_template()
    targets_base = compute_role_targets(profile_base, template)

    # With engine dep referencing "draw"
    profile_engine = _make_profile(
        engine_deps=[
            EngineDependency(
                engine="Card draw engine",
                engine_card_traits=["draw triggers", "draw payoff"],
                dependent_outputs=["storm count"],
                false_synergy_warning="Don't add draw without payoffs",
            )
        ]
    )
    targets_engine = compute_role_targets(profile_engine, template)

    assert targets_engine["draw"].is_engine_critical
    assert targets_engine["draw"].reliability > targets_base["draw"].reliability


def test_commander_draw_reduces_draw_target() -> None:
    """Commander with built-in draw should reduce draw target."""
    # Commander without draw
    profile_no_draw = _make_profile()
    # Commander with draw
    profile_draw = _make_profile(
        triggered="Whenever a creature dies, draw a card"
    )

    template = _make_template()
    targets_no_draw = compute_role_targets(profile_no_draw, template)
    targets_draw = compute_role_targets(profile_draw, template)

    assert targets_draw["draw"].reliability < targets_no_draw["draw"].reliability


def test_all_roles_have_targets() -> None:
    """Every role in ROLE_TIMING should appear in output."""
    profile = _make_profile()
    template = _make_template()
    targets = compute_role_targets(profile, template)
    for role in ROLE_TIMING:
        assert role in targets
        assert targets[role].target_count > 0
        assert targets[role].min_count >= 0
        assert targets[role].max_count >= targets[role].target_count
