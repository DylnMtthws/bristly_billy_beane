"""``cedh-deck-candidate.v1`` — the deck candidate this repository exports.

Cards are addressed by **oracle_id**, never by name. Names are carried for
display and are not the key: they are not unique across the corpus, and the
contract publishes the joined name for multi-faced cards, so a name-keyed
handoff silently loses a card at the far end.

The document is self-describing: it states the schema id, the pack and pack
version it was built from, the corpus snapshot the card facts came from, and
the evidence hash behind any prose attached to it. A stored candidate can be
told apart from one built a week later against a later nightly.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sabermetrics.cedh.domain import CommanderIdentity, WinPackageKind

SCHEMA_ID: Final = "cedh-deck-candidate.v1"

#: How a card got into the candidate. ``repair`` means the deterministic
#: legality repair put it there, which is a fact about the build worth keeping.
CardSource = Literal["auto_include", "pool", "user_required", "repair"]


class CandidateCard(BaseModel):
    """One card in the 99."""

    model_config = ConfigDict(frozen=True)

    oracle_id: str
    name: str
    role: str
    quantity: int = Field(default=1, ge=1)
    source: CardSource = "pool"
    #: Deterministic priority the builder used. Present so a reviewer can see
    #: why this card beat the next one without re-running the build.
    priority: float = 0.0


class CandidateWinPackage(BaseModel):
    """A win package and whether the built list actually assembles it."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: WinPackageKind
    piece_oracle_ids: tuple[str, ...] = ()
    converts_via: str = ""
    #: True only if every piece is in the built list. Computed, not asserted.
    complete: bool = False
    missing_oracle_ids: tuple[str, ...] = ()


class CandidateProvenance(BaseModel):
    """Where every part of this candidate came from."""

    model_config = ConfigDict(frozen=True)

    pack_id: str
    pack_version: str
    pack_source: str = ""
    pack_source_url: str = ""
    card_snapshot: str = ""
    meta_snapshot: str = ""
    meta_available: bool = False
    evidence_hash: str = ""
    window_days: int = 0
    min_event_size: int = 0
    builder_version: str = "1"
    simulator_version: str = ""
    simulator_result_schema: str = ""
    simulator_cards_sha256: str = ""
    simulator_threads: str = ""


class DeckCandidate(BaseModel):
    """A complete, legality-checked cEDH deck candidate."""

    model_config = ConfigDict(frozen=True)

    schema_id: Literal["cedh-deck-candidate.v1"] = SCHEMA_ID
    candidate_id: str
    generated_at: datetime
    commander: CommanderIdentity
    cards: tuple[CandidateCard, ...]
    win_packages: tuple[CandidateWinPackage, ...] = ()
    role_counts: dict[str, int] = Field(default_factory=dict)
    provenance: CandidateProvenance
    #: Anything the builder had to say about the build — a role it could not
    #: fill, a user request it could not honour. Never silently empty.
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _singleton_and_size(self) -> DeckCandidate:
        total = sum(card.quantity for card in self.cards)
        if total != 99:
            raise ValueError(
                f"a Commander candidate holds 99 cards beside the commander, "
                f"got {total}"
            )
        ids = [c.oracle_id for c in self.cards]
        if len(ids) != len(set(ids)):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate oracle_ids in candidate: {duplicates}")
        overlap = set(ids) & set(self.commander.oracle_ids)
        if overlap:
            raise ValueError(f"the commander appears in its own 99: {sorted(overlap)}")
        return self

    @property
    def deck_sha256(self) -> str:
        """Hash of the 99 plus the commander, order-independent.

        The simulator stamps a deck hash on its results; matching on this is
        how a stored simulation is known to describe *this* list rather than a
        list that has since changed.
        """
        digest = hashlib.sha256()
        for oracle_id in sorted(self.commander.oracle_ids):
            digest.update(f"C:{oracle_id}\n".encode())
        for card in sorted(self.cards, key=lambda c: c.oracle_id):
            digest.update(f"{card.oracle_id}:{card.quantity}\n".encode())
        return digest.hexdigest()

    def to_document(self) -> dict[str, Any]:
        """Render the versioned export document."""
        return {
            "schema": SCHEMA_ID,
            "candidate_id": self.candidate_id,
            "generated_at": self.generated_at.isoformat(),
            "deck_sha256": self.deck_sha256,
            "commander": {
                "oracle_ids": list(self.commander.oracle_ids),
                "names": list(self.commander.names),
                "color_identity": list(self.commander.color_identity),
            },
            "cards": [
                {
                    "oracle_id": c.oracle_id,
                    "name": c.name,
                    "role": c.role,
                    "quantity": c.quantity,
                    "source": c.source,
                }
                for c in sorted(self.cards, key=lambda c: c.oracle_id)
            ],
            "win_packages": [
                {
                    "name": w.name,
                    "kind": w.kind.value,
                    "piece_oracle_ids": list(w.piece_oracle_ids),
                    "converts_via": w.converts_via,
                    "complete": w.complete,
                    "missing_oracle_ids": list(w.missing_oracle_ids),
                }
                for w in self.win_packages
            ],
            "role_counts": dict(self.role_counts),
            "provenance": self.provenance.model_dump(mode="json"),
            "notes": list(self.notes),
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Serialise the export document."""
        return json.dumps(self.to_document(), indent=indent, sort_keys=False)


def new_candidate_id() -> str:
    """Return a time-ordered candidate id."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"cand-{stamp}-{hashlib.sha256(stamp.encode()).hexdigest()[:8]}"
