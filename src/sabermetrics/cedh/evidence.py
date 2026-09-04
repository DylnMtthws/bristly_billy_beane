"""Evidence retrieval: compact, attributed, and bounded.

Two sources feed the model, and neither of them is the card corpus:

1. **Tournament facts** from :class:`MetaRepository` — events, finishes and
   per-card inclusion rates, each carrying its sample size and window.
2. **Explicitly curated strategy material** from ``config/cedh_evidence/``,
   authored and checked in rather than scraped at generation time.

Every chunk carries source, source URL or internal id, the date it was fetched
or published, the commander identity it is about, the corpus snapshot it came
from, and a content hash. A chunk that cannot say where it came from is not
constructed — the provenance fields are required, not optional.

Popularity is not quality. Inclusion rates are rendered as a rate *and* its
denominator, and :class:`EvidencePackage` refuses to drop the window or the
event-size filter, because an inclusion rate stripped of those is a claim the
data does not support.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.cedh.domain import CommanderIdentity, MetagameWindow
from sabermetrics.cedh.errors import RepositoryUnavailable
from sabermetrics.cedh.repositories import (
    CorpusSnapshot,
    MetaAvailability,
    MetaRepository,
)
from sabermetrics.cedh.settings import EvidenceSettings

logger = logging.getLogger(__name__)

ChunkKind = Literal["tournament_fact", "inclusion_fact", "curated_strategy"]

#: Where curated strategy material lives. Checked in, versioned, human-authored.
CURATED_DIR = Path(__file__).resolve().parents[3] / "config" / "cedh_evidence"


def content_hash(text: str) -> str:
    """Return the sha256 of a chunk's content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EvidenceChunk(BaseModel):
    """One attributed piece of evidence.

    Every provenance field below is required to be *populated* by construction:
    :meth:`EvidenceChunk.build` refuses a chunk with no source and no locator,
    because an unattributable chunk read by a model becomes an unattributable
    claim in an explanation.
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    kind: ChunkKind
    content: str
    source: str
    source_url: str = ""
    source_id: str = ""
    fetched_at: datetime | None = None
    published_at: date | None = None
    commander_key: str = ""
    corpus_snapshot: str = ""
    content_sha256: str = ""
    #: Denominator behind the chunk, when it is a rate. None for prose.
    sample_size: int | None = None

    @classmethod
    def build(
        cls,
        *,
        chunk_id: str,
        kind: ChunkKind,
        content: str,
        source: str,
        commander_key: str,
        corpus_snapshot: str,
        source_url: str = "",
        source_id: str = "",
        fetched_at: datetime | None = None,
        published_at: date | None = None,
        sample_size: int | None = None,
    ) -> EvidenceChunk:
        """Construct a chunk, computing its hash and checking attribution.

        Raises:
            ValueError: If the chunk names no source, or names a source with
                neither a URL nor an internal id to locate it by.
        """
        if not source:
            raise ValueError(f"evidence chunk {chunk_id!r} has no source")
        if not source_url and not source_id:
            raise ValueError(
                f"evidence chunk {chunk_id!r} from {source!r} has neither a "
                "source_url nor a source_id; it could not be traced back"
            )
        return cls(
            chunk_id=chunk_id,
            kind=kind,
            content=content,
            source=source,
            source_url=source_url,
            source_id=source_id,
            fetched_at=fetched_at,
            published_at=published_at,
            commander_key=commander_key,
            corpus_snapshot=corpus_snapshot,
            content_sha256=content_hash(content),
            sample_size=sample_size,
        )


class EvidencePackage(BaseModel):
    """Everything sent to the model for one request, and where it came from."""

    model_config = ConfigDict(frozen=True)

    commander_key: str
    commander_name: str
    window_days: int
    min_event_size: int
    chunks: tuple[EvidenceChunk, ...] = ()
    card_snapshot: str = ""
    meta_snapshot: str = ""
    meta_available: bool = True
    meta_detail: str = ""
    #: Chunks dropped by the bounds. Reported so a truncated package is visibly
    #: truncated rather than quietly shorter.
    dropped_chunks: int = 0
    events_seen: int = 0
    decks_seen: int = 0

    @property
    def window_label(self) -> str:
        return f"last {self.window_days}d, events of {self.min_event_size}+"

    @property
    def evidence_hash(self) -> str:
        """Hash over chunk content hashes and the window.

        Includes the window because the same chunks retrieved under a different
        window are a different claim, and a cache keyed only on content would
        serve one answer for both.
        """
        digest = hashlib.sha256()
        digest.update(self.commander_key.encode("utf-8"))
        digest.update(f"|{self.window_days}|{self.min_event_size}|".encode())
        for chunk in self.chunks:
            digest.update(chunk.content_sha256.encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()

    def render(self) -> str:
        """Render the package as the attributed text block sent to the model."""
        if not self.chunks:
            return (
                f"NO EVIDENCE AVAILABLE for {self.commander_name} "
                f"({self.window_label}). {self.meta_detail}".strip()
            )
        lines = [
            f"EVIDENCE for {self.commander_name} ({self.window_label})",
            (
                f"corpus: cards {self.card_snapshot or 'unknown'}; "
                f"tournaments {self.meta_snapshot or 'unavailable'}"
            ),
            "",
        ]
        for chunk in self.chunks:
            locator = chunk.source_url or chunk.source_id
            stamp = (
                chunk.published_at.isoformat()
                if chunk.published_at
                else (
                    chunk.fetched_at.date().isoformat()
                    if chunk.fetched_at
                    else "undated"
                )
            )
            lines.append(
                f"[{chunk.chunk_id}] ({chunk.kind}) {chunk.source} "
                f"<{locator}> {stamp}"
            )
            lines.append(chunk.content)
            lines.append("")
        if self.dropped_chunks:
            lines.append(
                f"({self.dropped_chunks} further chunk(s) omitted by the "
                "evidence bounds.)"
            )
        return "\n".join(lines).strip()


class CuratedDocument(BaseModel):
    """A checked-in strategy document.

    Authored by a person and versioned in git, which is what "explicitly
    curated" means here — as opposed to scraped at generation time, which the
    generation path is not allowed to do.
    """

    doc_id: str
    title: str
    source: str
    source_url: str = ""
    source_id: str = ""
    published_at: date | None = None
    fetched_at: datetime | None = None
    #: Commander identity keys this document is about. Empty means format-wide.
    commander_keys: list[str] = Field(default_factory=list)
    #: Commander names this document is about. Matched as well as the keys,
    #: because a curated document outlives any one corpus: the fixture corpus
    #: and the live corpus assign different oracle_ids to the same commander,
    #: and a document pinned only to a key would silently stop matching.
    commander_names: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)


def load_curated(directory: Path | None = None) -> list[CuratedDocument]:
    """Load every curated strategy document from ``directory``."""
    root = directory or CURATED_DIR
    if not root.exists():
        return []
    docs: list[CuratedDocument] = []
    for path in sorted(root.glob("*.yaml")):
        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        raw.setdefault("doc_id", path.stem)
        docs.append(CuratedDocument(**raw))
    return docs


class EvidenceService:
    """Builds compact, attributed evidence packages.

    Args:
        meta: Tournament facts. May be unavailable; that is reported, not
            papered over.
        settings: Bounds on package size.
        curated_dir: Where curated strategy material lives.
        card_snapshot: Snapshot label from the card repository, for provenance.
    """

    def __init__(
        self,
        meta: MetaRepository,
        *,
        settings: EvidenceSettings | None = None,
        curated_dir: Path | None = None,
        card_snapshot: CorpusSnapshot | None = None,
    ) -> None:
        self._meta = meta
        self._settings = settings or EvidenceSettings()
        self._curated = load_curated(curated_dir)
        self._card_snapshot = card_snapshot

    def build(
        self,
        identity: CommanderIdentity,
        window: MetagameWindow | None = None,
    ) -> EvidencePackage:
        """Assemble the evidence package for one commander identity."""
        window = window or MetagameWindow()
        # UTC, not local: the window bound is compared against dates published
        # by another system, and a local "today" would silently shift it.
        now = datetime.now(UTC)
        since = now.date() - timedelta(days=window.days)

        availability = self._meta.availability()
        chunks: list[EvidenceChunk] = []
        events_seen = 0
        decks_seen = 0
        meta_snapshot = ""

        if availability.available:
            try:
                meta_snapshot = self._meta.snapshot().label
                chunks.extend(
                    self._tournament_chunks(identity, since, window, now, meta_snapshot)
                )
                chunks.extend(
                    self._inclusion_chunks(identity, since, window, now, meta_snapshot)
                )
                events_seen = len(
                    self._meta.events(
                        since=since, min_size=window.min_event_size, limit=200
                    )
                )
                commander = self._meta.commander(identity.key)
                decks_seen = commander.entries if commander else 0
            except RepositoryUnavailable as exc:
                logger.warning("cEDH evidence: meta unavailable: %s", exc)
                availability = MetaAvailability(available=False, detail=str(exc))

        chunks.extend(self._curated_chunks(identity, now))
        kept, dropped = self._bound(chunks)

        return EvidencePackage(
            commander_key=identity.key,
            commander_name=identity.display_name,
            window_days=window.days,
            min_event_size=window.min_event_size,
            chunks=tuple(kept),
            card_snapshot=(self._card_snapshot.label if self._card_snapshot else ""),
            meta_snapshot=meta_snapshot,
            meta_available=availability.available,
            meta_detail=availability.detail
            or (
                ""
                if availability.available
                else "no tournament evidence is available for this build"
            ),
            dropped_chunks=dropped,
            events_seen=events_seen,
            decks_seen=decks_seen,
        )

    # -- chunk builders ---------------------------------------------------

    def _tournament_chunks(
        self,
        identity: CommanderIdentity,
        since: date,
        window: MetagameWindow,
        now: datetime,
        snapshot: str,
    ) -> list[EvidenceChunk]:
        commander = self._meta.commander(identity.key)
        if commander is None:
            return []
        entries = self._meta.entries(
            identity.key,
            since=since,
            min_event_size=window.min_event_size,
            limit=25,
        )
        events = {
            e.event_id: e
            for e in self._meta.events(
                since=since, min_size=window.min_event_size, limit=200
            )
        }
        out = [
            EvidenceChunk.build(
                chunk_id="meta-presence",
                kind="tournament_fact",
                content=(
                    f"{identity.display_name}: {commander.entries} entries "
                    f"across {commander.events} events in the window "
                    f"({window.label}); {commander.top_cuts} top cuts, "
                    f"{commander.wins} event wins. Presence is exposure, not "
                    f"quality — it says how often the deck was brought."
                ),
                source="mtg_v1 tournament contract",
                source_id="mtg_v1.commander_identity",
                commander_key=identity.key,
                corpus_snapshot=snapshot,
                fetched_at=now,
                sample_size=commander.entries,
            )
        ]
        for entry in entries[:12]:
            event = events.get(entry.event_id)
            if event is None:
                continue
            out.append(
                EvidenceChunk.build(
                    chunk_id=f"meta-entry-{entry.entry_id}",
                    kind="tournament_fact",
                    content=(
                        f"{event.name} ({event.held_on}, {event.size} players): "
                        f"finished {entry.standing if entry.standing else 'n/a'} "
                        f"at {entry.wins}-{entry.losses}-{entry.draws}."
                    ),
                    source=event.source or "mtg_v1 tournament contract",
                    source_url=event.source_url,
                    source_id=f"mtg_v1.tournament_entry:{entry.entry_id}",
                    commander_key=identity.key,
                    corpus_snapshot=snapshot,
                    published_at=event.held_on,
                    fetched_at=now,
                    sample_size=event.size,
                )
            )
        return out

    def _inclusion_chunks(
        self,
        identity: CommanderIdentity,
        since: date,
        window: MetagameWindow,
        now: datetime,
        snapshot: str,
    ) -> list[EvidenceChunk]:
        facts = self._meta.inclusions(
            identity.key,
            since=since,
            min_event_size=window.min_event_size,
            limit=60,
        )
        if not facts:
            return []
        lines = [
            f"- {f.card_name}: {f.decks_including}/{f.decks} decks " f"({f.rate:.0%})"
            for f in facts[:40]
        ]
        denominator = max(f.decks for f in facts)
        return [
            EvidenceChunk.build(
                chunk_id="meta-inclusions",
                kind="inclusion_fact",
                content=(
                    "Card inclusion among tournament decks for "
                    f"{identity.display_name} ({window.label}, n={denominator} "
                    "decks). These are rates of play, not measures of card "
                    "quality:\n" + "\n".join(lines)
                ),
                source="mtg_v1 tournament contract",
                source_id="mtg_v1.commander_card_inclusion",
                commander_key=identity.key,
                corpus_snapshot=snapshot,
                fetched_at=now,
                sample_size=denominator,
            )
        ]

    def _curated_chunks(
        self, identity: CommanderIdentity, now: datetime
    ) -> list[EvidenceChunk]:
        out: list[EvidenceChunk] = []
        for doc in self._curated:
            scoped = doc.commander_keys or doc.commander_names
            matches = identity.key in doc.commander_keys or any(
                name in doc.commander_names for name in identity.names
            )
            if scoped and not matches:
                continue
            for index, section in enumerate(doc.sections):
                out.append(
                    EvidenceChunk.build(
                        chunk_id=f"{doc.doc_id}-{index}",
                        kind="curated_strategy",
                        content=section,
                        source=doc.source,
                        source_url=doc.source_url,
                        source_id=doc.source_id or f"curated:{doc.doc_id}",
                        commander_key=identity.key,
                        corpus_snapshot=f"curated:{doc.doc_id}",
                        published_at=doc.published_at,
                        fetched_at=doc.fetched_at or now,
                    )
                )
        return out

    # -- bounds -----------------------------------------------------------

    def _bound(
        self, chunks: Sequence[EvidenceChunk]
    ) -> tuple[list[EvidenceChunk], int]:
        """Apply the configured bounds. Returns the kept chunks and the drop count.

        Per-chunk truncation rewrites the content, so the hash is recomputed —
        a chunk whose hash describes text that was not sent is worse than no
        hash at all.
        """
        kept: list[EvidenceChunk] = []
        used = 0
        for chunk in chunks:
            if len(kept) >= self._settings.max_chunks:
                break
            content = chunk.content
            if len(content) > self._settings.max_chars_per_chunk:
                content = content[: self._settings.max_chars_per_chunk - 3] + "..."
                chunk = chunk.model_copy(
                    update={
                        "content": content,
                        "content_sha256": content_hash(content),
                    }
                )
            if used + len(content) > self._settings.max_total_chars:
                break
            kept.append(chunk)
            used += len(content)
        return kept, len(chunks) - len(kept)


def all_chunk_ids(chunks: Iterable[EvidenceChunk]) -> set[str]:
    """Return the set of chunk ids, for checking a model's citations."""
    return {chunk.chunk_id for chunk in chunks}
