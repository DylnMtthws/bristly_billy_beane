"""Repository interfaces: the boundary to the ingestion pipeline's contract.

This repository does not own card data or tournament data. It reads them
through ``mtg_v1``, the public schema the ingestion pipeline publishes, as the
``mtg_consumer`` role. These Protocols are the only shape the rest of the cEDH
path is allowed to see, so a schema change on the other side lands in one
adapter rather than across the generation path.

Two rules are enforced rather than documented:

* No query may name ``mtg_internal``. :func:`assert_v1_only` rejects it before
  execution, so the boundary fails in unit tests rather than at runtime on a
  role that happens to be over-privileged.
* Absence is reported, never defaulted. A missing legality row is
  :attr:`~sabermetrics.cedh.domain.Legality.UNKNOWN`, a missing meta view
  raises :class:`~sabermetrics.cedh.errors.RepositoryUnavailable`. Neither is
  an empty result set that reads like a real answer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sabermetrics.cedh.domain import Legality
from sabermetrics.cedh.errors import SchemaBoundaryViolation

#: The only schema production repositories may name.
PUBLIC_SCHEMA = "mtg_v1"

#: Schemas that are an implementation detail of the ingestion pipeline.
FORBIDDEN_SCHEMAS = ("mtg_internal",)

_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in FORBIDDEN_SCHEMAS) + r")\b",
    re.IGNORECASE,
)


def assert_v1_only(sql: str) -> None:
    """Reject SQL that names a schema outside the public contract.

    Args:
        sql: The statement about to be executed.

    Raises:
        SchemaBoundaryViolation: If the statement names a forbidden schema.
    """
    match = _FORBIDDEN_RE.search(sql)
    if match:
        raise SchemaBoundaryViolation(
            f"query names {match.group(1)!r}, which is an ingestion-internal "
            f"schema; production repositories may read {PUBLIC_SCHEMA} only"
        )


# --- Card facts -----------------------------------------------------------


class CardFace(BaseModel):
    """One face of a multi-faced card, as published by ``mtg_v1.card_face``."""

    model_config = ConfigDict(frozen=True)

    face_index: int
    name: str
    mana_cost: str | None = None
    face_mana_value: float | None = None
    type_line: str | None = None
    oracle_text: str | None = None


class CardFacts(BaseModel):
    """Everything the cEDH path is allowed to know about a card.

    Sourced from ``mtg_v1.card_any_medium`` — deliberately not ``mtg_v1.card``.
    The default view filters on an ``is_paper`` flag derived from the
    *representative printing*, which drops 254 Reserved List cards including
    Tropical Island, Mox Diamond and Lotus Petal. Those are cEDH staples, and
    the failure is silent: fewer rows and a success return. The ingestion repo
    records this as a known defect and directs consumers to the wider view.

    **There is no price field, on purpose.** cEDH is proxy-normal, so price is
    not a performance signal and the engine optimises for performance. Leaving
    the field out is what stops it drifting back into selection as a
    tie-break, a soft penalty or a display value someone later sorts by.
    ``mtg_v1`` publishes prices as ``rep_prices``, and the ``rep_`` prefix is a
    second reason to leave them alone: that is the price of one arbitrary
    printing, not of the card.
    """

    model_config = ConfigDict(frozen=True)

    oracle_id: str
    name: str
    layout: str = ""
    mana_cost: str | None = None
    mana_value: float = 0.0
    type_line: str = ""
    oracle_text: str | None = None
    colors: tuple[str, ...] = ()
    color_identity: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    all_types: tuple[str, ...] = ()
    #: Sorted distinct mana values this card can enter the stack at. EMPTY for
    #: cards that cannot be cast (1,455 rows corpus-wide) — callers taking a
    #: min() must skip empties rather than reading 0.
    castable_cmcs: tuple[float, ...] = ()
    has_land_face: bool = False
    face_count: int = 0
    faces: tuple[CardFace, ...] = ()
    content_updated_at: datetime | None = None

    @property
    def is_land(self) -> bool:
        return "Land" in self.all_types or self.type_line.startswith("Land")

    @property
    def front_name(self) -> str:
        """Front-face name. ``mtg_v1`` publishes the joined name for DFCs."""
        return self.name.split(" // ", 1)[0]


class CorpusSnapshot(BaseModel):
    """Which corpus version an answer came from.

    Carried into every evidence chunk and every deck candidate so a stored
    result can be told apart from one built against a later nightly.
    """

    model_config = ConfigDict(frozen=True)

    source_view: str
    row_count: int | None = None
    max_content_updated_at: datetime | None = None
    captured_at: datetime | None = None

    @property
    def label(self) -> str:
        stamp = (
            self.max_content_updated_at.date().isoformat()
            if self.max_content_updated_at
            else "unknown"
        )
        return f"{self.source_view}@{stamp}"


@runtime_checkable
class CardRepository(Protocol):
    """Card facts and legality. Read-only, ``mtg_v1`` only."""

    def snapshot(self) -> CorpusSnapshot:
        """Return which corpus version this repository is serving."""
        ...

    def get_by_oracle_ids(self, oracle_ids: Sequence[str]) -> dict[str, CardFacts]:
        """Fetch card facts keyed by oracle_id.

        Missing ids are simply absent from the mapping; the caller decides
        whether that is a gap or a filter. Never raises for a missing card.
        """
        ...

    def resolve_names(self, names: Sequence[str]) -> dict[str, CardFacts]:
        """Resolve display names to card facts, front-face names included.

        ``mtg_v1`` publishes the joined name for multi-faced cards, so
        ``"Bala Ged Recovery"`` must match
        ``"Bala Ged Recovery // Bala Ged Sanctuary"``. The mapping is keyed by
        the name that was asked for, not the name that was found.
        """
        ...

    def legality(
        self, oracle_ids: Sequence[str], fmt: str = "commander"
    ) -> dict[str, Legality]:
        """Return per-card legality in ``fmt``.

        Cards with no row map to :attr:`Legality.UNKNOWN`, which is not the
        same answer as ``not_legal`` and must not be rendered as one.
        """
        ...


# --- Tournament / metagame facts -----------------------------------------


class MetaCommander(BaseModel):
    """A commander identity as it appears in tournament results."""

    model_config = ConfigDict(frozen=True)

    identity_key: str
    oracle_ids: tuple[str, ...]
    names: tuple[str, ...]
    #: Distinct qualifying tournament entries in the requested cohort.
    entries: int = 0
    #: Distinct tournaments represented by those entries.
    events: int = 0
    #: Event wins (``standing = 1``), not summed match wins.
    wins: int = 0
    #: Entries within a known event top cut; unknown cut sizes never count.
    top_cuts: int = 0


class MetaEvent(BaseModel):
    """One tournament."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    name: str
    held_on: date | None = None
    size: int = 0
    source: str = ""
    source_url: str = ""


class MetaEntry(BaseModel):
    """One deck's finish at one event."""

    model_config = ConfigDict(frozen=True)

    entry_id: str
    event_id: str
    identity_key: str
    standing: int | None = None
    wins: int = 0
    losses: int = 0
    draws: int = 0
    decklist_id: str | None = None


class InclusionFact(BaseModel):
    """How often a card appears in decks for one commander identity.

    ``decks`` is the denominator and is required. An inclusion rate without its
    sample size is not usable as evidence and is not representable here.
    """

    model_config = ConfigDict(frozen=True)

    identity_key: str
    oracle_id: str
    card_name: str
    decks_including: int = Field(ge=0)
    decks: int = Field(gt=0)

    @property
    def rate(self) -> float:
        return self.decks_including / self.decks


class MetaAvailability(BaseModel):
    """What the meta repository can currently answer.

    Tournament summaries and card inclusion are separate capabilities.  The
    latter needs two additional source views, so losing it must not hide valid
    finishes.  Each state is reported rather than inferred from an empty list:
    "no qualifying decks" and "the query could not be executed" are different
    facts.
    """

    model_config = ConfigDict(frozen=True)

    summaries_available: bool
    inclusion_available: bool
    summary_detail: str = ""
    inclusion_detail: str = ""

    @model_validator(mode="after")
    def _inclusion_requires_summaries(self) -> MetaAvailability:
        if self.inclusion_available and not self.summaries_available:
            raise ValueError("card inclusion requires tournament summaries")
        return self

    @property
    def available(self) -> bool:
        """Compatibility name for the core tournament-summary capability."""
        return self.summaries_available

    @property
    def detail(self) -> str:
        """Compatibility name for the core tournament-summary detail."""
        return self.summary_detail


class MetaEvidenceLimits(BaseModel):
    """Bounds on rows returned for display, never on aggregate sample counts."""

    model_config = ConfigDict(frozen=True)

    events: int = Field(default=200, ge=1)
    entries: int = Field(default=25, ge=1)
    inclusions: int = Field(default=60, ge=1)


class MetaEvidenceSlice(BaseModel):
    """One snapshot-consistent Deck Lab cohort and its derived facts.

    ``entries``/``events`` are bounded display rows.  The counts on
    ``commander`` and ``inclusion_denominator`` are computed over the full
    cohort and therefore never come from the lengths of those lists.

    ``inclusions=None`` means the inclusion capability was unavailable;
    ``inclusions=()`` with a zero denominator means it ran successfully and
    found an empty cohort.
    """

    model_config = ConfigDict(frozen=True)

    identity_key: str
    since: date
    min_event_size: int = Field(ge=0)
    availability: MetaAvailability
    snapshot: CorpusSnapshot | None = None
    commander: MetaCommander | None = None
    events: tuple[MetaEvent, ...] = ()
    entries: tuple[MetaEntry, ...] = ()
    inclusions: tuple[InclusionFact, ...] | None = None
    inclusion_denominator: int | None = Field(default=None, ge=0)
    incomplete_decks: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _availability_is_explicit(self) -> MetaEvidenceSlice:
        if not self.availability.summaries_available and (
            self.snapshot is not None
            or self.commander is not None
            or self.events
            or self.entries
        ):
            raise ValueError("unavailable tournament summaries cannot carry facts")
        if self.availability.inclusion_available:
            if self.inclusions is None or self.inclusion_denominator is None:
                raise ValueError(
                    "available inclusion must carry a result and exact denominator"
                )
            if any(
                fact.decks != self.inclusion_denominator for fact in self.inclusions
            ):
                raise ValueError("all inclusion facts must use the exact denominator")
        elif self.inclusions is not None or self.inclusion_denominator is not None:
            raise ValueError("unavailable inclusion cannot masquerade as empty data")
        return self


@runtime_checkable
class MetaRepository(Protocol):
    """Deck Lab cohort aggregates derived from atomic ``mtg_v1`` facts."""

    def availability(self) -> MetaAvailability:
        """Execute cheap contract probes for each independently useful capability."""
        ...

    def load_evidence(
        self,
        identity_key: str,
        *,
        since: date,
        min_event_size: int = 0,
        limits: MetaEvidenceLimits | None = None,
    ) -> MetaEvidenceSlice:
        """Load all evidence through one consistent repository snapshot."""
        ...


def chunked(items: Iterable[str], size: int = 500) -> list[list[str]]:
    """Split ids into batches so a query parameter list stays bounded."""
    batch: list[str] = []
    out: list[list[str]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            out.append(batch)
            batch = []
    if batch:
        out.append(batch)
    return out
