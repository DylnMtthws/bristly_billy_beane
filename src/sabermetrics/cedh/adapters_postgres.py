"""Postgres adapters for the ``mtg_v1`` contract.

Connects as ``mtg_consumer`` and reads ``mtg_v1`` only. ``psycopg`` is imported
lazily so the ordinary test suite — which uses the fixture adapters — needs
neither the driver nor a database.

Card data is live today. The tournament half of the contract is **not
published yet**: the ingestion pipeline's decklist import (its Stage 3) is
paused, and ``mtg_v1`` currently exposes ``card``, ``card_any_medium``,
``card_non_gameplay``, ``card_face`` and ``card_legality`` and nothing else.
:class:`PostgresMetaRepository` is therefore written against the view contract
requested in ``docs/integration-handoff.md`` and reports itself unavailable,
by probing, until those views exist. It never fabricates a fallback.
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
    assert_v1_only,
    chunked,
)

logger = logging.getLogger(__name__)

#: The card view we read. NOT ``mtg_v1.card`` — see :class:`CardFacts`.
CARD_VIEW = "mtg_v1.card_any_medium"
FACE_VIEW = "mtg_v1.card_face"
LEGALITY_VIEW = "mtg_v1.card_legality"

#: Views the meta repository needs. Requested, not yet published.
META_VIEWS = (
    "tournament",
    "tournament_entry",
    "commander_identity",
    "commander_card_inclusion",
)


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
        return list(cur.fetchall())


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

    Probes for its views before answering. Until the ingestion pipeline
    publishes them, :meth:`availability` reports what is missing and every read
    raises :class:`RepositoryUnavailable` — the caller renders "no tournament
    evidence", which is a different statement from "no decks ran this card".
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn
        self._availability: MetaAvailability | None = None

    def _rows(self, sql: str, params: dict[str, Any]) -> list[dict]:
        with _connect(self._dsn) as conn:
            return _query(conn, sql, params)

    def availability(self) -> MetaAvailability:
        if self._availability is not None:
            return self._availability
        try:
            rows = self._rows(
                "SELECT table_name FROM information_schema.views "
                "WHERE table_schema = 'mtg_v1'",
                {},
            )
            present = {str(r["table_name"]) for r in rows}
            missing = tuple(v for v in META_VIEWS if v not in present)
            self._availability = MetaAvailability(
                available=not missing,
                missing_views=tuple(f"mtg_v1.{v}" for v in missing),
                detail=(
                    ""
                    if not missing
                    else "the ingestion pipeline has not published the "
                    "tournament half of the mtg_v1 contract yet"
                ),
            )
        except Exception as exc:
            self._availability = MetaAvailability(
                available=False,
                missing_views=tuple(f"mtg_v1.{v}" for v in META_VIEWS),
                detail=f"could not reach mtg_v1: {exc}",
            )
        return self._availability

    def _require(self) -> None:
        state = self.availability()
        if not state.available:
            raise RepositoryUnavailable(
                "tournament evidence is unavailable: missing "
                f"{', '.join(state.missing_views)} — {state.detail}"
            )

    def snapshot(self) -> CorpusSnapshot:
        self._require()
        rows = self._rows(
            "SELECT COUNT(*) AS n, MAX(held_on) AS latest FROM mtg_v1.tournament",
            {},
        )
        row = rows[0] if rows else {}
        latest = row.get("latest")
        return CorpusSnapshot(
            source_view="mtg_v1.tournament",
            row_count=row.get("n"),
            max_content_updated_at=(
                datetime.combine(latest, datetime.min.time(), tzinfo=UTC)
                if isinstance(latest, date)
                else None
            ),
            captured_at=datetime.now(UTC),
        )

    def commander(self, identity_key: str) -> MetaCommander | None:
        self._require()
        rows = self._rows(
            "SELECT identity_key, oracle_ids, names, entries, events, wins, "
            "top_cuts FROM mtg_v1.commander_identity "
            "WHERE identity_key = %(key)s",
            {"key": identity_key},
        )
        if not rows:
            return None
        row = rows[0]
        return MetaCommander(
            identity_key=row["identity_key"],
            oracle_ids=_tuple(row.get("oracle_ids")),
            names=_tuple(row.get("names")),
            entries=int(row.get("entries") or 0),
            events=int(row.get("events") or 0),
            wins=int(row.get("wins") or 0),
            top_cuts=int(row.get("top_cuts") or 0),
        )

    def events(
        self, *, since: date, min_size: int = 0, limit: int = 50
    ) -> list[MetaEvent]:
        self._require()
        rows = self._rows(
            "SELECT event_id, name, held_on, size, source, source_url "
            "FROM mtg_v1.tournament "
            "WHERE held_on >= %(since)s AND size >= %(min_size)s "
            "ORDER BY held_on DESC LIMIT %(limit)s",
            {"since": since, "min_size": min_size, "limit": limit},
        )
        return [
            MetaEvent(
                event_id=str(r["event_id"]),
                name=r.get("name") or "",
                held_on=r.get("held_on"),
                size=int(r.get("size") or 0),
                source=r.get("source") or "",
                source_url=r.get("source_url") or "",
            )
            for r in rows
        ]

    def entries(
        self,
        identity_key: str,
        *,
        since: date,
        min_event_size: int = 0,
        limit: int = 50,
    ) -> list[MetaEntry]:
        self._require()
        rows = self._rows(
            "SELECT e.entry_id, e.event_id, e.identity_key, e.standing, "
            "e.wins, e.losses, e.draws, e.decklist_id "
            "FROM mtg_v1.tournament_entry e "
            "JOIN mtg_v1.tournament t ON t.event_id = e.event_id "
            "WHERE e.identity_key = %(key)s AND t.held_on >= %(since)s "
            "AND t.size >= %(min_size)s "
            "ORDER BY t.held_on DESC, e.standing ASC NULLS LAST "
            "LIMIT %(limit)s",
            {
                "key": identity_key,
                "since": since,
                "min_size": min_event_size,
                "limit": limit,
            },
        )
        return [
            MetaEntry(
                entry_id=str(r["entry_id"]),
                event_id=str(r["event_id"]),
                identity_key=r["identity_key"],
                standing=r.get("standing"),
                wins=int(r.get("wins") or 0),
                losses=int(r.get("losses") or 0),
                draws=int(r.get("draws") or 0),
                decklist_id=(str(r["decklist_id"]) if r.get("decklist_id") else None),
            )
            for r in rows
        ]

    def inclusions(
        self,
        identity_key: str,
        *,
        since: date,
        min_event_size: int = 0,
        limit: int = 200,
    ) -> list[InclusionFact]:
        self._require()
        rows = self._rows(
            "SELECT identity_key, oracle_id, card_name, decks_including, decks "
            "FROM mtg_v1.commander_card_inclusion "
            "WHERE identity_key = %(key)s AND since <= %(since)s "
            "AND min_event_size >= %(min_size)s AND decks > 0 "
            "ORDER BY decks_including DESC LIMIT %(limit)s",
            {
                "key": identity_key,
                "since": since,
                "min_size": min_event_size,
                "limit": limit,
            },
        )
        return [
            InclusionFact(
                identity_key=r["identity_key"],
                oracle_id=str(r["oracle_id"]),
                card_name=r.get("card_name") or "",
                decks_including=int(r["decks_including"]),
                decks=int(r["decks"]),
            )
            for r in rows
        ]
