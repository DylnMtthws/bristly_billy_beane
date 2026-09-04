"""The boundary to the ingestion pipeline's mtg_v1 contract."""

from __future__ import annotations

import pytest

from sabermetrics.cedh.adapters_fixture import FixtureMetaRepository
from sabermetrics.cedh.domain import Legality
from sabermetrics.cedh.errors import RepositoryUnavailable, SchemaBoundaryViolation
from sabermetrics.cedh.repositories import (
    CardRepository,
    MetaRepository,
    assert_v1_only,
    chunked,
)


class TestSchemaBoundary:
    """No production query may name an ingestion-internal schema."""

    def test_public_schema_is_allowed(self):
        assert_v1_only("SELECT oracle_id FROM mtg_v1.card_any_medium")

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM mtg_internal.card",
            "select * from MTG_INTERNAL.card_face",
            "SELECT a FROM mtg_v1.card JOIN mtg_internal.card USING (oracle_id)",
        ],
    )
    def test_internal_schema_is_rejected(self, sql):
        with pytest.raises(SchemaBoundaryViolation, match="(?i)mtg_internal"):
            assert_v1_only(sql)

    def test_every_postgres_query_passes_the_guard(self):
        """The guard must be wired into the adapter, not merely available.

        A check nobody calls is worth nothing, so this asserts the adapter's
        one execution path runs it rather than trusting that it does.
        """
        import inspect

        from sabermetrics.cedh import adapters_postgres

        source = inspect.getsource(adapters_postgres._query)
        assert "assert_v1_only(sql)" in source
        # And no adapter method may reach the driver except through _query.
        module_source = inspect.getsource(adapters_postgres)
        assert module_source.count("cur.execute(") == 1

    def test_dsn_query_string_reaches_psycopg_untouched(self, monkeypatch):
        from sabermetrics.cedh import adapters_postgres

        captured = {}

        def fake_connect(dsn, **kwargs):
            captured["dsn"] = dsn
            return object()

        monkeypatch.setattr("psycopg.connect", fake_connect)
        dsn = "postgresql://mtg_consumer@example/mtg?sslmode=require"
        adapters_postgres._connect(dsn)
        assert captured["dsn"] == dsn


class TestFixtureCardRepository:
    def test_satisfies_the_protocol(self, cedh_cards):
        assert isinstance(cedh_cards, CardRepository)

    def test_snapshot_names_the_wide_view(self, cedh_cards):
        """We read card_any_medium, never card.

        mtg_v1.card filters on an is_paper flag derived from the representative
        printing, which silently drops 254 Reserved List cards — Tropical
        Island and Mox Diamond among them, both in this pack.
        """
        assert "card_any_medium" in cedh_cards.snapshot().source_view

    def test_reserved_list_staples_resolve(self, cedh_cards):
        found = cedh_cards.resolve_names(["Tropical Island", "Mox Diamond"])
        assert set(found) == {"Tropical Island", "Mox Diamond"}

    def test_unknown_name_is_absent_not_an_error(self, cedh_cards):
        assert cedh_cards.resolve_names(["Not A Real Card"]) == {}

    def test_lands_have_empty_castable_cmcs(self, cedh_cards):
        """A card that cannot be cast has no castable mana value, not zero.

        The real contract has 1,455 such rows and a consumer taking min()
        would silently read them as free spells.
        """
        island = cedh_cards.resolve_names(["Island"])["Island"]
        assert island.castable_cmcs == ()

    def test_modal_dfc_has_empty_card_level_cost_and_populated_faces(self, cedh_cards):
        card = cedh_cards.resolve_names(["Sink into Stupor"])["Sink into Stupor"]
        assert card.mana_cost == ""
        assert card.face_count == 2
        assert card.faces[0].mana_cost

    def test_front_face_name_resolution(self, cedh_cards):
        card = cedh_cards.resolve_names(["Sink into Stupor"])["Sink into Stupor"]
        assert card.front_name == "Sink into Stupor"

    def test_missing_legality_row_is_unknown_not_illegal(self, cedh_cards):
        """Absent evidence is not evidence of absence.

        'We have no row' and 'the row says not_legal' are different answers and
        the caller renders them differently.
        """
        result = cedh_cards.legality(["no-such-oracle-id"])
        assert result["no-such-oracle-id"] is Legality.UNKNOWN

    def test_known_card_is_legal(self, cedh_cards, kinnan_pack):
        oracle_id = next(iter(kinnan_pack.pool))
        assert cedh_cards.legality([oracle_id])[oracle_id] is Legality.LEGAL


class TestFixtureMetaRepository:
    def test_satisfies_the_protocol(self, cedh_meta_populated):
        assert isinstance(cedh_meta_populated, MetaRepository)

    def test_default_fixture_reproduces_todays_absent_contract(self, cedh_meta_absent):
        state = cedh_meta_absent.availability()
        assert state.available is False
        assert "mtg_v1.tournament" in state.missing_views

    def test_unavailable_reads_raise_rather_than_return_empty(self, cedh_meta_absent):
        """An empty list would read as 'no decks ran this', which is a lie."""
        from datetime import date

        with pytest.raises(RepositoryUnavailable):
            cedh_meta_absent.inclusions("any-key", since=date(2020, 1, 1))

    def test_availability_never_raises(self, cedh_meta_absent):
        assert cedh_meta_absent.availability().available is False

    def test_populated_fixture_answers(self, cedh_meta_populated, kinnan_pack):
        from datetime import date

        key = kinnan_pack.commander.key
        commander = cedh_meta_populated.commander(key)
        assert commander is not None
        assert commander.entries == 42
        facts = cedh_meta_populated.inclusions(key, since=date(2020, 1, 1))
        assert facts and all(f.decks > 0 for f in facts)

    def test_inclusion_rate_always_carries_its_denominator(
        self, cedh_meta_populated, kinnan_pack
    ):
        """A rate is not representable without a sample size."""
        from datetime import date

        from pydantic import ValidationError

        from sabermetrics.cedh.repositories import InclusionFact

        facts = cedh_meta_populated.inclusions(
            kinnan_pack.commander.key, since=date(2020, 1, 1)
        )
        assert facts[0].rate == facts[0].decks_including / facts[0].decks
        with pytest.raises(ValidationError):
            InclusionFact(
                identity_key="k",
                oracle_id="o",
                card_name="c",
                decks_including=5,
                decks=0,
            )

    def test_min_event_size_filters_small_events(self, cedh_meta_populated):
        from datetime import date

        big = cedh_meta_populated.events(since=date(2020, 1, 1), min_size=32)
        assert all(e.size >= 32 for e in big)
        assert "Small Locals" not in {e.name for e in big}

    def test_entries_respect_the_event_size_filter(
        self, cedh_meta_populated, kinnan_pack
    ):
        from datetime import date

        key = kinnan_pack.commander.key
        unfiltered = cedh_meta_populated.entries(key, since=date(2020, 1, 1))
        filtered = cedh_meta_populated.entries(
            key, since=date(2020, 1, 1), min_event_size=32
        )
        assert len(filtered) < len(unfiltered)

    def test_missing_fixture_file_is_loud(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            FixtureMetaRepository(tmp_path)


def test_chunked_bounds_parameter_lists():
    assert chunked([str(i) for i in range(1050)], 500) == [
        [str(i) for i in range(500)],
        [str(i) for i in range(500, 1000)],
        [str(i) for i in range(1000, 1050)],
    ]
    assert chunked([]) == []
