"""Opt-in smoke tests against the live integrations.

None of these run by default. Each is skipped unless its dependency is
configured, and all three are additionally behind markers:

    pytest -m postgres     # needs MTG_V1_DSN pointing at an mtg_consumer role
    pytest -m model        # needs HF_TOKEN; spends real money
    pytest -m simulator    # needs CEDH_SIMULATOR_BIN

The ordinary suite must never need any of them. ``test_default_suite_is_offline``
below asserts that, and it is the test that matters most here: a smoke test that
quietly became mandatory is how a suite stops being runnable on a laptop.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

DSN = os.environ.get("MTG_V1_DSN", "").strip()
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
SIMULATOR_BIN = os.environ.get("CEDH_SIMULATOR_BIN", "").strip()

requires_postgres = pytest.mark.skipif(not DSN, reason="MTG_V1_DSN is not set")
requires_model = pytest.mark.skipif(not HF_TOKEN, reason="HF_TOKEN is not set")
requires_simulator = pytest.mark.skipif(
    not SIMULATOR_BIN, reason="CEDH_SIMULATOR_BIN is not set"
)


@pytest.mark.postgres
@requires_postgres
class TestLivePostgres:
    """Reads the real mtg_v1 contract as mtg_consumer."""

    def test_card_facts_resolve(self):
        from sabermetrics.cedh.adapters_postgres import PostgresCardRepository

        repo = PostgresCardRepository(DSN)
        found = repo.resolve_names(["Kinnan, Bonder Prodigy", "Sol Ring"])
        assert set(found) == {"Kinnan, Bonder Prodigy", "Sol Ring"}
        assert found["Sol Ring"].oracle_id

    def test_reserved_list_staples_are_reachable(self):
        """The default mtg_v1.card view drops these; card_any_medium does not.

        Tropical Island, Mox Diamond and Lotus Petal are cEDH staples and all
        three are Reserved List cards the ingestion repo records as silently
        missing from mtg_v1.card. If this fails, the adapter has been pointed
        at the wrong view.
        """
        from sabermetrics.cedh.adapters_postgres import PostgresCardRepository

        names = ["Tropical Island", "Mox Diamond", "Lotus Petal"]
        found = PostgresCardRepository(DSN).resolve_names(names)
        assert set(found) == set(names)

    def test_front_face_names_resolve_to_joined_names(self):
        from sabermetrics.cedh.adapters_postgres import PostgresCardRepository

        found = PostgresCardRepository(DSN).resolve_names(["Boseiju, Who Endures"])
        assert found

    def test_the_consumer_role_cannot_reach_the_internal_schema(self):
        """The boundary is enforced by the database too, not only by us."""
        import psycopg

        with (
            psycopg.connect(DSN) as conn,
            conn.cursor() as cur,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            cur.execute("SELECT 1 FROM mtg_internal.card LIMIT 1")

    def test_the_shipped_kinnan_pack_resolves_against_the_real_corpus(self):
        """Every one of the pack's 99 names must exist. Missing is a defect."""
        from sabermetrics.cedh.adapters_postgres import PostgresCardRepository
        from sabermetrics.cedh.packs import PackRegistry

        registry = PackRegistry(PostgresCardRepository(DSN))
        failure = registry.unresolved("kinnan_basalt")
        assert failure is None, failure.detail

    def test_meta_availability_is_reported_without_raising(self):
        from sabermetrics.cedh.adapters_postgres import PostgresMetaRepository

        state = PostgresMetaRepository(DSN).availability()
        assert isinstance(state.available, bool)
        if not state.available:
            assert state.missing_views


@pytest.mark.model
@requires_model
class TestLiveModel:
    """Spends real money. One small call."""

    def test_a_structured_response_validates(self, tmp_path):
        from sabermetrics.cedh.model_gateway import (
            ReasoningMode,
            StructuredRequest,
        )
        from sabermetrics.cedh.provider_deepseek import build_gateway
        from sabermetrics.cedh.responses import IntentClassification

        with build_gateway(db_path=str(tmp_path / "smoke.db")) as gateway:
            result = gateway.generate(
                StructuredRequest(
                    prompt_version="smoke.v1",
                    system=("Return the pack_id 'kinnan_basalt' and nothing else."),
                    user="I want a fast Kinnan deck.",
                    schema=IntentClassification,
                    mode=ReasoningMode.CLASSIFY,
                    call_type="cedh:smoke",
                )
            )
        assert isinstance(result.value, IntentClassification)
        assert result.usage.model_id.endswith(":deepinfra")
        assert result.usage.input_tokens > 0
        assert result.usage.latency_ms > 0


@pytest.mark.simulator
@requires_simulator
class TestLiveSimulator:
    """Runs the real binary against a real candidate."""

    def test_a_candidate_round_trips(self, kinnan_pack, cedh_cards):
        from sabermetrics.cedh.builder import build_candidate
        from sabermetrics.cedh.simulator import (
            NotSimulated,
            SubprocessSimulatorClient,
        )

        candidate = build_candidate(kinnan_pack, cedh_cards)
        outcome = SubprocessSimulatorClient(SIMULATOR_BIN, games=200).simulate(
            candidate
        )
        if isinstance(outcome, NotSimulated):
            pytest.fail(
                "the simulator did not accept cedh-deck-candidate.v1: "
                f"{outcome.reason} — {outcome.detail}"
            )
        assert outcome.deck_sha256 == candidate.deck_sha256
        assert outcome.metric == "goldfish_turns_to_assembly"


def test_default_suite_is_offline():
    """The ordinary suite must need no network, database, model or binary.

    Asserted rather than assumed: this is the property that makes the suite
    runnable, and nothing else notices when it stops holding.
    """
    from sabermetrics.cedh.settings import load_cedh_settings

    settings = load_cedh_settings()
    assert settings.simulator.mode == "fixture"

    # The default adapters are the fixture ones whenever MTG_V1_DSN is unset.
    from sabermetrics.cedh import factory

    if not DSN:
        assert factory.build_card_repository(settings)[1] == "fixture"
        assert factory.build_meta_repository(settings)[1] == "fixture"

    fixtures = Path("fixtures/cedh")
    assert (fixtures / "cards.json").exists()
    assert (fixtures / "meta.json").exists()
    assert (fixtures / "simulation" / "kinnan.json").exists()


def test_psycopg_is_optional():
    """The driver is an extra. The default suite must not import it."""
    import sabermetrics.cedh.adapters_postgres as adapters

    source = Path(adapters.__file__).read_text(encoding="utf-8")
    module_level = source.split("def _connect")[0]
    assert "import psycopg" not in module_level


def test_fixture_data_is_labelled_as_synthetic():
    """Nothing downstream may mistake the fixture corpus for the real one."""
    import json

    cards = json.loads(Path("fixtures/cedh/cards.json").read_text())
    assert "SYNTHETIC FIXTURE" in cards["_README"]
    assert cards["snapshot"]["source_view"].startswith("fixture:")

    simulation = json.loads(Path("fixtures/cedh/simulation/kinnan.json").read_text())
    assert "REPRESENTATIVE FIXTURE" in simulation["_README"]


def test_the_fixture_corpus_covers_the_shipped_pack_exactly():
    """The generated corpus and the generated pack must not drift apart.

    Both come from scripts/build_cedh_fixtures.py. If someone edits one by hand
    the offline slice starts failing to resolve, and this says so in one line
    instead of as a pack-resolution error somewhere downstream.
    """
    import json

    import yaml

    pack = yaml.safe_load(
        Path("config/cedh_packs/kinnan_basalt.yaml").read_text(encoding="utf-8")
    )
    corpus = json.loads(Path("fixtures/cedh/cards.json").read_text())
    corpus_names = {card["name"] for card in corpus["cards"]}
    pack_names = {card["name"] for card in pack["cards"]}
    commander_names = set(pack["commander_names"])

    assert pack_names <= corpus_names
    assert commander_names <= corpus_names
    assert corpus_names == pack_names | commander_names
