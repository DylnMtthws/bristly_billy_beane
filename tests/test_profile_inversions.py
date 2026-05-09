"""Tests for profile value-inversion quality (Phase 6.5.9).

Validates that the profile schema correctly handles value inversions,
engine dependencies, and mispriced card examples for known commanders.
Uses hand-crafted fixture profiles, not LLM outputs.
"""

import json
from pathlib import Path

import pytest

from sabermetrics.models.profile import (
    CommanderProfile,
    ValueInversion,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "profiles"


def _load_profile(name: str) -> CommanderProfile:
    """Load and parse a fixture profile JSON."""
    path = FIXTURES_DIR / f"{name}.json"
    data = json.loads(path.read_text())
    return CommanderProfile(**data)


# --- Arcades, the Strategist: stat + keyword inversions ---


class TestArcadesInversions:
    """Arcades inverts toughness→power and defender→valuable."""

    @pytest.fixture()
    def profile(self) -> CommanderProfile:
        return _load_profile("arcades_the_strategist")

    def test_has_value_inversions(self, profile: CommanderProfile) -> None:
        assert len(profile.strategic_profile.value_inversions) >= 1

    def test_toughness_or_defender_in_desired(
        self, profile: CommanderProfile
    ) -> None:
        all_desired = []
        for inv in profile.strategic_profile.value_inversions:
            all_desired.extend(
                trait.lower() for trait in inv.desired_characteristics
            )
        has_toughness = any("toughness" in d for d in all_desired)
        has_defender = any("defender" in d for d in all_desired)
        assert has_toughness or has_defender

    def test_power_in_undesired(self, profile: CommanderProfile) -> None:
        all_undesired = []
        for inv in profile.strategic_profile.value_inversions:
            all_undesired.extend(
                trait.lower() for trait in inv.undesired_characteristics
            )
        assert any("power" in u for u in all_undesired)

    def test_has_mispriced_cards(self, profile: CommanderProfile) -> None:
        assert len(profile.strategic_profile.mispriced_card_examples) >= 3

    def test_undesired_characteristics_populated(
        self, profile: CommanderProfile
    ) -> None:
        """At least one inversion has undesired_characteristics."""
        has_undesired = any(
            len(inv.undesired_characteristics) > 0
            for inv in profile.strategic_profile.value_inversions
        )
        assert has_undesired


# --- Eriette of the Charmed Apple: engine + cost inversion ---


class TestErietteInversions:
    """Eriette has an Aura engine dependency and cost inversion."""

    @pytest.fixture()
    def profile(self) -> CommanderProfile:
        return _load_profile("eriette_of_the_charmed_apple")

    def test_has_engine_dependencies(self, profile: CommanderProfile) -> None:
        assert len(profile.strategic_profile.engine_dependencies) >= 1

    def test_engine_has_aura_traits(self, profile: CommanderProfile) -> None:
        all_traits = []
        for dep in profile.strategic_profile.engine_dependencies:
            all_traits.extend(
                trait.lower() for trait in dep.engine_card_traits
            )
        assert any("aura" in t for t in all_traits)

    def test_has_value_inversions(self, profile: CommanderProfile) -> None:
        assert len(profile.strategic_profile.value_inversions) >= 1

    def test_aura_cost_in_inversion(self, profile: CommanderProfile) -> None:
        """At least one inversion references aura cost evaluation."""
        all_guidance = " ".join(
            inv.evaluation_guidance.lower()
            for inv in profile.strategic_profile.value_inversions
        )
        assert "mana" in all_guidance or "cost" in all_guidance or "cheap" in all_guidance

    def test_has_mispriced_cards(self, profile: CommanderProfile) -> None:
        assert len(profile.strategic_profile.mispriced_card_examples) >= 3


# --- Krenko, Mob Boss: quantity inversion ---


class TestKrenkoInversions:
    """Krenko inverts quantity vs quality — many small > few large."""

    @pytest.fixture()
    def profile(self) -> CommanderProfile:
        return _load_profile("krenko_mob_boss")

    def test_has_value_inversions(self, profile: CommanderProfile) -> None:
        assert len(profile.strategic_profile.value_inversions) >= 1

    def test_token_or_goblin_in_desired(
        self, profile: CommanderProfile
    ) -> None:
        all_desired = []
        for inv in profile.strategic_profile.value_inversions:
            all_desired.extend(
                trait.lower() for trait in inv.desired_characteristics
            )
        has_token = any("token" in d for d in all_desired)
        has_goblin = any("goblin" in d for d in all_desired)
        assert has_token or has_goblin

    def test_undesired_characteristics_populated(
        self, profile: CommanderProfile
    ) -> None:
        has_undesired = any(
            len(inv.undesired_characteristics) > 0
            for inv in profile.strategic_profile.value_inversions
        )
        assert has_undesired

    def test_has_mispriced_cards(self, profile: CommanderProfile) -> None:
        assert len(profile.strategic_profile.mispriced_card_examples) >= 3


# --- The Ur-Dragon: conventional (no inversions) ---


class TestUrDragonConventional:
    """Ur-Dragon is a conventional tribal lord — no inversions expected."""

    @pytest.fixture()
    def profile(self) -> CommanderProfile:
        return _load_profile("the_ur_dragon")

    def test_no_value_inversions(self, profile: CommanderProfile) -> None:
        assert len(profile.strategic_profile.value_inversions) == 0

    def test_no_or_minimal_mispriced_cards(
        self, profile: CommanderProfile
    ) -> None:
        assert len(profile.strategic_profile.mispriced_card_examples) <= 1

    def test_game_plan_mentions_standard_eval(
        self, profile: CommanderProfile
    ) -> None:
        """Conventional commander should note standard evaluation applies."""
        summary = profile.strategic_profile.game_plan_summary.lower()
        assert (
            "standard" in summary
            or "conventional" in summary
            or "heuristic" in summary
            or "quality" in summary
        )


# --- Model validation tests ---


class TestValueInversionModel:
    """Tests for the ValueInversion Pydantic model."""

    def test_value_inversion_with_undesired(self) -> None:
        """New undesired_characteristics field populates correctly."""
        inv = ValueInversion(
            normal_heuristic="Power matters most",
            inverted_value="Toughness is the damage stat",
            desired_characteristics=["high toughness"],
            undesired_characteristics=["high power", "power-based abilities"],
            evaluation_guidance="Evaluate by toughness, not power",
        )
        assert inv.undesired_characteristics == [
            "high power",
            "power-based abilities",
        ]

    def test_value_inversion_backward_compatible(self) -> None:
        """Old profiles without undesired_characteristics still parse."""
        inv = ValueInversion(
            normal_heuristic="Power matters most",
            inverted_value="Toughness is the damage stat",
            desired_characteristics=["high toughness"],
            evaluation_guidance="Evaluate by toughness, not power",
        )
        assert inv.undesired_characteristics == []

    def test_profile_round_trip_with_inversions(self) -> None:
        """Serialize/deserialize preserves all inversion fields."""
        profile = _load_profile("arcades_the_strategist")

        # Round-trip through JSON
        json_str = profile.model_dump_json()
        restored = CommanderProfile.model_validate_json(json_str)

        # Verify inversions survived
        assert len(restored.strategic_profile.value_inversions) == len(
            profile.strategic_profile.value_inversions
        )
        for orig, rest in zip(
            profile.strategic_profile.value_inversions,
            restored.strategic_profile.value_inversions,
        ):
            assert orig.normal_heuristic == rest.normal_heuristic
            assert orig.desired_characteristics == rest.desired_characteristics
            assert (
                orig.undesired_characteristics
                == rest.undesired_characteristics
            )
            assert orig.evaluation_guidance == rest.evaluation_guidance

    def test_profile_round_trip_empty_inversions(self) -> None:
        """Round-trip works for profiles with no inversions."""
        profile = _load_profile("the_ur_dragon")

        json_str = profile.model_dump_json()
        restored = CommanderProfile.model_validate_json(json_str)

        assert restored.strategic_profile.value_inversions == []
        assert restored.strategic_profile.mispriced_card_examples == []
