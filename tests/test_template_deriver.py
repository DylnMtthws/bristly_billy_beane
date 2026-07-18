"""Tests for template derivation (6.5.3)."""


from sabermetrics.models.template import DeckTemplate, SlotIntent
from sabermetrics.reasoning.template_deriver import (
    derive_deck_template,
    _estimate_creature_density,
    _parse_commander_cmc,
)


def _make_mock_profile():
    """Create a minimal CommanderProfile for testing."""
    from sabermetrics.models.profile import (
        CardAnalysis,
        BehavioralSignals,
        CommunitySignals,
        CommanderProfile,
        EvidenceFreshness,
        PowerIndicators,
        ProfileSources,
        StrategicConstraints,
        StrategicProfile,
        TopCard,
        UserIntent,
        WinCondition,
    )
    from datetime import datetime

    return CommanderProfile(
        commander_id="test-id",
        commander_name="Test Commander",
        generated_at=datetime.now(),
        set_version="TST",
        card_analysis=CardAnalysis(
            mana_cost="{2}{B}{R}",
            color_identity=["B", "R"],
            core_mechanic="Sacrifice creatures for value",
            triggered_abilities=["Whenever a creature dies"],
            activated_abilities=[],
            static_abilities=[],
        ),
        behavioral_signals=BehavioralSignals(
            total_decks_tracked=500,
            edhrec_themes=["Sacrifice", "Aristocrats"],
            most_included_cards=[TopCard(card_name="Sol Ring", inclusion_pct=99.0)],
            average_deck_price_usd=100.0,
            average_cmc=3.0,
        ),
        community_signals=CommunitySignals(
            reddit_thread_count=10,
            named_archetypes=["Aristocrats"],
            primer_articles_referenced=[],
        ),
        strategic_profile=StrategicProfile(
            primary_archetype="Aristocrats sacrifice",
            game_plan_summary="Sacrifice creatures for incremental value.",
            win_conditions=[
                WinCondition(
                    description="Drain opponents through death triggers",
                    key_cards=["Blood Artist"],
                    reliability="primary",
                ),
            ],
            build_paths=[],
            synergy_priorities={"sacrifice": ["sac outlet", "death trigger"]},
            anti_synergies=[],
            strategic_constraints=StrategicConstraints(
                mana_base_requirements="Standard BR mana base",
                interaction_density="medium",
                speed_tier="midrange",
            ),
            power_indicators=PowerIndicators(
                estimated_ceiling_bracket=4,
                estimated_floor_bracket=2,
                notes="Solid aristocrats",
            ),
        ),
        user_intent=UserIntent(provided=False),
        sources=ProfileSources(
            evidence_freshness=EvidenceFreshness(),
        ),
    )


def test_deck_template_sums_to_99() -> None:
    """DeckTemplate infrastructure + differentiator should sum to 99."""
    template = DeckTemplate(
        land_count=36,
        ramp_count=10,
        draw_count=8,
        removal_count=6,
        board_wipe_count=2,
        differentiator_slots=37,
        avg_cmc_target=3.0,
    )
    total = (
        template.land_count + template.ramp_count + template.draw_count
        + template.removal_count + template.board_wipe_count
        + template.differentiator_slots
    )
    assert total == 99


def test_deck_template_to_composition() -> None:
    """to_composition() produces dict summing to 99."""
    template = DeckTemplate(
        land_count=36,
        ramp_count=10,
        draw_count=8,
        removal_count=6,
        board_wipe_count=2,
        differentiator_slots=37,
        avg_cmc_target=3.0,
    )
    comp = template.to_composition()
    assert sum(comp.values()) == 99


def test_slot_intent_model() -> None:
    """SlotIntent model validates correctly."""
    intent = SlotIntent(
        category="sacrifice_outlet",
        priority=0.8,
        current_count=1,
        target_count=3,
        slots_to_fill=2,
    )
    assert intent.slots_to_fill == 2


def test_derive_template_basic() -> None:
    """Template derivation produces valid template."""
    profile = _make_mock_profile()
    template = derive_deck_template(profile, budget=200.0, power_target=3)

    assert 30 <= template.land_count <= 42
    assert 5 <= template.ramp_count <= 18
    assert 3 <= template.draw_count <= 15
    assert 3 <= template.removal_count <= 15
    assert template.differentiator_slots >= 10

    total = (
        template.land_count + template.ramp_count + template.draw_count
        + template.removal_count + template.board_wipe_count
        + template.differentiator_slots
    )
    assert total == 99


def test_derive_template_high_power() -> None:
    """Higher power should mean more ramp and fewer lands."""
    profile = _make_mock_profile()
    t3 = derive_deck_template(profile, power_target=3)
    t5 = derive_deck_template(profile, power_target=5)

    assert t5.ramp_count >= t3.ramp_count
    assert t5.land_count <= t3.land_count


def test_parse_commander_cmc() -> None:
    """Commander CMC parsing from mana cost string."""
    profile = _make_mock_profile()
    assert _parse_commander_cmc(profile) == 4  # {2}{B}{R}


def test_estimate_creature_density() -> None:
    """Creature density varies by archetype."""
    from sabermetrics.models.profile import (
        PowerIndicators, StrategicConstraints, StrategicProfile,
    )

    sp_tribal = StrategicProfile(
        primary_archetype="Tribal aggro",
        game_plan_summary="",
        win_conditions=[],
        build_paths=[],
        synergy_priorities={},
        anti_synergies=[],
        strategic_constraints=StrategicConstraints(
            mana_base_requirements="", interaction_density="low", speed_tier="fast",
        ),
        power_indicators=PowerIndicators(
            estimated_ceiling_bracket=3, estimated_floor_bracket=1, notes="",
        ),
    )
    sp_spells = StrategicProfile(
        primary_archetype="Spellslinger storm",
        game_plan_summary="",
        win_conditions=[],
        build_paths=[],
        synergy_priorities={},
        anti_synergies=[],
        strategic_constraints=StrategicConstraints(
            mana_base_requirements="", interaction_density="low", speed_tier="fast",
        ),
        power_indicators=PowerIndicators(
            estimated_ceiling_bracket=3, estimated_floor_bracket=1, notes="",
        ),
    )
    assert _estimate_creature_density(sp_tribal) > _estimate_creature_density(sp_spells)


# --- Empirical composition grounding ---


def _make_composition(**overrides):
    from sabermetrics.analytics.empirical_valuation import EmpiricalComposition

    defaults = dict(
        lands=36, enchantments=36, creatures=18, artifacts=8,
        auras=27, avg_cmc=2.5,
    )
    defaults.update(overrides)
    return EmpiricalComposition(**defaults)


def test_template_uses_corpus_composition() -> None:
    """With a reliable corpus, lands/avg-CMC/type targets come from it.

    The Eriette case: the formula estimated ~3.0+ avg CMC from power target
    alone and built 39 lands; real decks run a 2.5 curve with 36 lands and 36
    enchantments.
    """
    profile = _make_mock_profile()
    comp = _make_composition()

    t = derive_deck_template(profile, empirical_composition=comp)

    assert t.avg_cmc_target == 2.5
    # Karsten(2.5) is ~34; corpus median 36 is within the +/-3 clamp.
    assert t.land_count == 36
    assert t.type_targets == {"enchantment": 36, "creature": 18, "artifact": 8}


def test_corpus_land_count_is_clamped_to_karsten_band() -> None:
    """An outlier corpus median can't drag lands beyond Karsten +/-3."""
    profile = _make_mock_profile()
    comp = _make_composition(lands=45, avg_cmc=2.5)

    t = derive_deck_template(profile, empirical_composition=comp)

    from sabermetrics.pipeline.mana_base import target_land_count
    assert t.land_count == min(42, target_land_count(2.5) + 3)


def test_no_corpus_leaves_type_targets_none() -> None:
    t = derive_deck_template(_make_mock_profile())
    assert t.type_targets is None


def test_cheap_curve_scales_ramp_down() -> None:
    """A 2.4-curve deck wants less acceleration than a 3.4-curve deck."""
    profile = _make_mock_profile()
    cheap = derive_deck_template(
        profile, empirical_composition=_make_composition(avg_cmc=2.4)
    )
    pricey = derive_deck_template(
        profile, empirical_composition=_make_composition(avg_cmc=3.4)
    )
    assert cheap.ramp_count < pricey.ramp_count
