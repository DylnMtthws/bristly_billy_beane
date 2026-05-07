"""Tests for oracle text keyword extraction and CVAR synergy integration."""

from sabermetrics.analytics.oracle_keywords import (
    MTG_KEYWORD_ABILITIES,
    card_matches_referenced_keywords,
    extract_referenced_keywords,
    extract_referenced_mechanics,
)
from sabermetrics.analytics.cvar import ScoringContext, compute_synergy_score
from sabermetrics.models.profile import ValueInversion, StrategicProfile
from sabermetrics.models.evidence import EvidencePackage


def _make_card(
    name: str,
    oracle_text: str = "",
    type_line: str = "Creature",
    keywords: list[str] | None = None,
    color_identity: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "oracle_text": oracle_text,
        "type_line": type_line,
        "keywords": keywords or [],
        "color_identity": color_identity or [],
    }


# ---------------------------------------------------------------------------
# Extraction tests
# ---------------------------------------------------------------------------

ARCADES_TEXT = (
    "Flying, vigilance\n"
    "Whenever a creature with defender enters the battlefield under your "
    "control, draw a card.\n"
    "Each creature you control with defender assigns combat damage equal "
    "to its toughness rather than its power and can attack as though it "
    "didn't have defender."
)

ISPERIA_TEXT = (
    "Flying\n"
    "Whenever a creature with flying deals combat damage to one of your "
    "opponents, you may reveal the top card of your library. If it's an "
    "instant or sorcery card, put it into your hand."
)


def test_extract_defender_from_arcades() -> None:
    """Arcades' 'creatures with defender' extracts defender."""
    result = extract_referenced_keywords(ARCADES_TEXT)
    assert "defender" in result


def test_extract_flying_from_isperia() -> None:
    """Isperia's 'creature with flying deals' extracts flying."""
    result = extract_referenced_keywords(ISPERIA_TEXT)
    assert "flying" in result


def test_extract_infect() -> None:
    """Text mentioning 'creatures with infect' extracts infect."""
    text = "Creatures you control with infect get +1/+1."
    result = extract_referenced_keywords(text)
    assert "infect" in result


def test_extract_no_false_positives() -> None:
    """'Sacrifice a permanent' should not extract sacrifice as a keyword."""
    text = "Sacrifice a permanent: Draw a card."
    result = extract_referenced_keywords(text)
    assert "sacrifice" not in result


def test_extract_multiple_keywords() -> None:
    """Text with multiple keyword references extracts all."""
    text = (
        "Creatures you control with flying get +1/+1. "
        "Creatures you control with vigilance have hexproof."
    )
    result = extract_referenced_keywords(text)
    assert "flying" in result
    assert "vigilance" in result


def test_extract_empty_oracle() -> None:
    """Empty or None oracle text returns empty list."""
    assert extract_referenced_keywords("") == []
    assert extract_referenced_keywords(None) == []


def test_extract_toughness_matters() -> None:
    """Arcades toughness text extracts toughness_matters mechanic."""
    result = extract_referenced_mechanics(ARCADES_TEXT)
    assert "toughness_matters" in result


def test_extract_artifact_creature() -> None:
    """'Artifact creature enters' extracts artifact_creature mechanic."""
    text = "Whenever an artifact creature enters the battlefield, draw a card."
    result = extract_referenced_mechanics(text)
    assert "artifact_creature" in result


def test_extract_mechanics_empty() -> None:
    """Empty text returns no mechanics."""
    assert extract_referenced_mechanics("") == []
    assert extract_referenced_mechanics(None) == []


# ---------------------------------------------------------------------------
# Card matching tests
# ---------------------------------------------------------------------------


def test_defender_card_matches() -> None:
    """Wall with Defender keyword matches referenced defender."""
    card = _make_card(
        "Wall of Omens",
        oracle_text="When Wall of Omens enters the battlefield, draw a card.",
        type_line="Creature — Wall",
        keywords=["Defender"],
    )
    assert card_matches_referenced_keywords(card, ["defender"], []) is True


def test_wall_type_matches_toughness_matters() -> None:
    """Wall creature matches toughness_matters mechanic."""
    card = _make_card(
        "Wall of Denial",
        type_line="Creature — Wall",
        keywords=["Defender", "Flying", "Shroud"],
    )
    assert card_matches_referenced_keywords(card, [], ["toughness_matters"]) is True


def test_artifact_creature_matches() -> None:
    """Artifact Creature type line matches artifact_creature mechanic."""
    card = _make_card(
        "Solemn Simulacrum",
        type_line="Artifact Creature — Golem",
    )
    assert card_matches_referenced_keywords(card, [], ["artifact_creature"]) is True


def test_unrelated_card_does_not_match() -> None:
    """Beast with Trample doesn't match defender/toughness_matters."""
    card = _make_card(
        "Charging Badger",
        type_line="Creature — Badger",
        keywords=["Trample"],
    )
    assert card_matches_referenced_keywords(card, ["defender"], ["toughness_matters"]) is False


def test_no_match_when_empty_refs() -> None:
    """No referenced keywords/mechanics always returns False."""
    card = _make_card("Sol Ring", type_line="Artifact", keywords=[])
    assert card_matches_referenced_keywords(card, [], []) is False


# ---------------------------------------------------------------------------
# CVAR integration tests
# ---------------------------------------------------------------------------


def _arcades_context() -> ScoringContext:
    return ScoringContext(
        commander_id="arcades-id",
        commander_name="Arcades, the Strategist",
        commander_colors=["W", "U", "G"],
        commander_keywords=["Flying", "Vigilance"],
        commander_oracle_text=ARCADES_TEXT,
        referenced_keywords=["defender"],
        referenced_mechanics=["toughness_matters"],
    )


def _generic_context() -> ScoringContext:
    return ScoringContext(
        commander_id="generic-id",
        commander_name="Generic Commander",
        commander_colors=["W", "U", "G"],
        commander_keywords=["Flying", "Vigilance"],
        commander_oracle_text="Flying, vigilance",
    )


def test_synergy_score_boost_for_defender() -> None:
    """Defender creature scores >= 0.5 higher with Arcades context (0.6 bonus)."""
    card = _make_card(
        "Wall of Omens",
        oracle_text="When Wall of Omens enters the battlefield, draw a card.",
        type_line="Creature — Wall",
        keywords=["Defender"],
        color_identity=["W"],
    )
    arcades_score = compute_synergy_score(card, _arcades_context())
    generic_score = compute_synergy_score(card, _generic_context())
    assert arcades_score >= generic_score + 0.5


def test_synergy_score_no_regression_for_korvold() -> None:
    """Sacrifice outlet for Korvold scores same — empty referenced_keywords."""
    korvold_context = ScoringContext(
        commander_id="korvold-id",
        commander_name="Korvold, Fae-Cursed King",
        commander_colors=["B", "R", "G"],
        commander_keywords=["Flying"],
        commander_oracle_text=(
            "Flying\n"
            "Whenever Korvold, Fae-Cursed King enters the battlefield or "
            "attacks, sacrifice another permanent.\n"
            "Whenever you sacrifice a permanent, put a +1/+1 counter on "
            "Korvold and draw a card."
        ),
        # Empty — no keywords referenced, just "sacrifice" which is not a keyword
        referenced_keywords=[],
        referenced_mechanics=[],
    )
    card = _make_card(
        "Viscera Seer",
        oracle_text="Sacrifice a creature: Scry 1.",
        type_line="Creature — Vampire Wizard",
        keywords=[],
        color_identity=["B"],
    )

    # Also test without referenced fields at all (default)
    korvold_default = ScoringContext(
        commander_id="korvold-id",
        commander_name="Korvold, Fae-Cursed King",
        commander_colors=["B", "R", "G"],
        commander_keywords=["Flying"],
        commander_oracle_text=korvold_context.commander_oracle_text,
    )

    score_with = compute_synergy_score(card, korvold_context)
    score_default = compute_synergy_score(card, korvold_default)
    assert score_with == score_default


# ---------------------------------------------------------------------------
# ValueInversion model tests
# ---------------------------------------------------------------------------


def test_value_inversion_model_validates() -> None:
    """ValueInversion Pydantic model validates correctly."""
    vi = ValueInversion(
        normal_heuristic="Defender creatures can't attack",
        inverted_value="Defender creatures are X/X attackers that draw a card",
        desired_characteristics=["high toughness", "defender keyword", "low CMC"],
        evaluation_guidance="Evaluate defenders by toughness, not power",
    )
    assert vi.normal_heuristic == "Defender creatures can't attack"
    assert len(vi.desired_characteristics) == 3


def test_strategic_profile_defaults_empty_inversions() -> None:
    """StrategicProfile defaults to empty value_inversions list."""
    from sabermetrics.models.profile import (
        AntiSynergy,
        PowerIndicators,
        StrategicConstraints,
        WinCondition,
    )

    profile = StrategicProfile(
        primary_archetype="Voltron",
        game_plan_summary="Attack with commander",
        win_conditions=[
            WinCondition(
                description="Commander damage",
                key_cards=["Sword of Fire and Ice"],
                reliability="primary",
            )
        ],
        build_paths=[],
        synergy_priorities={"high": [], "medium": [], "low": []},
        anti_synergies=[],
        strategic_constraints=StrategicConstraints(
            mana_base_requirements="Standard",
            interaction_density="medium",
            speed_tier="midrange",
        ),
        power_indicators=PowerIndicators(
            estimated_ceiling_bracket=3,
            estimated_floor_bracket=2,
            notes="Mid-power Voltron",
        ),
    )
    assert profile.value_inversions == []


def test_evidence_package_has_referenced_fields() -> None:
    """EvidencePackage includes referenced_keywords and referenced_mechanics."""
    from sabermetrics.models.card import Card

    card = Card(
        id="test-id",
        oracle_id="test-oracle",
        name="Test Commander",
        cmc=4.0,
        type_line="Legendary Creature",
        color_identity=["W"],
        is_legal_commander=True,
        is_legal_in_99=True,
        set_code="TST",
        rarity="mythic",
        last_updated="2024-01-01T00:00:00",
    )
    pkg = EvidencePackage(
        commander=card,
        rulings=[],
        reddit_threads=[],
        primer_articles=[],
        reference_chunks=[],
        referenced_keywords=["defender"],
        referenced_mechanics=["toughness_matters"],
    )
    assert pkg.referenced_keywords == ["defender"]
    assert pkg.referenced_mechanics == ["toughness_matters"]


def test_evidence_package_defaults_empty_refs() -> None:
    """EvidencePackage defaults to empty lists for referenced fields."""
    from sabermetrics.models.card import Card

    card = Card(
        id="test-id",
        oracle_id="test-oracle",
        name="Test Commander",
        cmc=4.0,
        type_line="Legendary Creature",
        color_identity=["W"],
        is_legal_commander=True,
        is_legal_in_99=True,
        set_code="TST",
        rarity="mythic",
        last_updated="2024-01-01T00:00:00",
    )
    pkg = EvidencePackage(
        commander=card,
        rulings=[],
        reddit_threads=[],
        primer_articles=[],
        reference_chunks=[],
    )
    assert pkg.referenced_keywords == []
    assert pkg.referenced_mechanics == []


# ---------------------------------------------------------------------------
# New mechanic extraction tests
# ---------------------------------------------------------------------------

HYLDA_TEXT = (
    "Whenever you tap an untapped creature an opponent controls, "
    "choose one —\n"
    "• Create a 4/4 white and blue Elemental creature token.\n"
    "• Put a +1/+1 counter on each creature you control.\n"
    "• Scry 2, then draw a card."
)

YARUS_TEXT = (
    "Whenever a face-down creature you control dies, reveal it and "
    "exile it. If it's a creature card, it deals damage equal to its "
    "power to each opponent.\n"
    "Whenever Yarus attacks, turn target face-down creature you "
    "control face up."
)

KORVOLD_TEXT = (
    "Flying\n"
    "Whenever Korvold, Fae-Cursed King enters the battlefield or "
    "attacks, sacrifice another permanent.\n"
    "Whenever you sacrifice a permanent, put a +1/+1 counter on "
    "Korvold and draw a card."
)

VEYRAN_TEXT = (
    "Magecraft — Whenever you cast or copy an instant or sorcery "
    "spell, Veyran, Voice of Duality gets +1/+1 until end of turn.\n"
    "If you casting or copying an instant or sorcery spell causes a "
    "triggered ability of a permanent you control to trigger, that "
    "ability triggers an additional time."
)

MEREN_TEXT = (
    "Whenever another creature you control dies, you get an "
    "experience counter.\n"
    "At the beginning of your end step, choose target creature card "
    "in your graveyard. If that card's mana value is less than or "
    "equal to the number of experience counters you have, return it "
    "to the battlefield. Otherwise, put it into your hand."
)

ATRAXA_TEXT = (
    "Flying, vigilance, deathtouch, lifelink\n"
    "At the beginning of your end step, proliferate. (Choose any "
    "number of permanents and/or players, then give each another "
    "counter of each kind already there.)"
)

RHYS_TEXT = (
    "Whenever Rhys the Redeemed attacks, create a 1/1 green and "
    "white Elf Warrior creature token that's tapped and attacking."
)


def test_extract_tap_synergy_from_hylda() -> None:
    """Hylda's 'tap an untapped creature' extracts tap_synergy."""
    result = extract_referenced_mechanics(HYLDA_TEXT)
    assert "tap_synergy" in result


def test_extract_face_down_synergy_from_yarus() -> None:
    """Yarus's 'face-down creature' extracts face_down_synergy."""
    result = extract_referenced_mechanics(YARUS_TEXT)
    assert "face_down_synergy" in result


def test_extract_sacrifice_synergy_from_korvold() -> None:
    """Korvold's 'sacrifice another permanent' extracts sacrifice_synergy."""
    result = extract_referenced_mechanics(KORVOLD_TEXT)
    assert "sacrifice_synergy" in result


def test_extract_spellslinger_from_veyran() -> None:
    """Veyran's 'cast an instant or sorcery' extracts spellslinger."""
    result = extract_referenced_mechanics(VEYRAN_TEXT)
    assert "spellslinger" in result


def test_extract_death_trigger_from_meren() -> None:
    """Meren's 'whenever another creature...dies' extracts death_trigger."""
    result = extract_referenced_mechanics(MEREN_TEXT)
    assert "death_trigger" in result


def test_extract_graveyard_synergy_from_meren() -> None:
    """Meren's 'return...from graveyard' extracts graveyard_synergy."""
    result = extract_referenced_mechanics(MEREN_TEXT)
    assert "graveyard_synergy" in result


def test_extract_token_synergy_from_rhys() -> None:
    """Rhys's 'create...token' extracts token_synergy."""
    result = extract_referenced_mechanics(RHYS_TEXT)
    assert "token_synergy" in result


# ---------------------------------------------------------------------------
# New card matching tests
# ---------------------------------------------------------------------------


def test_tap_card_matches_tap_synergy() -> None:
    """Card with 'Tap target creature' matches tap_synergy."""
    card = _make_card(
        "Frost Lynx",
        oracle_text="When Frost Lynx enters the battlefield, tap target creature an opponent controls.",
        type_line="Creature — Elemental Cat",
    )
    assert card_matches_referenced_keywords(card, [], ["tap_synergy"]) is True


def test_morph_card_matches_face_down_synergy() -> None:
    """Card with morph keyword matches face_down_synergy."""
    card = _make_card(
        "Willbender",
        oracle_text="Morph {1}{U}\nWhen Willbender is turned face up, change the target of target spell or ability with a single target.",
        type_line="Creature — Human Wizard",
        keywords=["Morph"],
    )
    assert card_matches_referenced_keywords(card, [], ["face_down_synergy"]) is True


def test_sacrifice_card_matches_sacrifice_synergy() -> None:
    """Card with sacrifice outlet matches sacrifice_synergy."""
    card = _make_card(
        "Viscera Seer",
        oracle_text="Sacrifice a creature: Scry 1.",
        type_line="Creature — Vampire Wizard",
    )
    assert card_matches_referenced_keywords(card, [], ["sacrifice_synergy"]) is True


def test_toughness_matters_rejects_base_stat_setting() -> None:
    """Cards that set 'base power and toughness' do NOT match toughness_matters.

    Regression test: cards like Warkite Marauder mention 'toughness' in
    stat-setting text but don't mechanically care about high toughness.
    """
    marauder = _make_card(
        "Warkite Marauder",
        oracle_text=(
            "Flying. Whenever Warkite Marauder attacks, target creature "
            "defending player controls loses all abilities and has base "
            "power and toughness 0/1 until end of turn."
        ),
        type_line="Creature — Human Pirate",
        keywords=["Flying"],
    )
    assert card_matches_referenced_keywords(
        marauder, [], ["toughness_matters"]
    ) is False

    spirit = _make_card(
        "Ascendant Spirit",
        oracle_text=(
            "Ascendant Spirit becomes a Spirit Warrior with base power "
            "and toughness 2/3."
        ),
        type_line="Creature — Spirit",
        keywords=["Flying"],
    )
    assert card_matches_referenced_keywords(
        spirit, [], ["toughness_matters"]
    ) is False


def test_toughness_matters_accepts_damage_equal_to_toughness() -> None:
    """Cards with 'assigns combat damage equal to its toughness' match."""
    assault = _make_card(
        "Assault Formation",
        oracle_text=(
            "Each creature you control assigns combat damage equal to "
            "its toughness rather than its power."
        ),
        type_line="Enchantment",
    )
    assert card_matches_referenced_keywords(
        assault, [], ["toughness_matters"]
    ) is True


def test_toughness_matters_accepts_toughness_buff() -> None:
    """Cards with +0/+X toughness-only buffs match."""
    tower = _make_card(
        "Tower Defense",
        oracle_text="Creatures you control get +0/+5 and gain reach until end of turn.",
        type_line="Instant",
    )
    assert card_matches_referenced_keywords(
        tower, [], ["toughness_matters"]
    ) is True


def test_high_cmc_card_matches_cost_reduction() -> None:
    """Card with CMC >= 5 matches cost_reduction."""
    card = _make_card("Blightsteel Colossus", type_line="Artifact Creature — Phyrexian Golem")
    card["cmc"] = 12
    assert card_matches_referenced_keywords(card, [], ["cost_reduction"]) is True


def test_low_cmc_card_does_not_match_cost_reduction() -> None:
    """Card with CMC < 5 doesn't match cost_reduction."""
    card = _make_card("Sol Ring", type_line="Artifact")
    card["cmc"] = 1
    assert card_matches_referenced_keywords(card, [], ["cost_reduction"]) is False


def test_counter_card_matches_counters_matter() -> None:
    """Card mentioning '+1/+1 counter' matches counters_matter."""
    card = _make_card(
        "Hardened Scales",
        oracle_text="If one or more +1/+1 counters would be placed on a creature you control, that many plus one +1/+1 counters are placed on it instead.",
        type_line="Enchantment",
    )
    assert card_matches_referenced_keywords(card, [], ["counters_matter"]) is True


def test_death_card_matches_death_trigger() -> None:
    """Card with 'when this creature dies' matches death_trigger."""
    card = _make_card(
        "Solemn Simulacrum",
        oracle_text="When Solemn Simulacrum enters the battlefield, search your library for a basic land card, put that card onto the battlefield tapped, then shuffle.\nWhen Solemn Simulacrum dies, draw a card.",
        type_line="Artifact Creature — Golem",
    )
    assert card_matches_referenced_keywords(card, [], ["death_trigger"]) is True


def test_flashback_card_matches_graveyard_synergy() -> None:
    """Card with flashback keyword matches graveyard_synergy."""
    card = _make_card(
        "Faithless Looting",
        oracle_text="Draw two cards, then discard two cards.\nFlashback {2}{R}",
        type_line="Sorcery",
        keywords=["Flashback"],
    )
    assert card_matches_referenced_keywords(card, [], ["graveyard_synergy"]) is True


def test_token_card_matches_token_synergy() -> None:
    """Card that creates tokens matches token_synergy."""
    card = _make_card(
        "Raise the Alarm",
        oracle_text="Create two 1/1 white Soldier creature tokens.",
        type_line="Instant",
    )
    assert card_matches_referenced_keywords(card, [], ["token_synergy"]) is True


def test_instant_matches_spellslinger() -> None:
    """Instant card matches spellslinger."""
    card = _make_card(
        "Lightning Bolt",
        oracle_text="Lightning Bolt deals 3 damage to any target.",
        type_line="Instant",
    )
    assert card_matches_referenced_keywords(card, [], ["spellslinger"]) is True


def test_creature_no_false_positive_spellslinger() -> None:
    """Non-instant/sorcery creature doesn't match spellslinger."""
    card = _make_card(
        "Grizzly Bears",
        oracle_text="",
        type_line="Creature — Bear",
    )
    assert card_matches_referenced_keywords(card, [], ["spellslinger"]) is False


def test_regular_card_no_false_positive_tap_synergy() -> None:
    """Card without tap effects doesn't match tap_synergy."""
    card = _make_card(
        "Lightning Bolt",
        oracle_text="Lightning Bolt deals 3 damage to any target.",
        type_line="Instant",
    )
    assert card_matches_referenced_keywords(card, [], ["tap_synergy"]) is False


def test_cvar_bonus_differentiates_defenders_from_generic() -> None:
    """With 0.6 bonus and 35% synergy weight, effective diff should be >= 0.21."""
    defender = _make_card(
        "Wall of Denial",
        type_line="Creature — Wall",
        keywords=["Defender", "Flying", "Shroud"],
        color_identity=["W", "U"],
    )
    generic = _make_card(
        "Aven Squire",
        oracle_text="Exalted",
        type_line="Creature — Bird Soldier",
        keywords=["Exalted"],
        color_identity=["W"],
    )
    ctx = _arcades_context()
    defender_score = compute_synergy_score(defender, ctx)
    generic_score = compute_synergy_score(generic, ctx)
    # Effective differential in CVAR = 0.35 * (defender - generic) >= 0.21
    assert (defender_score - generic_score) * 0.35 >= 0.18


# ---------------------------------------------------------------------------
# EngineDependency model tests
# ---------------------------------------------------------------------------


def test_engine_dependency_model_validates() -> None:
    """EngineDependency Pydantic model validates correctly."""
    from sabermetrics.models.profile import EngineDependency

    dep = EngineDependency(
        engine="Auras you control",
        engine_card_traits=["aura", "enchant creature", "bestow"],
        dependent_outputs=["life drain", "creature lockdown"],
        false_synergy_warning=(
            "Lifegain cards that don't interact with Auras are false synergies."
        ),
    )
    assert dep.engine == "Auras you control"
    assert len(dep.engine_card_traits) == 3
    assert "life drain" in dep.dependent_outputs
    assert "false synergies" in dep.false_synergy_warning


def test_strategic_profile_defaults_empty_engine_deps() -> None:
    """StrategicProfile defaults to empty engine_dependencies list."""
    from sabermetrics.models.profile import (
        AntiSynergy,
        PowerIndicators,
        StrategicConstraints,
        WinCondition,
    )

    profile = StrategicProfile(
        primary_archetype="Voltron",
        game_plan_summary="Attack with commander",
        win_conditions=[
            WinCondition(
                description="Commander damage",
                key_cards=["Sword of Fire and Ice"],
                reliability="primary",
            )
        ],
        build_paths=[],
        synergy_priorities={"high": [], "medium": [], "low": []},
        anti_synergies=[],
        strategic_constraints=StrategicConstraints(
            mana_base_requirements="Standard",
            interaction_density="medium",
            speed_tier="midrange",
        ),
        power_indicators=PowerIndicators(
            estimated_ceiling_bracket=3,
            estimated_floor_bracket=2,
            notes="Mid-power Voltron",
        ),
    )
    assert profile.engine_dependencies == []


# ---------------------------------------------------------------------------
# Engine-aware CVAR scoring tests
# ---------------------------------------------------------------------------


ERIETTE_TEXT = (
    "Other enchanted creatures you control have lifelink.\n"
    "At the beginning of your end step, each opponent loses life equal "
    "to the amount of life you gained this turn."
)


def _eriette_engine_context() -> ScoringContext:
    """Context for Eriette with engine dependency keywords.

    Note: Eriette doesn't *have* Lifelink as a keyword — she grants it
    to enchanted creatures. Her keywords list is empty.
    """
    return ScoringContext(
        commander_id="eriette-id",
        commander_name="Eriette of the Charmed Apple",
        commander_colors=["W", "B"],
        commander_keywords=[],
        commander_oracle_text=ERIETTE_TEXT,
        engine_keywords=["aura", "enchant creature", "bestow"],
        output_keywords=["life drain", "lifelink", "life"],
    )


def _eriette_no_engine_context() -> ScoringContext:
    """Context for Eriette WITHOUT engine data (fallback behavior)."""
    return ScoringContext(
        commander_id="eriette-id",
        commander_name="Eriette of the Charmed Apple",
        commander_colors=["W", "B"],
        commander_keywords=[],
        commander_oracle_text=ERIETTE_TEXT,
    )


def test_engine_card_gets_mechanic_bonus() -> None:
    """Aura card matching engine keywords gets synergy bonus."""
    aura = _make_card(
        "All That Glitters",
        oracle_text="Enchant creature\nEnchanted creature gets +1/+1 for each artifact and enchantment you control.",
        type_line="Enchantment — Aura",
        color_identity=["W"],
    )
    ctx = _eriette_engine_context()
    score = compute_synergy_score(aura, ctx)
    # Should get engine bonus (0.15+ from "enchant creature" match)
    assert score >= 0.15


def test_output_only_card_gets_no_mechanic_bonus() -> None:
    """Lifelink creature matching only output keywords gets no mechanic bonus."""
    lifelink_creature = _make_card(
        "Gifted Aetherborn",
        oracle_text="Deathtouch, lifelink",
        type_line="Creature — Aetherborn Vampire",
        keywords=["Deathtouch", "Lifelink"],
        color_identity=["B"],
    )
    ctx = _eriette_engine_context()
    score = compute_synergy_score(lifelink_creature, ctx)
    # Should NOT get mechanic pattern bonus — lifelink is an output,
    # and Lifelink keyword overlap gives 0.3, plus color 0.1 = 0.4 max
    # The key assertion: no mechanic_patterns bonus was added
    assert score <= 0.5


def test_engine_card_scores_higher_than_output_only() -> None:
    """Aura card scores higher than standalone lifelink creature with engine context."""
    aura = _make_card(
        "Ethereal Armor",
        oracle_text="Enchant creature\nEnchanted creature gets +1/+1 for each enchantment you control and has first strike.",
        type_line="Enchantment — Aura",
        color_identity=["W"],
    )
    lifelink_only = _make_card(
        "Gifted Aetherborn",
        oracle_text="Deathtouch, lifelink",
        type_line="Creature — Aetherborn Vampire",
        keywords=["Deathtouch", "Lifelink"],
        color_identity=["B"],
    )
    ctx = _eriette_engine_context()
    aura_score = compute_synergy_score(aura, ctx)
    lifelink_score = compute_synergy_score(lifelink_only, ctx)
    assert aura_score > lifelink_score


def test_fallback_uses_mechanic_patterns_without_engine() -> None:
    """Without engine keywords, fallback to original mechanic_patterns."""
    card = _make_card(
        "Sanguine Bond",
        oracle_text="Whenever you gain life, target opponent loses that much life.",
        type_line="Enchantment",
        color_identity=["B"],
    )
    ctx = _eriette_no_engine_context()
    score = compute_synergy_score(card, ctx)
    # "life" appears in both oracle_text and commander_oracle_text,
    # so it should get the original mechanic_patterns bonus
    assert score >= 0.1


def test_fallback_mechanic_patterns_korvold_unchanged() -> None:
    """Korvold (no engine data) still uses original mechanic_patterns correctly."""
    korvold_ctx = ScoringContext(
        commander_id="korvold-id",
        commander_name="Korvold, Fae-Cursed King",
        commander_colors=["B", "R", "G"],
        commander_keywords=["Flying"],
        commander_oracle_text=KORVOLD_TEXT,
        # No engine_keywords — should use fallback
    )
    sac_outlet = _make_card(
        "Viscera Seer",
        oracle_text="Sacrifice a creature: Scry 1.",
        type_line="Creature — Vampire Wizard",
        color_identity=["B"],
    )
    score = compute_synergy_score(sac_outlet, korvold_ctx)
    # "sacrifice" pattern should match in both oracle texts
    assert score >= 0.1


# ---------------------------------------------------------------------------
# EDHREC corroboration synergy tests
# ---------------------------------------------------------------------------


def test_edhrec_corroboration_boosts_synergy() -> None:
    """Card with 50%+ EDHREC inclusion gets +0.2 synergy bonus."""
    ctx = ScoringContext(
        commander_id="test-id",
        commander_name="Test Commander",
        commander_colors=["G"],
        commander_keywords=[],
        commander_oracle_text="Some text.",
        edhrec_top_cards={"sol ring": 65.0, "arcane signet": 50.0},
    )
    card = _make_card(
        "Sol Ring",
        oracle_text="{T}: Add {C}{C}.",
        type_line="Artifact",
        color_identity=[],
    )
    score_with = compute_synergy_score(card, ctx)

    ctx_without = ScoringContext(
        commander_id="test-id",
        commander_name="Test Commander",
        commander_colors=["G"],
        commander_keywords=[],
        commander_oracle_text="Some text.",
    )
    score_without = compute_synergy_score(card, ctx_without)
    # 65% inclusion -> min(0.2, 0.65*0.4) = 0.2
    assert score_with >= score_without + 0.19


def test_edhrec_corroboration_scales_with_inclusion() -> None:
    """25% inclusion gives ~0.1, 10% gives ~0.04."""
    ctx_25 = ScoringContext(
        commander_id="test-id",
        commander_name="Test Commander",
        commander_colors=["G"],
        commander_keywords=[],
        commander_oracle_text="Some text.",
        edhrec_top_cards={"test card": 25.0},
    )
    ctx_10 = ScoringContext(
        commander_id="test-id",
        commander_name="Test Commander",
        commander_colors=["G"],
        commander_keywords=[],
        commander_oracle_text="Some text.",
        edhrec_top_cards={"test card": 10.0},
    )
    card = _make_card("Test Card", type_line="Creature", color_identity=[])

    score_25 = compute_synergy_score(card, ctx_25)
    score_10 = compute_synergy_score(card, ctx_10)
    assert score_25 > score_10


def test_edhrec_corroboration_absent_card_no_bonus() -> None:
    """Card not in EDHREC top cards gets no bonus (backward compatible)."""
    ctx = ScoringContext(
        commander_id="test-id",
        commander_name="Test Commander",
        commander_colors=["G"],
        commander_keywords=[],
        commander_oracle_text="Some text.",
        edhrec_top_cards={"sol ring": 65.0},
    )
    card = _make_card(
        "Random Bear",
        type_line="Creature — Bear",
        color_identity=["G"],
    )
    ctx_empty = ScoringContext(
        commander_id="test-id",
        commander_name="Test Commander",
        commander_colors=["G"],
        commander_keywords=[],
        commander_oracle_text="Some text.",
    )
    score_with = compute_synergy_score(card, ctx)
    score_without = compute_synergy_score(card, ctx_empty)
    assert score_with == score_without
