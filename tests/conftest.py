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
    from tests._populated_db import HAS_POPULATED_DB, SKIP_REASON

    if not HAS_POPULATED_DB:
        pytest.skip(SKIP_REASON)
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
                win_conditions=[
                    WinCondition(
                        description="Combat", key_cards=[], reliability="primary"
                    )
                ],
                build_paths=[],
                synergy_priorities={},
                anti_synergies=[],
                strategic_constraints=StrategicConstraints(
                    mana_base_requirements="",
                    interaction_density="medium",
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
            profile=profile,
            cache_hit=True,
            generation_cost_usd=0.0,
            generation_time_seconds=0.0,
        )

    return _make


# --- cEDH Deck Lab fixtures ------------------------------------------------
#
# Every one of these is offline. No network, no Postgres, no model provider and
# no simulator binary is required by any test that uses them.


@pytest.fixture
def cedh_cards():
    """The synthetic card corpus covering the Kinnan pack."""
    from sabermetrics.cedh.adapters_fixture import FixtureCardRepository

    return FixtureCardRepository()


@pytest.fixture
def cedh_meta_absent():
    """A meta repository in today's state: the tournament views do not exist."""
    from sabermetrics.cedh.adapters_fixture import FixtureMetaRepository

    return FixtureMetaRepository()


@pytest.fixture
def cedh_meta_populated():
    """A meta repository in the state the mtg_v1 contract is asked to provide."""
    from sabermetrics.cedh.adapters_fixture import FixtureMetaRepository

    return FixtureMetaRepository(filename="meta_populated.json")


@pytest.fixture
def cedh_registry(cedh_cards):
    """The pack registry resolved against the fixture corpus."""
    from sabermetrics.cedh.packs import PackRegistry

    return PackRegistry(cedh_cards)


@pytest.fixture
def kinnan_pack(cedh_registry):
    """The resolved Kinnan strategy pack."""
    return cedh_registry.get("kinnan_basalt")


@pytest.fixture
def cedh_simulator():
    """The fixture simulator client."""
    from sabermetrics.cedh.simulator import FixtureSimulatorClient

    return FixtureSimulatorClient()


@pytest.fixture(autouse=True)
def _offline_cedh_env(monkeypatch):
    """Keep every test on the fixture adapters, whatever the shell has set.

    ``MTG_V1_DSN`` and ``HF_TOKEN`` switch the cEDH factory to the live
    Postgres and the real model provider. A developer with either exported —
    which is the normal state on the deploy box — would otherwise see tests
    quietly change what they exercise, and the ones asserting "this is running
    on fixtures" would fail for a reason that has nothing to do with the code.

    The opt-in smoke tests read the same variables directly at import time, so
    they are unaffected by this.
    """
    for var in (
        "MTG_V1_DSN",
        "HF_TOKEN",
        "CEDH_SIMULATOR_URL",
        "CEDH_SIMULATOR_TIMEOUT",
        "SABER_AUTH_MODE",
        "SABER_PUBLIC",
    ):
        monkeypatch.delenv(var, raising=False)
    # Several tests invoke the Click CLI, whose entry point loads `.env`. On a
    # machine that has actually been deployed that file sets AUTH_MODE,
    # MTG_V1_DSN and more, so the CLI would put the real deployment's
    # configuration back after this fixture cleared it. Deleting the variables
    # is not enough; the loader has to be told to stay out.
    monkeypatch.setenv("SABER_SKIP_DOTENV", "1")
