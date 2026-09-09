"""Corpus sources: where the tag builder gets card text, and how it names it.

The tag builder needs something ``mtg_v1``'s repository interface deliberately
does not offer — a **full scan**. ``CardRepository`` is built for "fetch these
oracle ids", because that is what deterministic deck construction needs; a
mechanic tag rebuild reads every card exactly once. That is a different access
pattern, so it gets a different interface rather than a fifth method on the
existing one.

Three sources implement it, and the difference between them is provenance, not
behaviour:

* :class:`PostgresCorpusSource` — production. ``mtg_v1.card_any_medium``, never
  ``mtg_v1.card`` (ADR-020), read-only, ``assert_v1_only`` on every statement.
* :class:`JsonCorpusSource` — a materialised snapshot on disk. Used by the test
  suite and for local development, so the tag library can be measured with no
  database. It carries the provenance of whatever produced it and refuses to
  pretend to be the production view.
* Any object satisfying :class:`CorpusSource`, for tests that construct cards
  in memory.

Every source yields the same :class:`~sabermetrics.mechanics.tags.predicates.CardView`,
which is a strict subset of ``CardFacts``. A predicate therefore cannot read a
field that exists offline and not in production — the failure mode where a tag
passes every fixture and tags nothing against the real corpus.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sabermetrics.mechanics.tags.predicates import CardView, FaceView

#: The on-disk snapshot format this module reads and ``scripts/`` writes.
CORPUS_SCHEMA_VERSION = "mechanic-corpus-snapshot.v1"


@dataclass(frozen=True)
class SnapshotIdentity:
    """Which corpus a tag build read, in a form two builds can be compared on.

    Mirrors :class:`sabermetrics.cedh.repositories.CorpusSnapshot`, plus the
    hash the tag rows carry. ``source_view`` is the honest label: a snapshot
    materialised from a development export says so, so a row built from it can
    never be mistaken for one built from ``mtg_v1``.
    """

    source_view: str
    row_count: int | None = None
    max_content_updated_at: str | None = None
    captured_at: str | None = None

    def sha256(self) -> str:
        """Stable hash over the identifying fields, not the card rows.

        Hashing the rows would be more precise and would also mean reading the
        whole corpus twice; these four fields are what ``mtg_v1`` already
        publishes to distinguish one nightly from the next, and the tag build's
        own content hash covers the output.
        """
        payload = "|".join(
            (
                self.source_view,
                "" if self.row_count is None else str(self.row_count),
                self.max_content_updated_at or "",
                self.captured_at or "",
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@runtime_checkable
class CorpusSource(Protocol):
    """A full scan over card text, plus the identity of what was scanned."""

    def identity(self) -> SnapshotIdentity:
        """Name the snapshot. Called before ``iter_cards``."""

    def iter_cards(self) -> Iterator[CardView]:
        """Yield every card exactly once, in a deterministic order."""


# --- in-memory -------------------------------------------------------------


@dataclass(frozen=True)
class InMemoryCorpusSource:
    """A fixed list of cards. For unit tests that build their own text."""

    cards: tuple[CardView, ...]
    source_view: str = "memory"

    def identity(self) -> SnapshotIdentity:
        return SnapshotIdentity(source_view=self.source_view, row_count=len(self.cards))

    def iter_cards(self) -> Iterator[CardView]:
        yield from sorted(self.cards, key=lambda c: (c.oracle_id, c.name))


# --- on disk ---------------------------------------------------------------


def card_view_from_mapping(raw: dict[str, Any]) -> CardView:
    """Build a :class:`CardView` from one snapshot record.

    Args:
        raw: A mapping with ``mtg_v1.card_any_medium`` column names.

    Returns:
        The card. Missing optional fields take their documented defaults;
        ``oracle_id`` and ``name`` are required, because a card that cannot be
        identified cannot carry a tag row.

    Raises:
        KeyError: If ``oracle_id`` or ``name`` is absent.
    """
    faces = tuple(
        FaceView(
            name=str(face.get("name") or ""),
            mana_cost=face.get("mana_cost"),
            type_line=face.get("type_line"),
            oracle_text=face.get("oracle_text"),
        )
        for face in (raw.get("faces") or ())
    )
    return CardView(
        oracle_id=str(raw["oracle_id"]),
        name=str(raw["name"]),
        layout=str(raw.get("layout") or ""),
        mana_cost=raw.get("mana_cost"),
        mana_value=float(raw.get("mana_value") or 0.0),
        type_line=str(raw.get("type_line") or ""),
        oracle_text=raw.get("oracle_text"),
        colors=tuple(raw.get("colors") or ()),
        color_identity=tuple(raw.get("color_identity") or ()),
        keywords=tuple(raw.get("keywords") or ()),
        all_types=tuple(raw.get("all_types") or ()),
        castable_cmcs=tuple(float(c) for c in (raw.get("castable_cmcs") or ())),
        faces=faces,
    )


class JsonCorpusSource:
    """A materialised snapshot: ``.json`` with a ``cards`` array, or ``.jsonl``.

    ``.jsonl`` takes its provenance from a sibling ``<name>.meta.json``. A
    snapshot with no provenance is readable but reports
    ``source_view="unattributed:<filename>"`` rather than inventing one, because
    a tag row whose snapshot cannot be named is a row nobody can reproduce.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if not self._path.exists():
            raise FileNotFoundError(f"corpus snapshot missing: {self._path}")
        self._identity = self._read_identity()

    @property
    def path(self) -> Path:
        return self._path

    def _read_identity(self) -> SnapshotIdentity:
        if self._path.suffix == ".jsonl":
            meta_path = self._path.with_suffix(".meta.json")
            raw = (
                json.loads(meta_path.read_text(encoding="utf-8"))
                if meta_path.exists()
                else {}
            )
        else:
            with self._path.open(encoding="utf-8") as handle:
                raw = json.load(handle)
        snapshot = raw.get("snapshot") or {}
        return SnapshotIdentity(
            source_view=str(
                snapshot.get("source_view") or f"unattributed:{self._path.name}"
            ),
            row_count=snapshot.get("row_count"),
            max_content_updated_at=snapshot.get("max_content_updated_at"),
            captured_at=snapshot.get("captured_at"),
        )

    def identity(self) -> SnapshotIdentity:
        return self._identity

    def iter_cards(self) -> Iterator[CardView]:
        if self._path.suffix == ".jsonl":
            with self._path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        yield card_view_from_mapping(json.loads(line))
            return
        with self._path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        for raw in payload.get("cards", []):
            yield card_view_from_mapping(raw)

    def by_name(self) -> dict[str, CardView]:
        """Index the snapshot by card name, and by front-face name for DFCs.

        Used by the fixture harness, which labels cards the way a person does.
        The joined ``A // B`` name wins over a bare front-face key, so a fixture
        naming ``Fire`` still resolves while ``Fire // Ice`` resolves exactly.
        """
        index: dict[str, CardView] = {}
        for card in self.iter_cards():
            front = card.name.split(" // ", 1)[0]
            index.setdefault(front, card)
        for card in self.iter_cards():
            index[card.name] = card
        return index


# --- production ------------------------------------------------------------


class PostgresCorpusSource:
    """Full scan of ``mtg_v1.card_any_medium`` as ``mtg_consumer``.

    Reads through the existing cEDH Postgres adapter's connection helpers so
    there is one place that knows the DSN, the read-only transaction settings
    and the schema guard. The view is ``card_any_medium`` and not ``card``: the
    default view silently drops 254 Reserved List cards, and a tag corpus built
    from it would be missing Lotus Petal and Mox Diamond while reporting
    success.
    """

    def __init__(self, dsn: str | None = None, *, batch_size: int = 5000) -> None:
        self._dsn = dsn
        self._batch_size = batch_size
        self._identity: SnapshotIdentity | None = None

    def identity(self) -> SnapshotIdentity:
        if self._identity is None:
            from sabermetrics.cedh.adapters_postgres import PostgresCardRepository

            snapshot = PostgresCardRepository(self._dsn).snapshot()
            self._identity = SnapshotIdentity(
                source_view=snapshot.source_view,
                row_count=snapshot.row_count,
                max_content_updated_at=(
                    snapshot.max_content_updated_at.isoformat()
                    if snapshot.max_content_updated_at
                    else None
                ),
                captured_at=(
                    snapshot.captured_at.isoformat() if snapshot.captured_at else None
                ),
            )
        return self._identity

    def iter_cards(self) -> Iterator[CardView]:
        """Scan cards and faces inside one read-only repeatable-read snapshot.

        The two reads must see the same instant. Counting faces and cards in
        separate transactions during a nightly import produces a corpus that
        never existed, and the build would then hash it and claim it did.
        """
        from sabermetrics.cedh import adapters_postgres as pg
        from sabermetrics.cedh.repositories import assert_v1_only

        card_sql = f"SELECT {pg._CARD_COLUMNS} FROM {pg.CARD_VIEW} ORDER BY oracle_id"
        face_sql = (
            "SELECT oracle_id, face_index, name, mana_cost, type_line, oracle_text "
            f"FROM {pg.FACE_VIEW} ORDER BY oracle_id, face_index"
        )
        # ``_query`` guards each statement too; asserting here as well means the
        # boundary is checked where the SQL is written, not only where it runs.
        assert_v1_only(card_sql)
        assert_v1_only(face_sql)
        with pg._connect(self._dsn) as conn, conn.transaction():
            pg._query(conn, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            faces: dict[str, list[dict[str, Any]]] = {}
            for row in pg._query(conn, face_sql, {}):
                faces.setdefault(str(row["oracle_id"]), []).append(dict(row))
            for row in pg._query(conn, card_sql, {}):
                record = dict(row)
                record["faces"] = faces.get(str(record["oracle_id"]), [])
                yield card_view_from_mapping(record)


def resolve_source(
    snapshot: str | Path | None = None, *, dsn: str | None = None
) -> CorpusSource:
    """Pick a corpus source from what the caller supplied.

    Args:
        snapshot: Path to a materialised snapshot. Wins when present.
        dsn: Postgres DSN, or ``None`` to take it from the environment.

    Returns:
        A :class:`CorpusSource`.
    """
    if snapshot is not None:
        return JsonCorpusSource(snapshot)
    return PostgresCorpusSource(dsn)


def names_missing_from(source: JsonCorpusSource, names: Sequence[str]) -> list[str]:
    """Fixture names the snapshot cannot resolve, sorted.

    A missing fixture is an error rather than a skipped case: a precision figure
    computed over the fixtures that happened to resolve is a different number
    than the one the tag claims, and nothing in the output would say so.
    """
    index = source.by_name()
    return sorted({name for name in names if name not in index})
