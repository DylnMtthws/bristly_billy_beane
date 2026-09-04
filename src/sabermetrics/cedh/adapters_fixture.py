"""Fixture adapters: the repositories the ordinary test suite runs against.

Every test outside the opt-in smoke suite uses these. They read checked-in JSON
under ``fixtures/cedh/`` and require neither network, Postgres, a model
provider, nor the simulator binary.

The fixtures are a *representative* sample, not a corpus mirror: enough cards
to build the Kinnan slice, a small tournament set, and the awkward cases the
real contract contains — a modal DFC whose card-level ``mana_cost`` is empty, a
card with empty ``castable_cmcs``, and a Reserved List card that
``mtg_v1.card`` would have silently dropped.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sabermetrics.cedh.domain import Legality
from sabermetrics.cedh.repositories import (
    CardFace,
    CardFacts,
    CorpusSnapshot,
    InclusionFact,
    MetaAvailability,
    MetaCommander,
    MetaEntry,
    MetaEvent,
)

#: Repo-root fixture directory. Shared by tests and local development.
FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "cedh"


def _load(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"cEDH fixture missing: {path}")
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _d(value: Any) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) else None


class FixtureCardRepository:
    """:class:`CardRepository` backed by ``fixtures/cedh/cards.json``."""

    def __init__(self, root: Path | None = None) -> None:
        data = _load((root or FIXTURE_ROOT) / "cards.json")
        snap = data.get("snapshot", {})
        self._snapshot = CorpusSnapshot(
            source_view=snap.get("source_view", "mtg_v1.card_any_medium"),
            row_count=snap.get("row_count"),
            max_content_updated_at=_dt(snap.get("max_content_updated_at")),
            captured_at=_dt(snap.get("captured_at")),
        )
        self._cards: dict[str, CardFacts] = {}
        for raw in data.get("cards", []):
            facts = CardFacts(
                oracle_id=raw["oracle_id"],
                name=raw["name"],
                layout=raw.get("layout", ""),
                mana_cost=raw.get("mana_cost"),
                mana_value=float(raw.get("mana_value", 0.0)),
                type_line=raw.get("type_line", ""),
                oracle_text=raw.get("oracle_text"),
                colors=tuple(raw.get("colors", ())),
                color_identity=tuple(raw.get("color_identity", ())),
                keywords=tuple(raw.get("keywords", ())),
                all_types=tuple(raw.get("all_types", ())),
                castable_cmcs=tuple(float(c) for c in raw.get("castable_cmcs", ())),
                has_land_face=bool(raw.get("has_land_face", False)),
                face_count=int(raw.get("face_count", 0)),
                faces=tuple(CardFace(**face) for face in raw.get("faces", ())),
                content_updated_at=_dt(raw.get("content_updated_at")),
            )
            self._cards[facts.oracle_id] = facts
        self._legality: dict[str, dict[str, str]] = data.get("legality", {})

    def snapshot(self) -> CorpusSnapshot:
        return self._snapshot

    def get_by_oracle_ids(self, oracle_ids: Sequence[str]) -> dict[str, CardFacts]:
        return {oid: self._cards[oid] for oid in oracle_ids if oid in self._cards}

    def resolve_names(self, names: Sequence[str]) -> dict[str, CardFacts]:
        by_name: dict[str, CardFacts] = {}
        for facts in self._cards.values():
            by_name[facts.name] = facts
            by_name.setdefault(facts.front_name, facts)
        return {name: by_name[name] for name in names if name in by_name}

    def legality(
        self, oracle_ids: Sequence[str], fmt: str = "commander"
    ) -> dict[str, Legality]:
        table = self._legality.get(fmt, {})
        out: dict[str, Legality] = {}
        for oid in oracle_ids:
            raw = table.get(oid)
            try:
                out[oid] = Legality(raw) if raw else Legality.UNKNOWN
            except ValueError:
                out[oid] = Legality.UNKNOWN
        return out


class FixtureMetaRepository:
    """:class:`MetaRepository` backed by ``fixtures/cedh/meta.json``.

    ``available`` is a field in the fixture, so both branches are testable: the
    populated world the contract will eventually provide, and today's world
    where the tournament views do not exist.
    """

    def __init__(self, root: Path | None = None, filename: str = "meta.json") -> None:
        data = _load((root or FIXTURE_ROOT) / filename)
        snap = data.get("snapshot", {})
        self._snapshot = CorpusSnapshot(
            source_view=snap.get("source_view", "mtg_v1.tournament"),
            row_count=snap.get("row_count"),
            max_content_updated_at=_dt(snap.get("max_content_updated_at")),
            captured_at=_dt(snap.get("captured_at")),
        )
        self._availability = MetaAvailability(
            available=bool(data.get("available", True)),
            missing_views=tuple(data.get("missing_views", ())),
            detail=data.get("detail", ""),
        )
        self._commanders = {
            c["identity_key"]: MetaCommander(
                identity_key=c["identity_key"],
                oracle_ids=tuple(c.get("oracle_ids", ())),
                names=tuple(c.get("names", ())),
                entries=int(c.get("entries", 0)),
                events=int(c.get("events", 0)),
                wins=int(c.get("wins", 0)),
                top_cuts=int(c.get("top_cuts", 0)),
            )
            for c in data.get("commanders", [])
        }
        self._events = [
            MetaEvent(
                event_id=e["event_id"],
                name=e.get("name", ""),
                held_on=_d(e.get("held_on")),
                size=int(e.get("size", 0)),
                source=e.get("source", ""),
                source_url=e.get("source_url", ""),
            )
            for e in data.get("events", [])
        ]
        self._entries = [
            MetaEntry(
                entry_id=e["entry_id"],
                event_id=e["event_id"],
                identity_key=e["identity_key"],
                standing=e.get("standing"),
                wins=int(e.get("wins", 0)),
                losses=int(e.get("losses", 0)),
                draws=int(e.get("draws", 0)),
                decklist_id=e.get("decklist_id"),
            )
            for e in data.get("entries", [])
        ]
        self._inclusions = [
            InclusionFact(
                identity_key=i["identity_key"],
                oracle_id=i["oracle_id"],
                card_name=i.get("card_name", ""),
                decks_including=int(i["decks_including"]),
                decks=int(i["decks"]),
            )
            for i in data.get("inclusions", [])
        ]

    # -- MetaRepository ---------------------------------------------------

    def availability(self) -> MetaAvailability:
        return self._availability

    def _require(self) -> None:
        if not self._availability.available:
            from sabermetrics.cedh.errors import RepositoryUnavailable

            raise RepositoryUnavailable(
                "tournament evidence is unavailable: missing "
                f"{', '.join(self._availability.missing_views)} — "
                f"{self._availability.detail}"
            )

    def snapshot(self) -> CorpusSnapshot:
        self._require()
        return self._snapshot

    def commander(self, identity_key: str) -> MetaCommander | None:
        self._require()
        return self._commanders.get(identity_key)

    def _event_ok(self, event_id: str, since: date, min_size: int) -> bool:
        for event in self._events:
            if event.event_id != event_id:
                continue
            if event.size < min_size:
                return False
            return event.held_on is None or event.held_on >= since
        return False

    def events(
        self, *, since: date, min_size: int = 0, limit: int = 50
    ) -> list[MetaEvent]:
        self._require()
        hits = [
            e
            for e in self._events
            if e.size >= min_size and (e.held_on is None or e.held_on >= since)
        ]
        hits.sort(key=lambda e: (e.held_on or date.min), reverse=True)
        return hits[:limit]

    def entries(
        self,
        identity_key: str,
        *,
        since: date,
        min_event_size: int = 0,
        limit: int = 50,
    ) -> list[MetaEntry]:
        self._require()
        hits = [
            e
            for e in self._entries
            if e.identity_key == identity_key
            and self._event_ok(e.event_id, since, min_event_size)
        ]
        hits.sort(key=lambda e: (e.standing is None, e.standing or 0))
        return hits[:limit]

    def inclusions(
        self,
        identity_key: str,
        *,
        since: date,
        min_event_size: int = 0,
        limit: int = 200,
    ) -> list[InclusionFact]:
        self._require()
        hits = [i for i in self._inclusions if i.identity_key == identity_key]
        hits.sort(key=lambda i: i.decks_including, reverse=True)
        return hits[:limit]
