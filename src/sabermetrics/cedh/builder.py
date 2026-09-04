"""Deterministic candidate construction.

Given a strategy pack, a card repository and a set of constraints, this builds
the 99. It is deterministic: the same inputs produce the same list, byte for
byte, and nothing here samples, scores stochastically, or calls a model. The
model's role begins after this function returns.

The order of operations is the whole design:

1. **Filter.** Legality, colour identity and user exclusions remove cards from
   the pack pool. A card whose legality is *unknown* is removed too, with a
   note — an unverifiable card in a tournament deck is a worse outcome than a
   missing one, and "we could not check" is not the same as "it is fine".
2. **Fill.** Auto-includes, then user-required cards, then each role up to its
   budget in deterministic priority order. Nothing here consults price:
   cEDH is proxy-normal and the objective is performance.
3. **Repair.** Exactly 99, singleton outside basic lands, colour identity a
   subset of the commander's. Repair is the invariant, not a warning: this
   mirrors the legality repair the legacy pipeline has always done.

Every departure from what was asked for becomes a note on the candidate. A
required card that was illegal, a role that could not be filled, a card the
pool could not supply — the candidate says so.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sabermetrics.cedh.candidate import (
    CandidateCard,
    CandidateProvenance,
    CandidateWinPackage,
    DeckCandidate,
    new_candidate_id,
)
from sabermetrics.cedh.domain import (
    ROLES,
    BuildConstraints,
    Legality,
    StrategyPack,
    WinPackage,
)
from sabermetrics.cedh.errors import CandidateConstraintViolation
from sabermetrics.cedh.evidence import EvidencePackage
from sabermetrics.cedh.repositories import CardFacts, CardRepository

logger = logging.getLogger(__name__)

BUILDER_VERSION = "1"

DECK_SIZE = 99

#: Basic land names, used by the deterministic shortfall repair. Duplicates of
#: these are legal, which is what makes them the only safe filler.
BASIC_LAND_BY_COLOR: dict[str, str] = {
    "W": "Plains",
    "U": "Island",
    "B": "Swamp",
    "R": "Mountain",
    "G": "Forest",
}
COLORLESS_BASIC = "Wastes"

#: Roles are filled in this order. Lands first because a land shortfall cannot
#: be repaired by anything except lands, and win packages before flex because
#: flex is by definition what is left.
FILL_ORDER: tuple[str, ...] = (
    "land",
    "acceleration",
    "win_package",
    "interaction",
    "tutor",
    "card_advantage",
    "protection",
    "flex",
)


class _Selection:
    """Mutable working set for one build."""

    def __init__(self) -> None:
        self.chosen: dict[str, CandidateCard] = {}
        self.notes: list[str] = []

    def add(
        self,
        facts: CardFacts,
        role: str,
        source: str,
        priority: float,
        *,
        quantity: int = 1,
    ) -> None:
        self.chosen[facts.oracle_id] = CandidateCard(
            oracle_id=facts.oracle_id,
            name=facts.name,
            role=role,
            quantity=quantity,
            source=source,  # type: ignore[arg-type]
            priority=priority,
        )

    @property
    def size(self) -> int:
        return sum(c.quantity for c in self.chosen.values())


def _sort_key(
    oracle_id: str,
    pack: StrategyPack,
    role: str | None = None,
) -> tuple[int, float, str]:
    """Deterministic ordering for one role's candidates.

    Primary role, then authored priority, then oracle_id. Three terms, all
    about the card: what it does, how good the pack's author judged it, and a
    stable tiebreak. Nothing here knows anything about the person asking.

    The primary-role term is what keeps the authored role budget intact. A card
    with two roles — Basalt Monolith is acceleration *and* a win-package piece,
    Sink into Stupor is interaction with a land back face — would otherwise be
    pulled into whichever role is filled first purely on priority, and every
    later role would come up short while the repair quietly put the displaced
    cards back under a different label. Secondary-role cards are still
    available; they are just the fallback, which is what "secondary" meant in
    the pack.

    The final term is ``oracle_id`` rather than anything about the user. A
    build is reproducible from the pack alone: two people asking for the same
    pack get the same 99, which is what makes a candidate hash mean something.
    """
    roles = pack.pool.get(oracle_id, ())
    is_primary = 0 if role is not None and roles and roles[0] == role else 1
    return (is_primary, -pack.priority.get(oracle_id, 0.0), oracle_id)


def build_candidate(
    pack: StrategyPack,
    cards: CardRepository,
    *,
    constraints: BuildConstraints | None = None,
    evidence: EvidencePackage | None = None,
    candidate_id: str | None = None,
    generated_at: datetime | None = None,
) -> DeckCandidate:
    """Build one deterministic cEDH deck candidate.

    Args:
        pack: The resolved strategy pack. Its pool is the only card source.
        cards: Card facts and legality.
        constraints: User constraints. There is no budget among them.
        evidence: Evidence package, for provenance only. Nothing here reads
            its contents — evidence informs prose, not card selection.
        candidate_id: Override the generated id, for reproducible tests.
        generated_at: Override the timestamp, for reproducible tests.

    Returns:
        A candidate holding exactly 99 legal, singleton cards.

    Raises:
        CandidateConstraintViolation: The pool cannot produce a legal 99 even
            after repair — for example a colourless commander with no basic
            land available to fill a shortfall.
    """
    constraints = constraints or BuildConstraints()
    selection = _Selection()

    pool_ids = list(pack.pool)
    facts_by_id = cards.get_by_oracle_ids(pool_ids)
    legality = cards.legality(pool_ids, "commander")
    commander_colors = set(pack.commander.color_identity)
    commander_ids = set(pack.commander.oracle_ids)

    eligible: dict[str, CardFacts] = {}
    for oracle_id in pool_ids:
        facts = facts_by_id.get(oracle_id)
        if facts is None:
            selection.notes.append(
                f"pool card {oracle_id} is absent from the card corpus and "
                "was skipped"
            )
            continue
        if oracle_id in commander_ids:
            continue
        if oracle_id in constraints.excluded_oracle_ids:
            continue
        status = legality.get(oracle_id, Legality.UNKNOWN)
        if status is not Legality.LEGAL:
            selection.notes.append(
                f"{facts.name} excluded: Commander legality is "
                f"{status.value}"
                + (
                    " (no legality row in the corpus, which is not the same as"
                    " a ruling that it is illegal)"
                    if status is Legality.UNKNOWN
                    else ""
                )
            )
            continue
        if not set(facts.color_identity) <= commander_colors:
            selection.notes.append(
                f"{facts.name} excluded: colour identity "
                f"{''.join(facts.color_identity) or 'C'} is outside the "
                f"commander's {''.join(sorted(commander_colors)) or 'C'}"
            )
            continue
        eligible[oracle_id] = facts

    # -- 1. auto-includes -------------------------------------------------
    for oracle_id in pack.auto_include:
        facts = eligible.get(oracle_id)
        if facts is None:
            continue
        if selection.size >= DECK_SIZE:
            break
        role = pack.pool[oracle_id][0]
        selection.add(facts, role, "auto_include", pack.priority.get(oracle_id, 0.0))

    # -- 2. user-required cards -------------------------------------------
    for oracle_id in sorted(constraints.must_include_oracle_ids):
        if oracle_id in selection.chosen:
            continue
        facts = eligible.get(oracle_id)
        if facts is None:
            selection.notes.append(
                f"requested card {oracle_id} is not in this strategy pack's "
                "pool, or did not pass the legality and colour-identity "
                "filters, and was not added"
            )
            continue
        if selection.size >= DECK_SIZE:
            selection.notes.append(
                f"requested card {facts.name} did not fit in 99 slots"
            )
            continue
        selection.add(
            facts,
            pack.pool[oracle_id][0],
            "user_required",
            pack.priority.get(oracle_id, 0.0),
        )

    # -- 3. role fill ------------------------------------------------------
    targets = _effective_targets(pack, constraints)
    for role in FILL_ORDER:
        target = targets.get(role, 0)
        have = sum(1 for c in selection.chosen.values() if c.role == role)
        if have >= target:
            continue
        candidates = sorted(
            (
                oid
                for oid, facts in eligible.items()
                if oid not in selection.chosen and role in pack.pool[oid]
            ),
            key=lambda oid: _sort_key(oid, pack, role),
        )
        for oracle_id in candidates:
            if have >= target or selection.size >= DECK_SIZE:
                break
            selection.add(
                eligible[oracle_id], role, "pool", pack.priority.get(oracle_id, 0.0)
            )
            have += 1
        if have < target:
            selection.notes.append(
                f"role '{role}' is short: {have} of {target} slots filled "
                "from this pack's pool"
            )

    # -- 4. deterministic legality repair ---------------------------------
    _repair(selection, pack, cards, eligible, constraints)

    win_packages = tuple(
        _assess_package(package, set(selection.chosen)) for package in pack.win_packages
    )
    role_counts: dict[str, int] = {}
    for card in selection.chosen.values():
        role_counts[card.role] = role_counts.get(card.role, 0) + card.quantity

    snapshot = cards.snapshot()
    provenance = CandidateProvenance(
        pack_id=pack.pack_id,
        pack_version=pack.version,
        pack_source=pack.source,
        pack_source_url=pack.source_url,
        card_snapshot=snapshot.label,
        meta_snapshot=evidence.meta_snapshot if evidence else "",
        meta_available=bool(evidence and evidence.meta_available),
        evidence_hash=evidence.evidence_hash if evidence else "",
        window_days=evidence.window_days if evidence else 0,
        min_event_size=evidence.min_event_size if evidence else 0,
        builder_version=BUILDER_VERSION,
    )

    return DeckCandidate(
        candidate_id=candidate_id or new_candidate_id(),
        generated_at=generated_at or datetime.now(UTC),
        commander=pack.commander,
        cards=tuple(sorted(selection.chosen.values(), key=lambda c: c.oracle_id)),
        win_packages=win_packages,
        role_counts=role_counts,
        provenance=provenance,
        notes=tuple(selection.notes),
    )


def _effective_targets(
    pack: StrategyPack, constraints: BuildConstraints
) -> dict[str, int]:
    """Role targets after user flex slots and normalisation to 99.

    User flex slots come out of the pack's flex budget first, and out of the
    largest remaining role only if flex cannot cover them — a request for open
    slots must not quietly cut the land count.
    """
    targets: dict[str, int] = {
        role: pack.role_budget.target_for(role) for role in ROLES
    }
    reserve = constraints.flex_slots
    take = min(reserve, targets.get("flex", 0))
    targets["flex"] = targets.get("flex", 0) - take
    reserve -= take
    while reserve > 0:
        role = max(
            (r for r in FILL_ORDER if r != "land"),
            key=lambda r: (targets.get(r, 0), r),
        )
        if targets.get(role, 0) <= 0:
            break
        targets[role] -= 1
        reserve -= 1
    return targets


def _repair(
    selection: _Selection,
    pack: StrategyPack,
    cards: CardRepository,
    eligible: dict[str, CardFacts],
    constraints: BuildConstraints,
) -> None:
    """Force the invariants: exactly 99, singleton, in colour identity.

    Over-full lists are trimmed lowest-priority-first, never touching an
    auto-include or a user-required card. Short lists are filled first from
    whatever is left in the pool and then, only as a last resort, with basic
    lands — which are the one card type where duplicates are legal.
    """
    # Trim.
    if selection.size > DECK_SIZE:
        protected = {"auto_include", "user_required"}
        removable = sorted(
            (c for c in selection.chosen.values() if c.source not in protected),
            key=lambda c: (c.priority, c.oracle_id),
        )
        for card in removable:
            if selection.size <= DECK_SIZE:
                break
            selection.chosen.pop(card.oracle_id, None)
            selection.notes.append(f"{card.name} trimmed to reach 99 cards")

    # Fill from the remaining pool, best first.
    if selection.size < DECK_SIZE:
        leftovers = sorted(
            (oid for oid in eligible if oid not in selection.chosen),
            key=lambda oid: _sort_key(oid, pack),
        )
        added: list[str] = []
        for oracle_id in leftovers:
            if selection.size >= DECK_SIZE:
                break
            facts = eligible[oracle_id]
            selection.add(
                facts,
                pack.pool[oracle_id][0],
                "repair",
                pack.priority.get(oracle_id, 0.0),
            )
            added.append(facts.name)
        if added:
            selection.notes.append(
                f"{len(added)} card(s) added outside the role budget to reach "
                f"99: {', '.join(sorted(added))}"
            )

    # Fill the remainder with basics.
    shortfall = DECK_SIZE - selection.size
    if shortfall > 0:
        # A deck can never legitimately need more basic lands than it has land
        # slots. Past that the constraints did not produce a deck, and handing
        # back 89 Forests would be a build that looks successful and is not.
        cap = max(pack.role_budget.target_for("land"), 1)
        if shortfall > cap:
            raise CandidateConstraintViolation(
                f"pack {pack.pack_id!r} could only fill {selection.size} of 99 "
                f"slots, leaving a {shortfall}-card shortfall that exceeds the "
                f"pack's {cap} land slots. The constraints cannot produce a "
                "deck from this pool — check the exclusion list and whether "
                "the pack resolves against the current corpus."
            )
        _fill_with_basics(selection, pack, cards, shortfall)

    if selection.size != DECK_SIZE:
        raise CandidateConstraintViolation(
            f"pack {pack.pack_id!r} produced {selection.size} cards and the "
            "deterministic repair could not reach 99. This is a pack or "
            "corpus defect, not a build the user should be shown."
        )


def _fill_with_basics(
    selection: _Selection,
    pack: StrategyPack,
    cards: CardRepository,
    shortfall: int,
) -> None:
    """Add basic lands in the commander's colours to reach 99."""
    colors = [
        c for c in ("W", "U", "B", "R", "G") if c in pack.commander.color_identity
    ]
    names = [BASIC_LAND_BY_COLOR[c] for c in colors] if colors else [COLORLESS_BASIC]
    resolved = cards.resolve_names(names)
    available = [resolved[n] for n in names if n in resolved]
    if not available:
        raise CandidateConstraintViolation(
            f"pack {pack.pack_id!r} is {shortfall} cards short and no basic "
            f"land among {names} resolved against the corpus, so the "
            "shortfall cannot be repaired"
        )
    per_basic = _distribute(shortfall, len(available))
    for facts, count in zip(available, per_basic, strict=True):
        if count <= 0:
            continue
        existing = selection.chosen.get(facts.oracle_id)
        # add() replaces the entry, so an already-chosen basic keeps its count.
        quantity = count + (existing.quantity if existing else 0)
        selection.add(facts, "land", "repair", -1.0, quantity=quantity)
    selection.notes.append(
        f"{shortfall} basic land(s) added by the legality repair to reach 99"
    )


def _distribute(total: int, buckets: int) -> list[int]:
    """Split ``total`` across ``buckets`` as evenly as possible, deterministically."""
    base, remainder = divmod(total, buckets)
    return [base + (1 if i < remainder else 0) for i in range(buckets)]


def _assess_package(package: WinPackage, chosen: set[str]) -> CandidateWinPackage:
    """Report whether a win package is actually assembled by the built list."""
    missing = tuple(sorted(p for p in package.piece_oracle_ids if p not in chosen))
    return CandidateWinPackage(
        name=package.name,
        kind=package.kind,
        piece_oracle_ids=package.piece_oracle_ids,
        converts_via=package.converts_via,
        complete=not missing,
        missing_oracle_ids=missing,
    )
