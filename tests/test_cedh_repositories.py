"""The boundary to the ingestion pipeline's mtg_v1 contract."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from sabermetrics.cedh.adapters_fixture import FixtureMetaRepository
from sabermetrics.cedh.domain import Legality
from sabermetrics.cedh.errors import SchemaBoundaryViolation
from sabermetrics.cedh.repositories import (
    CardRepository,
    MetaEvidenceLimits,
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

    def test_default_fixture_reports_unavailable_contract(self, cedh_meta_absent):
        state = cedh_meta_absent.availability()
        assert state.available is False
        assert "mtg_v1.tournament" in state.summary_detail

    def test_unavailable_is_not_an_empty_cohort(self, cedh_meta_absent):
        """Unavailable inclusion is None; a valid empty query is an empty tuple."""
        from datetime import date

        unavailable = cedh_meta_absent.load_evidence("any-key", since=date(2020, 1, 1))
        assert unavailable.availability.summaries_available is False
        assert unavailable.inclusions is None

    def test_availability_never_raises(self, cedh_meta_absent):
        assert cedh_meta_absent.availability().available is False

    def test_populated_fixture_derives_windowed_summary(
        self, cedh_meta_populated, kinnan_pack
    ):
        from datetime import date

        key = kinnan_pack.commander.key
        result = cedh_meta_populated.load_evidence(
            key, since=date(2026, 3, 1), min_event_size=32
        )
        commander = result.commander
        assert commander is not None
        assert commander.entries == 3
        assert commander.events == 3
        assert commander.oracle_ids == (key,)
        facts = result.inclusions
        assert facts is not None
        assert facts and all(f.decks > 0 for f in facts)

    def test_inclusion_rate_always_carries_its_denominator(
        self, cedh_meta_populated, kinnan_pack
    ):
        """A rate is not representable without a sample size."""
        from datetime import date

        from pydantic import ValidationError

        from sabermetrics.cedh.repositories import InclusionFact

        result = cedh_meta_populated.load_evidence(
            kinnan_pack.commander.key,
            since=date(2026, 3, 1),
            min_event_size=32,
        )
        facts = result.inclusions
        assert facts is not None
        assert facts[0].rate == facts[0].decks_including / facts[0].decks
        with pytest.raises(ValidationError):
            InclusionFact(
                identity_key="k",
                oracle_id="o",
                card_name="c",
                decks_including=5,
                decks=0,
            )

    def test_date_window_and_event_size_filter_the_whole_cohort(
        self, cedh_meta_populated, kinnan_pack
    ):
        from datetime import date

        key = kinnan_pack.commander.key
        result = cedh_meta_populated.load_evidence(
            key, since=date(2026, 8, 1), min_event_size=100
        )
        assert result.commander is not None
        assert result.commander.entries == 1
        assert {event.name for event in result.events} == {"Eminence Open 12"}
        assert result.inclusion_denominator == 1

    def test_null_deck_event_wins_and_top_cuts_are_derived_honestly(
        self, cedh_meta_populated, kinnan_pack
    ):
        from datetime import date

        result = cedh_meta_populated.load_evidence(
            kinnan_pack.commander.key,
            since=date(2026, 3, 1),
            min_event_size=32,
        )
        commander = result.commander
        assert commander is not None
        assert commander.entries == 3  # the null-deck entry is not in the cohort
        assert sum(entry.wins for entry in result.entries) == 15
        assert commander.wins == 1  # standing=1, not the 15 match wins
        assert commander.top_cuts == 1  # unknown top_cut never counts

    def test_inclusion_uses_distinct_decks_and_keeps_incomplete_decks(
        self, cedh_meta_populated, kinnan_pack
    ):
        from datetime import date

        result = cedh_meta_populated.load_evidence(
            kinnan_pack.commander.key,
            since=date(2026, 3, 1),
            min_event_size=32,
        )
        assert result.inclusion_denominator == 3
        assert result.incomplete_decks == 1
        assert result.inclusions is not None
        assert {fact.card_name: fact.decks_including for fact in result.inclusions} == {
            "Basalt Monolith": 2,
            "Sol Ring": 2,
        }
        assert all(fact.decks == 3 for fact in result.inclusions)

    def test_partner_identity_is_derived_from_two_commander_rows(
        self, cedh_meta_populated
    ):
        from datetime import date

        result = cedh_meta_populated.load_evidence(
            "partner-a+partner-b", since=date(2026, 3, 1), min_event_size=32
        )
        assert result.commander is not None
        assert result.commander.oracle_ids == ("partner-a", "partner-b")
        assert result.commander.names == ("Thrasios, Triton Hero", "Tymna the Weaver")

    def test_display_limits_do_not_change_aggregate_counts(
        self, cedh_meta_populated, kinnan_pack
    ):
        from datetime import date

        result = cedh_meta_populated.load_evidence(
            kinnan_pack.commander.key,
            since=date(2026, 3, 1),
            min_event_size=32,
            limits=MetaEvidenceLimits(events=1, entries=1, inclusions=1),
        )
        assert result.commander is not None
        assert result.commander.entries == 3
        assert result.commander.events == 3
        assert (
            len(result.entries)
            == len(result.events)
            == len(result.inclusions or ())
            == 1
        )

    def test_valid_empty_cohort_remains_distinct_from_unavailable(
        self, cedh_meta_populated
    ):
        from datetime import date

        result = cedh_meta_populated.load_evidence(
            "not-present", since=date(2026, 3, 1), min_event_size=32
        )
        assert result.availability.summaries_available is True
        assert result.commander is None
        assert result.inclusions == ()
        assert result.inclusion_denominator == 0

    def test_summary_survives_missing_inclusion_capability(
        self, cedh_meta_populated, kinnan_pack, tmp_path
    ):
        import json
        from datetime import date
        from pathlib import Path

        raw = json.loads(
            Path("fixtures/cedh/meta_populated.json").read_text(encoding="utf-8")
        )
        raw["availability"]["inclusion_available"] = False
        raw["availability"]["inclusion_detail"] = "mtg_v1.deck_card: permission denied"
        (tmp_path / "partial.json").write_text(json.dumps(raw), encoding="utf-8")
        repo = FixtureMetaRepository(tmp_path, filename="partial.json")
        result = repo.load_evidence(
            kinnan_pack.commander.key,
            since=date(2026, 3, 1),
            min_event_size=32,
        )
        assert result.commander is not None
        assert result.commander.entries == 3
        assert result.inclusions is None
        assert result.availability.inclusion_available is False

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


class TestPostgresContractProbes:
    class _Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def transaction(self):
            return TestPostgresContractProbes._Transaction()

    def test_probes_execute_required_columns_and_permissions(self, monkeypatch):
        import psycopg

        from sabermetrics.cedh import adapters_postgres

        executed = []

        def fake_query(_conn, sql, _params=None):
            executed.append(sql)
            if "FROM mtg_v1.deck_card" in sql:
                raise psycopg.errors.InsufficientPrivilege("permission denied")
            return []

        monkeypatch.setattr(adapters_postgres, "_query", fake_query)
        state = adapters_postgres.PostgresMetaRepository._probe_availability(
            self._Connection()
        )
        assert state.summaries_available is True
        assert state.inclusion_available is False
        assert "permission denied" in state.inclusion_detail
        assert all("WHERE FALSE" in sql for sql in executed)
        assert not any("information_schema" in sql for sql in executed)
        joined = " ".join(executed)
        for column in (
            "tournament_id",
            "event_date",
            "player_count",
            "deck_id",
            "commander_identity",
            "submitted_name",
            "board",
        ):
            assert column in joined

    def test_missing_summary_column_does_not_look_like_an_empty_query(
        self, monkeypatch
    ):
        import psycopg

        from sabermetrics.cedh import adapters_postgres

        def fake_query(_conn, sql, _params=None):
            if "FROM mtg_v1.tournament WHERE FALSE" in sql:
                raise psycopg.errors.UndefinedColumn("event_date is missing")
            return []

        monkeypatch.setattr(adapters_postgres, "_query", fake_query)
        state = adapters_postgres.PostgresMetaRepository._probe_availability(
            self._Connection()
        )
        assert state.summaries_available is False
        assert "event_date is missing" in state.summary_detail

    @pytest.mark.parametrize(
        ("event_date", "expected_held_on"),
        [
            pytest.param(
                datetime.fromisoformat("2026-08-30T18:45:12.123456+00:00"),
                date(2026, 8, 30),
                id="non-midnight-utc",
            ),
            pytest.param(
                datetime.fromisoformat("2026-08-30T00:30:00+14:00"),
                date(2026, 8, 29),
                id="previous-utc-date",
            ),
            pytest.param(
                datetime.fromisoformat("2026-08-30T23:30:00-07:00"),
                date(2026, 8, 31),
                id="next-utc-date",
            ),
            pytest.param(date(2026, 8, 30), date(2026, 8, 30), id="date-preserved"),
            pytest.param(None, None, id="none-preserved"),
        ],
    )
    def test_real_producer_shape_maps_through_every_evidence_query(
        self, monkeypatch, event_date, expected_held_on
    ):
        from sabermetrics.cedh import adapters_postgres

        connection = self._Connection()
        connects = []
        executed = []

        def fake_connect(_dsn):
            connects.append(connection)
            return connection

        def fake_query(_conn, sql, params=None):
            assert _conn is connection
            executed.append((sql, params or {}))
            if "WHERE FALSE" in sql or sql.startswith("SET TRANSACTION"):
                return []
            if "COUNT(*) AS n, MAX(event_date)" in sql:
                return [{"n": 1, "latest": event_date}]
            if "COUNT(DISTINCT entry_id) AS entries" in sql:
                return [
                    {
                        "entries": 1,
                        "events": 1,
                        "event_wins": 1,
                        "top_cuts": 1,
                        "decks": 1,
                        "incomplete_decks": 1,
                    }
                ]
            if "MIN(dc.submitted_name)" in sql:
                return [
                    {
                        "oracle_id": "commander-id",
                        "submitted_name": "Commander Name",
                        "position": 0,
                    }
                ]
            if "SELECT DISTINCT tournament_id AS event_id" in sql:
                return [
                    {
                        "event_id": "tournament-id",
                        "name": "Event Name",
                        "held_on": event_date,
                        "size": 64,
                        "source": "EDHTop16",
                        "source_url": "https://example.test/event",
                    }
                ]
            if "deck_id AS decklist_id" in sql:
                return [
                    {
                        "entry_id": "entry-id",
                        "event_id": "tournament-id",
                        "identity_key": "commander-id",
                        "standing": 1,
                        "wins": 4,
                        "losses": 1,
                        "draws": 0,
                        "decklist_id": "deck-id",
                    }
                ]
            if "COUNT(DISTINCT c.deck_id) AS decks_including" in sql:
                return [
                    {
                        "oracle_id": "card-id",
                        "card_name": "Card Name",
                        "decks_including": 1,
                    }
                ]
            raise AssertionError(f"unexpected SQL: {sql}")

        monkeypatch.setattr(adapters_postgres, "_connect", fake_connect)
        monkeypatch.setattr(adapters_postgres, "_query", fake_query)
        result = adapters_postgres.PostgresMetaRepository("dsn").load_evidence(
            "commander-id", since=date(2026, 1, 1), min_event_size=32
        )

        assert len(connects) == 1
        assert result.commander is not None
        assert result.commander.names == ("Commander Name",)
        assert result.events[0].event_id == "tournament-id"
        assert result.events[0].held_on == expected_held_on
        assert result.events[0].size == 64
        assert result.events[0].source_url == "https://example.test/event"
        assert result.entries[0].decklist_id == "deck-id"
        assert result.inclusions is not None
        assert result.inclusions[0].decks == 1
        sql = " ".join(statement for statement, _ in executed)
        assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" in sql
        assert "d.commander_identity = %(key)s" in sql
        assert "t.event_date >= %(since)s" in sql
        assert "t.player_count >= %(min_size)s" in sql
        assert "tournament_id AS event_id" in sql
        assert "event_date AS held_on" in sql
        assert "player_count AS size" in sql
        assert "url AS source_url" in sql
        assert "deck_id AS decklist_id" in sql
        assert "commander_identity AS identity_key" in sql
        assert "dc.board = 'mainboard'" in sql
        assert "is_complete IS FALSE" in sql

    def test_probe_does_not_swallow_programming_errors(self, monkeypatch):
        from sabermetrics.cedh import adapters_postgres

        def broken_query(_conn, _sql, _params=None):
            raise TypeError("adapter bug")

        monkeypatch.setattr(adapters_postgres, "_query", broken_query)
        with pytest.raises(TypeError, match="adapter bug"):
            adapters_postgres.PostgresMetaRepository._probe_availability(
                self._Connection()
            )
