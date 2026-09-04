"""The request half of the simulator contract.

This module exists because the previous boundary pinned only the *response*.
The request body was described in prose, this repository built its own reading
of it from that prose, the simulator built another, each suite tested its own
shape against itself and stayed green, and every real deck build failed on the
wire with ``422 unsupported candidate schema_version``.

So: the documents this repository *sends* are constructed here, in one place,
against schemas vendored verbatim from the simulator
(``fixtures/cedh/contracts/``), and validated against those schemas in the
tests. Nothing else in this repository builds a simulator request.

Two documents, both versioned:

* ``cedh-deck-candidate.v2`` — the deck, addressed by ``oracle_id``.
* ``cedh-simulation-request.v1`` — the ``POST /simulate`` envelope around it.

Note what the candidate carries and what it does not. ``deck_sha256`` is the
deck list and only the deck list (ADR-025). ``strategy_pack_id`` travels
beside it as a *request* for an execution context, not as part of the deck's
identity — the two were fused in v1 under one field called ``candidate_hash``,
and fusing them meant an identical deck run under a different pack reported as
a different deck.
"""

from __future__ import annotations

from typing import Any

from sabermetrics.cedh.candidate import DeckCandidate

CANDIDATE_SCHEMA_ID = "cedh-deck-candidate.v2"
REQUEST_SCHEMA_ID = "cedh-simulation-request.v1"

#: The simulator's only v1 scenario. A controlled-disruption model must take a
#: new id rather than change what this one means.
SCENARIO_ID = "goldfish_assembly.v1"

#: Placeholder used where this repository has no real digest to publish. The
#: simulator requires provenance to be *shaped* correctly but does not
#: interpret it, and a fabricated-looking constant is preferable to inventing a
#: digest that would read as a real one.
_UNKNOWN_DIGEST = "sha256:" + "0" * 64


def _digest_or_placeholder(value: str) -> str:
    """Render a 64-hex digest in wire form, or say plainly that we lack one."""
    cleaned = value.removeprefix("sha256:").strip().lower()
    if len(cleaned) == 64 and all(char in "0123456789abcdef" for char in cleaned):
        return f"sha256:{cleaned}"
    return _UNKNOWN_DIGEST


def _snapshot_timestamp(value: str) -> str:
    """The contract wants a date-time; a corpus label is not one."""
    return value if value.endswith("Z") or "T" in value else "1970-01-01T00:00:00Z"


def candidate_document(candidate: DeckCandidate) -> dict[str, Any]:
    """Render a ``cedh-deck-candidate.v2`` document.

    Args:
        candidate: The built candidate.

    Returns:
        The document the simulator parses. Cards are addressed by
        ``oracle_id``; names are display-only and are deliberately absent,
        because names are not unique across the corpus and a name-keyed
        handoff loses multi-faced cards at the far end.
    """
    provenance = candidate.provenance
    snapshot_at = _snapshot_timestamp(provenance.card_snapshot)
    return {
        "schema_version": CANDIDATE_SCHEMA_ID,
        "candidate_id": candidate.candidate_id,
        "commander_oracle_ids": sorted(candidate.commander.oracle_ids),
        "library": [
            {"oracle_id": card.oracle_id, "quantity": card.quantity}
            for card in sorted(candidate.cards, key=lambda c: c.oracle_id)
        ],
        "strategy_pack_id": provenance.simulator_pack_id,
        "strategy_pack_version": provenance.simulator_pack_version,
        "provenance": {
            "producer": {
                "name": "bristly_billy_beane",
                "version": provenance.builder_version,
            },
            "card_data": {
                "source": provenance.card_snapshot or "unknown",
                "snapshot_at": snapshot_at,
                "hash": _digest_or_placeholder(provenance.evidence_hash),
            },
            "corpus": {
                "source": provenance.card_snapshot or "unknown",
                "snapshot_at": snapshot_at,
                "hash": _digest_or_placeholder(provenance.evidence_hash),
            },
        },
        # The deck list only. The strategy pack above is deliberately not in
        # here; see fixtures/cedh/contracts/hash-golden-vectors.json.
        "deck_sha256": candidate.deck_sha256_wire,
    }


def simulation_request(
    candidate: DeckCandidate,
    *,
    games: int,
    turn: int,
    seed: int | None = None,
    sweep: bool = False,
    ablate: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Render a ``cedh-simulation-request.v1`` envelope.

    Args:
        candidate: The candidate to simulate.
        games: Games to run.
        turn: The assembly horizon to report against.
        seed: Explicit seed. Defaults to one derived from the deck hash, so a
            given list is measured identically every time it is submitted
            without the caller having to remember a number.
        sweep: Request the full leave-one-out ablation set.
        ablate: Specific card names to ablate. Mutually exclusive with
            ``sweep``; the service returns 400 rather than picking one.
    """
    if sweep and ablate:
        raise ValueError("sweep and a non-empty ablate list are mutually exclusive")
    return {
        "schema_version": REQUEST_SCHEMA_ID,
        "candidate": candidate_document(candidate),
        "games": games,
        "turn": turn,
        "seed": int(candidate.deck_sha256[:16], 16) if seed is None else seed,
        "scenario": SCENARIO_ID,
        "sweep": sweep,
        "ablate": list(ablate),
    }
