"""Shared pytest fixtures."""

import shutil
from datetime import datetime
from pathlib import Path

import pytest

_PROD_DB = Path("data/sabermetrics.db")


@pytest.fixture(scope="session")
def build_db(tmp_path_factory) -> Path:
    """A writable copy of the production DB for end-to-end build tests.

    End-to-end builds persist a generated deck; copying the DB once per session
    keeps the real (symlinked) production database untouched.
    """
    if not _PROD_DB.exists():
        pytest.skip("no populated DB available")
    dst = tmp_path_factory.mktemp("build_db") / "saber.db"
    shutil.copy(str(_PROD_DB.resolve()), str(dst))
    return dst


@pytest.fixture
def canned_profile():
    """Factory: (commander_id, colors) -> a minimal valid ProfileResult.

    Lets end-to-end build tests skip the profile-synthesis LLM call.
    """
    def _make(commander_id: str, colors: list[str]):
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
        from sabermetrics.reasoning.profiler import ProfileResult

        profile = CommanderProfile(
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
                reddit_thread_count=0,
                named_archetypes=[],
                primer_articles_referenced=[],
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
        return ProfileResult(
            profile=profile, cache_hit=True,
            generation_cost_usd=0.0, generation_time_seconds=0.0,
        )

    return _make
