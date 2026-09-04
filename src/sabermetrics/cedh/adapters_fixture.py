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
    MetaEvidenceLimits,
    MetaEvidenceSlice,
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

    The JSON mirrors the producer's atomic view rows.  This adapter deliberately
    derives the same cohort as the Postgres adapter instead of storing
    consumer-specific commander and inclusion aggregates in a fixture.
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
        availability = data.get("availability", {})
        self._availability = MetaAvailability(
            summaries_available=bool(
                availability.get("summaries_available", data.get("available", True))
            ),
            inclusion_available=bool(
                availability.get("inclusion_available", data.get("available", True))
            ),
            summary_detail=availability.get("summary_detail", data.get("detail", "")),
            inclusion_detail=availability.get("inclusion_detail", ""),
        )
        self._tournaments = []
        for raw in data.get("tournaments", []):
            event = dict(raw)
            event["event_date"] = _d(event.get("event_date"))
            self._tournaments.append(event)
        self._tournament_entries = list(data.get("tournament_entries", []))
        self._decks = list(data.get("decks", []))
        self._deck_commanders = list(data.get("deck_commanders", []))
        self._deck_cards = list(data.get("deck_cards", []))
        self._cards = list(data.get("cards", []))

    # -- MetaRepository ---------------------------------------------------

    def availability(self) -> MetaAvailability:
        return self._availability

    def load_evidence(
        self,
        identity_key: str,
        *,
        since: date,
        min_event_size: int = 0,
        limits: MetaEvidenceLimits | None = None,
    ) -> MetaEvidenceSlice:
        """Derive one cohort from the fixture's atomic producer-shaped rows."""
        limits = limits or MetaEvidenceLimits()
        if not self._availability.summaries_available:
            return MetaEvidenceSlice(
                identity_key=identity_key,
                since=since,
                min_event_size=min_event_size,
                availability=self._availability,
                inclusions=None,
            )

        tournaments = {str(t["tournament_id"]): t for t in self._tournaments}
        decks = {str(d["deck_id"]): d for d in self._decks}
        cohort: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        seen_entries: set[str] = set()
        for entry in self._tournament_entries:
            entry_id = str(entry["entry_id"])
            deck_id = entry.get("deck_id")
            event = tournaments.get(str(entry.get("tournament_id")))
            deck = decks.get(str(deck_id)) if deck_id is not None else None
            if entry_id in seen_entries or event is None or deck is None:
                continue
            event_date = event.get("event_date")
            if (
                deck.get("commander_identity") != identity_key
                or event_date is None
                or event_date < since
                or int(event.get("player_count") or 0) < min_event_size
            ):
                continue
            seen_entries.add(entry_id)
            cohort.append((entry, event, deck))

        cohort.sort(
            key=lambda row: (
                row[1].get("event_date") or date.min,
                -(row[0].get("standing") or 1_000_000),
            ),
            reverse=True,
        )
        deck_ids = {str(entry["deck_id"]) for entry, _, _ in cohort}
        event_ids = {str(entry["tournament_id"]) for entry, _, _ in cohort}

        commander_rows: dict[str, tuple[Any, str]] = {}
        for raw in self._deck_commanders:
            oracle_id = raw.get("oracle_id")
            if str(raw.get("deck_id")) not in deck_ids or oracle_id is None:
                continue
            key = str(oracle_id)
            candidate = (
                int(raw.get("position") or 0),
                str(raw.get("submitted_name") or ""),
            )
            if key not in commander_rows or candidate < commander_rows[key]:
                commander_rows[key] = candidate
        ordered_commanders = sorted(
            commander_rows.items(), key=lambda item: (item[1][0], item[0])
        )

        event_wins = sum(1 for entry, _, _ in cohort if entry.get("standing") == 1)
        top_cuts = sum(
            1
            for entry, event, _ in cohort
            if event.get("top_cut") is not None
            and entry.get("standing") is not None
            and int(entry["standing"]) <= int(event["top_cut"])
        )
        commander = (
            MetaCommander(
                identity_key=identity_key,
                oracle_ids=tuple(item[0] for item in ordered_commanders),
                names=tuple(item[1][1] for item in ordered_commanders),
                entries=len(cohort),
                events=len(event_ids),
                wins=event_wins,
                top_cuts=top_cuts,
            )
            if cohort
            else None
        )

        unique_events = {event_id: tournaments[event_id] for event_id in event_ids}
        event_models = [
            MetaEvent(
                event_id=event_id,
                name=event.get("name") or "",
                held_on=event.get("event_date"),
                size=int(event.get("player_count") or 0),
                source=event.get("source") or "",
                source_url=event.get("url") or "",
            )
            for event_id, event in unique_events.items()
        ]
        event_models.sort(key=lambda event: event.held_on or date.min, reverse=True)
        entry_models = tuple(
            MetaEntry(
                entry_id=str(entry["entry_id"]),
                event_id=str(entry["tournament_id"]),
                identity_key=identity_key,
                standing=entry.get("standing"),
                wins=int(entry.get("wins") or 0),
                losses=int(entry.get("losses") or 0),
                draws=int(entry.get("draws") or 0),
                decklist_id=str(entry["deck_id"]),
            )
            for entry, _, _ in cohort[: limits.entries]
        )

        inclusions: tuple[InclusionFact, ...] | None = None
        denominator: int | None = None
        if self._availability.inclusion_available:
            denominator = len(deck_ids)
            names = {
                str(card["oracle_id"]): str(card.get("name") or "")
                for card in self._cards
            }
            included_by: dict[str, set[str]] = {}
            for raw in self._deck_cards:
                deck_id = str(raw.get("deck_id"))
                oracle_id = raw.get("oracle_id")
                if (
                    deck_id not in deck_ids
                    or raw.get("board") != "mainboard"
                    or oracle_id is None
                    or str(oracle_id) not in names
                ):
                    continue
                included_by.setdefault(str(oracle_id), set()).add(deck_id)
            ranked = sorted(
                included_by.items(),
                key=lambda item: (-len(item[1]), names[item[0]], item[0]),
            )[: limits.inclusions]
            inclusions = tuple(
                InclusionFact(
                    identity_key=identity_key,
                    oracle_id=oracle_id,
                    card_name=names[oracle_id],
                    decks_including=len(including),
                    decks=denominator,
                )
                for oracle_id, including in ranked
                if denominator > 0
            )

        incomplete_decks = sum(
            1 for deck_id in deck_ids if decks[deck_id].get("is_complete") is False
        )
        return MetaEvidenceSlice(
            identity_key=identity_key,
            since=since,
            min_event_size=min_event_size,
            availability=self._availability,
            snapshot=self._snapshot,
            commander=commander,
            events=tuple(event_models[: limits.events]),
            entries=entry_models,
            inclusions=inclusions,
            inclusion_denominator=denominator,
            incomplete_decks=incomplete_decks,
        )
