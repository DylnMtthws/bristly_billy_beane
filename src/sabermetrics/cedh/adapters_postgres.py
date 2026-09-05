"""Postgres adapters for the ``mtg_v1`` contract.

Connects as ``mtg_consumer`` and reads ``mtg_v1`` only. ``psycopg`` is imported
lazily so the ordinary test suite — which uses the fixture adapters — needs
neither the driver nor a database.

Tournament evidence is derived here from the ingestion service's atomic views.
The producer owns source facts; Deck Lab owns the commander/window cohort and
its aggregates.  Summary and inclusion capabilities are probed separately so
a missing ``deck_card`` grant, for example, does not hide valid finishes.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from sabermetrics.cedh.domain import Legality
from sabermetrics.cedh.errors import RepositoryUnavailable
from sabermetrics.cedh.repositories import (
    CardFace,
    CardFacts,
    CorpusSnapshot,
    InclusionFact,
    MetaAvailability,
    MetaCommander,
    MetaEntry,
    MetaEvent,
    MetaEvidenceLimits,
    MetaEvidenceSlice,
    assert_v1_only,
    chunked,
)

logger = logging.getLogger(__name__)

#: The card view we read. NOT ``mtg_v1.card`` — see :class:`CardFacts`.
CARD_VIEW = "mtg_v1.card_any_medium"
FACE_VIEW = "mtg_v1.card_face"
LEGALITY_VIEW = "mtg_v1.card_legality"

#: Executable contract checks.  Unlike an ``information_schema`` name lookup,
#: these validate required columns and the consumer role's SELECT permission.
SUMMARY_CONTRACT_CHECKS = (
    (
        "mtg_v1.tournament",
        "SELECT tournament_id, source, name, event_date, url, player_count, "
        "top_cut FROM mtg_v1.tournament WHERE FALSE",
    ),
    (
        "mtg_v1.tournament_entry",
        "SELECT entry_id, tournament_id, deck_id, standing, wins, losses, draws "
        "FROM mtg_v1.tournament_entry WHERE FALSE",
    ),
    (
        "mtg_v1.deck",
        "SELECT deck_id, commander_identity, is_complete "
        "FROM mtg_v1.deck WHERE FALSE",
    ),
    (
        "mtg_v1.deck_commander",
        "SELECT deck_id, oracle_id, submitted_name, position "
        "FROM mtg_v1.deck_commander WHERE FALSE",
    ),
)
INCLUSION_CONTRACT_CHECKS = (
    (
        "mtg_v1.deck_card",
        "SELECT deck_id, oracle_id, board FROM mtg_v1.deck_card WHERE FALSE",
    ),
    (
        "mtg_v1.card_any_medium",
        "SELECT oracle_id, name FROM mtg_v1.card_any_medium WHERE FALSE",
    ),
)

_CONTRACT_SQLSTATES = {
    "3F000",  # invalid_schema_name
    "42501",  # insufficient_privilege
    "42703",  # undefined_column
    "42P01",  # undefined_table
}


def _dsn() -> str:
    """Return the consumer DSN from the environment.

    Raises:
        RepositoryUnavailable: If no DSN is configured.
    """
    dsn = os.environ.get("MTG_V1_DSN", "").strip()
    if not dsn:
        raise RepositoryUnavailable(
            "MTG_V1_DSN is not set. It must be an mtg_consumer connection "
            "string to the ingestion pipeline's Postgres."
        )
    return dsn


def _connect(dsn: str | None = None) -> Any:
    """Open a psycopg connection with dict rows, importing the driver lazily."""
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - exercised only with driver absent
        raise RepositoryUnavailable(
            "psycopg is not installed. Install the 'postgres' extra to use the "
            "live mtg_v1 adapters: pip install -e '.[postgres]'"
        ) from exc
    return psycopg.connect(dsn or _dsn(), row_factory=dict_row)


def _query(conn: Any, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
    """Execute a read against the public contract.

    Every statement passes :func:`assert_v1_only` first, so a query naming an
    ingestion-internal schema fails here rather than depending on the database
    role being correctly restricted.
    """
    assert_v1_only(sql)
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        return list(cur.fetchall()) if cur.description is not None else []


def _tuple(value: Any) -> tuple:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


# rep_prices is deliberately NOT selected. cEDH is proxy-normal, so price is
# not a performance signal; not fetching it is what keeps it out of selection.
_CARD_COLUMNS = """
    oracle_id, name, layout, mana_cost, mana_value, type_line, oracle_text,
    colors, color_identity, keywords, all_types, castable_cmcs,
    has_land_face, face_count, content_updated_at
"""


class PostgresCardRepository:
    """:class:`~sabermetrics.cedh.repositories.CardRepository` over ``mtg_v1``."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn
        self._snapshot: CorpusSnapshot | None = None

    # -- internals --------------------------------------------------------

    def _rows(self, sql: str, params: dict[str, Any]) -> list[dict]:
        with _connect(self._dsn) as conn:
            return _query(conn, sql, params)

    def _faces(self, oracle_ids: Sequence[str]) -> dict[str, list[CardFace]]:
        if not oracle_ids:
            return {}
        out: dict[str, list[CardFace]] = {}
        for batch in chunked(oracle_ids):
            rows = self._rows(
                f"""
                SELECT oracle_id, face_index, name, mana_cost, face_mana_value,
                       type_line, oracle_text
                FROM {FACE_VIEW}
                WHERE oracle_id = ANY(%(ids)s)
                ORDER BY oracle_id, face_index
                """,
                {"ids": list(batch)},
            )
            for row in rows:
                out.setdefault(str(row["oracle_id"]), []).append(
                    CardFace(
                        face_index=row["face_index"],
                        name=row["name"],
                        mana_cost=row.get("mana_cost"),
                        face_mana_value=row.get("face_mana_value"),
                        type_line=row.get("type_line"),
                        oracle_text=row.get("oracle_text"),
                    )
                )
        return out

    @staticmethod
    def _to_facts(row: dict, faces: Sequence[CardFace]) -> CardFacts:
        return CardFacts(
            oracle_id=str(row["oracle_id"]),
            name=row["name"],
            layout=row.get("layout") or "",
            mana_cost=row.get("mana_cost"),
            mana_value=float(row.get("mana_value") or 0.0),
            type_line=row.get("type_line") or "",
            oracle_text=row.get("oracle_text"),
            colors=_tuple(row.get("colors")),
            color_identity=_tuple(row.get("color_identity")),
            keywords=_tuple(row.get("keywords")),
            all_types=_tuple(row.get("all_types")),
            castable_cmcs=tuple(float(c) for c in _tuple(row.get("castable_cmcs"))),
            has_land_face=bool(row.get("has_land_face")),
            face_count=int(row.get("face_count") or 0),
            faces=tuple(faces),
            content_updated_at=row.get("content_updated_at"),
        )

    # -- CardRepository ---------------------------------------------------

    def snapshot(self) -> CorpusSnapshot:
        if self._snapshot is None:
            rows = self._rows(
                f"SELECT COUNT(*) AS n, MAX(content_updated_at) AS latest "
                f"FROM {CARD_VIEW}",
                {},
            )
            row = rows[0] if rows else {}
            self._snapshot = CorpusSnapshot(
                source_view=CARD_VIEW,
                row_count=row.get("n"),
                max_content_updated_at=row.get("latest"),
                captured_at=datetime.now(UTC),
            )
        return self._snapshot

    def get_by_oracle_ids(self, oracle_ids: Sequence[str]) -> dict[str, CardFacts]:
        ids = list(dict.fromkeys(oracle_ids))
        if not ids:
            return {}
        rows: list[dict] = []
        for batch in chunked(ids):
            rows.extend(
                self._rows(
                    f"SELECT {_CARD_COLUMNS} FROM {CARD_VIEW} "
                    f"WHERE oracle_id = ANY(%(ids)s)",
                    {"ids": list(batch)},
                )
            )
        faces = self._faces([str(r["oracle_id"]) for r in rows])
        return {
            str(r["oracle_id"]): self._to_facts(r, faces.get(str(r["oracle_id"]), []))
            for r in rows
        }

    def resolve_names(self, names: Sequence[str]) -> dict[str, CardFacts]:
        wanted = list(dict.fromkeys(names))
        if not wanted:
            return {}
        rows: list[dict] = []
        for batch in chunked(wanted):
            rows.extend(
                self._rows(
                    f"""
                    SELECT {_CARD_COLUMNS} FROM {CARD_VIEW}
                    WHERE name = ANY(%(names)s)
                       OR split_part(name, ' // ', 1) = ANY(%(names)s)
                    """,
                    {"names": list(batch)},
                )
            )
        faces = self._faces([str(r["oracle_id"]) for r in rows])
        by_name: dict[str, CardFacts] = {}
        for row in rows:
            facts = self._to_facts(row, faces.get(str(row["oracle_id"]), []))
            by_name[facts.name] = facts
            by_name.setdefault(facts.front_name, facts)
        return {name: by_name[name] for name in wanted if name in by_name}

    def legality(
        self, oracle_ids: Sequence[str], fmt: str = "commander"
    ) -> dict[str, Legality]:
        ids = list(dict.fromkeys(oracle_ids))
        out: dict[str, Legality] = dict.fromkeys(ids, Legality.UNKNOWN)
        for batch in chunked(ids):
            for row in self._rows(
                f"SELECT oracle_id, status FROM {LEGALITY_VIEW} "
                f"WHERE format = %(fmt)s AND oracle_id = ANY(%(ids)s)",
                {"fmt": fmt, "ids": list(batch)},
            ):
                try:
                    out[str(row["oracle_id"])] = Legality(row["status"])
                except ValueError:
                    out[str(row["oracle_id"])] = Legality.UNKNOWN
        return out


class PostgresMetaRepository:
    """:class:`~sabermetrics.cedh.repositories.MetaRepository` over ``mtg_v1``.

    All evidence for a package is loaded on one connection inside a read-only,
    repeatable-read transaction. Aggregate counts are computed over the full
    cohort before display limits are applied.
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn

    @staticmethod
    def _is_contract_error(exc: Exception) -> bool:
        """Return whether ``exc`` is an expected schema/grant contract failure."""
        try:
            import psycopg
        except ImportError:  # pragma: no cover - _connect reports this first
            return False
        return isinstance(exc, psycopg.Error) and getattr(exc, "sqlstate", None) in (
            _CONTRACT_SQLSTATES
        )

    @staticmethod
    def _is_connection_error(exc: Exception) -> bool:
        """Return whether ``exc`` means Postgres could not serve the read."""
        try:
            import psycopg
        except ImportError:  # pragma: no cover - _connect reports this first
            return False
        return isinstance(exc, psycopg.OperationalError)

    @classmethod
    def _probe_group(
        cls, conn: Any, checks: Sequence[tuple[str, str]]
    ) -> tuple[str, ...]:
        """Execute a capability's checks, isolating expected failures by savepoint."""
        failures: list[str] = []
        for source, sql in checks:
            try:
                # This is a savepoint when called inside load_evidence's outer
                # transaction, keeping a missing optional view from aborting it.
                with conn.transaction():
                    _query(conn, sql)
            except Exception as exc:
                if not cls._is_contract_error(exc):
                    raise
                failures.append(f"{source}: {exc}")
        return tuple(failures)

    @classmethod
    def _probe_availability(cls, conn: Any) -> MetaAvailability:
        summary_failures = cls._probe_group(conn, SUMMARY_CONTRACT_CHECKS)
        inclusion_failures = cls._probe_group(conn, INCLUSION_CONTRACT_CHECKS)
        summaries_available = not summary_failures
        inclusion_available = summaries_available and not inclusion_failures
        if summary_failures:
            inclusion_detail = (
                "card inclusion requires the tournament-summary capability"
            )
            if inclusion_failures:
                inclusion_detail += "; " + "; ".join(inclusion_failures)
        else:
            inclusion_detail = "; ".join(inclusion_failures)
        return MetaAvailability(
            summaries_available=summaries_available,
            inclusion_available=inclusion_available,
            summary_detail="; ".join(summary_failures),
            inclusion_detail=inclusion_detail,
        )

    @staticmethod
    def _unreachable(detail: str) -> MetaAvailability:
        return MetaAvailability(
            summaries_available=False,
            inclusion_available=False,
            summary_detail=detail,
            inclusion_detail="card inclusion requires tournament facts",
        )

    def availability(self) -> MetaAvailability:
        try:
            with _connect(self._dsn) as conn, conn.transaction():
                return self._probe_availability(conn)
        except RepositoryUnavailable as exc:
            return self._unreachable(str(exc))
        except Exception as exc:
            if self._is_connection_error(exc):
                return self._unreachable(f"could not reach mtg_v1: {exc}")
            raise

    def load_evidence(
        self,
        identity_key: str,
        *,
        since: date,
        min_event_size: int = 0,
        limits: MetaEvidenceLimits | None = None,
    ) -> MetaEvidenceSlice:
        """Load one exact cohort and all derived facts in a consistent snapshot."""
        limits = limits or MetaEvidenceLimits()
        params = {
            "key": identity_key,
            "since": since,
            "min_size": min_event_size,
        }
        cohort = """
            WITH cohort AS MATERIALIZED (
                SELECT e.entry_id, e.tournament_id, e.deck_id, e.standing,
                       e.wins, e.losses, e.draws, t.name AS event_name,
                       t.event_date, t.player_count, t.source, t.url, t.top_cut,
                       d.commander_identity, d.is_complete
                FROM mtg_v1.tournament AS t
                JOIN mtg_v1.tournament_entry AS e
                  ON e.tournament_id = t.tournament_id
                JOIN mtg_v1.deck AS d ON d.deck_id = e.deck_id
                WHERE e.deck_id IS NOT NULL
                  AND d.commander_identity = %(key)s
                  AND t.event_date >= %(since)s
                  AND t.player_count >= %(min_size)s
            )
        """
        try:
            with _connect(self._dsn) as conn, conn.transaction():
                _query(
                    conn,
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
                )
                availability = self._probe_availability(conn)
                if not availability.summaries_available:
                    return MetaEvidenceSlice(
                        identity_key=identity_key,
                        since=since,
                        min_event_size=min_event_size,
                        availability=availability,
                        inclusions=None,
                    )

                captured_at = datetime.now(UTC)
                snapshot_rows = _query(
                    conn,
                    "SELECT COUNT(*) AS n, MAX(event_date) AS latest "
                    "FROM mtg_v1.tournament",
                )
                snapshot_row = snapshot_rows[0] if snapshot_rows else {}
                latest = snapshot_row.get("latest")
                latest_at = (
                    latest
                    if isinstance(latest, datetime)
                    else (
                        datetime.combine(latest, datetime.min.time(), tzinfo=UTC)
                        if isinstance(latest, date)
                        else None
                    )
                )
                snapshot = CorpusSnapshot(
                    source_view="mtg_v1.tournament",
                    row_count=snapshot_row.get("n"),
                    max_content_updated_at=latest_at,
                    captured_at=captured_at,
                )

                aggregate_rows = _query(
                    conn,
                    cohort + """
                    SELECT COUNT(DISTINCT entry_id) AS entries,
                           COUNT(DISTINCT tournament_id) AS events,
                           COUNT(DISTINCT entry_id)
                               FILTER (WHERE standing = 1) AS event_wins,
                           COUNT(DISTINCT entry_id) FILTER (
                               WHERE top_cut IS NOT NULL
                                 AND standing IS NOT NULL
                                 AND standing <= top_cut
                           ) AS top_cuts,
                           COUNT(DISTINCT deck_id) AS decks,
                           COUNT(DISTINCT deck_id)
                               FILTER (WHERE is_complete IS FALSE)
                               AS incomplete_decks
                    FROM cohort
                    """,
                    params,
                )
                aggregate = aggregate_rows[0] if aggregate_rows else {}
                entry_count = int(aggregate.get("entries") or 0)
                denominator = int(aggregate.get("decks") or 0)
                incomplete_decks = int(aggregate.get("incomplete_decks") or 0)

                commander: MetaCommander | None = None
                if entry_count:
                    commander_rows = _query(
                        conn,
                        cohort + """
                        SELECT dc.oracle_id,
                               MIN(dc.submitted_name) AS submitted_name,
                               MIN(dc.position) AS position
                        FROM cohort AS c
                        JOIN mtg_v1.deck_commander AS dc
                          ON dc.deck_id = c.deck_id
                        WHERE dc.oracle_id IS NOT NULL
                        GROUP BY dc.oracle_id
                        ORDER BY MIN(dc.position), dc.oracle_id
                        """,
                        params,
                    )
                    commander = MetaCommander(
                        identity_key=identity_key,
                        oracle_ids=tuple(str(r["oracle_id"]) for r in commander_rows),
                        names=tuple(
                            str(r.get("submitted_name") or "") for r in commander_rows
                        ),
                        entries=entry_count,
                        events=int(aggregate.get("events") or 0),
                        wins=int(aggregate.get("event_wins") or 0),
                        top_cuts=int(aggregate.get("top_cuts") or 0),
                    )

                event_rows = _query(
                    conn,
                    cohort + """
                    SELECT DISTINCT tournament_id AS event_id, event_name AS name,
                           event_date AS held_on, player_count AS size, source,
                           url AS source_url
                    FROM cohort
                    ORDER BY held_on DESC, event_id
                    LIMIT %(event_limit)s
                    """,
                    {**params, "event_limit": limits.events},
                )
                events = tuple(
                    MetaEvent(
                        event_id=str(r["event_id"]),
                        name=r.get("name") or "",
                        # The producer exposes timestamptz; evidence uses UTC dates.
                        held_on=(
                            r["held_on"].astimezone(UTC).date()
                            if isinstance(r.get("held_on"), datetime)
                            else r.get("held_on")
                        ),
                        size=int(r.get("size") or 0),
                        source=r.get("source") or "",
                        source_url=r.get("source_url") or "",
                    )
                    for r in event_rows
                )

                entry_rows = _query(
                    conn,
                    cohort + """
                    SELECT entry_id, tournament_id AS event_id,
                           commander_identity AS identity_key, standing,
                           wins, losses, draws, deck_id AS decklist_id
                    FROM cohort
                    ORDER BY event_date DESC, standing ASC NULLS LAST, entry_id
                    LIMIT %(entry_limit)s
                    """,
                    {**params, "entry_limit": limits.entries},
                )
                entries = tuple(
                    MetaEntry(
                        entry_id=str(r["entry_id"]),
                        event_id=str(r["event_id"]),
                        identity_key=str(r["identity_key"]),
                        standing=r.get("standing"),
                        wins=int(r.get("wins") or 0),
                        losses=int(r.get("losses") or 0),
                        draws=int(r.get("draws") or 0),
                        decklist_id=str(r["decklist_id"]),
                    )
                    for r in entry_rows
                )

                inclusions: tuple[InclusionFact, ...] | None = None
                if availability.inclusion_available:
                    inclusion_rows = _query(
                        conn,
                        cohort + """
                        SELECT dc.oracle_id, card.name AS card_name,
                               COUNT(DISTINCT c.deck_id) AS decks_including
                        FROM (SELECT DISTINCT deck_id FROM cohort) AS c
                        JOIN mtg_v1.deck_card AS dc ON dc.deck_id = c.deck_id
                        JOIN mtg_v1.card_any_medium AS card
                          ON card.oracle_id = dc.oracle_id
                        WHERE dc.board = 'mainboard'
                          AND dc.oracle_id IS NOT NULL
                        GROUP BY dc.oracle_id, card.name
                        ORDER BY decks_including DESC, card.name, dc.oracle_id
                        LIMIT %(inclusion_limit)s
                        """,
                        {**params, "inclusion_limit": limits.inclusions},
                    )
                    inclusions = tuple(
                        InclusionFact(
                            identity_key=identity_key,
                            oracle_id=str(r["oracle_id"]),
                            card_name=r.get("card_name") or "",
                            decks_including=int(r["decks_including"]),
                            decks=denominator,
                        )
                        for r in inclusion_rows
                        if denominator > 0
                    )

                return MetaEvidenceSlice(
                    identity_key=identity_key,
                    since=since,
                    min_event_size=min_event_size,
                    availability=availability,
                    snapshot=snapshot,
                    commander=commander,
                    events=events,
                    entries=entries,
                    inclusions=inclusions,
                    inclusion_denominator=(
                        denominator if availability.inclusion_available else None
                    ),
                    incomplete_decks=incomplete_decks,
                )
        except RepositoryUnavailable:
            raise
        except Exception as exc:
            if self._is_contract_error(exc) or self._is_connection_error(exc):
                raise RepositoryUnavailable(
                    f"tournament evidence query could not use mtg_v1: {exc}"
                ) from exc
            raise
