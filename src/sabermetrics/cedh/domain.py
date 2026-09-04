"""The cEDH product model.

Casual power levels and budget-as-goal do not appear here, and neither does
price in any form. A cEDH request is a commander identity plus a strategy pack
plus explicit constraints, and the answer is a deck candidate with named win
packages and a role budget — a budget of *slots*, not of dollars.

Everything in this module is a value object: no I/O, no provider, no database.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- Vocabulary -----------------------------------------------------------

#: The deterministic role budget. These are the cEDH deckbuilding categories
#: that replace the casual generator's ramp/draw/removal/wincon buckets. Lands
#: are a role because in cEDH the land count *is* a strategy decision.
Role = Literal[
    "acceleration",
    "tutor",
    "interaction",
    "protection",
    "card_advantage",
    "win_package",
    "land",
    "flex",
]

ROLES: tuple[Role, ...] = (
    "acceleration",
    "tutor",
    "interaction",
    "protection",
    "card_advantage",
    "win_package",
    "land",
    "flex",
)


class WinPackageKind(str, Enum):
    """How a win package closes the game."""

    COMBO = "combo"
    LOOP = "loop"
    ATTRITION = "attrition"
    COMBAT = "combat"


class Legality(str, Enum):
    """Per-format legality, mirroring ``mtg_v1.card_legality.status``.

    ``UNKNOWN`` is ours, not the contract's: it means the repository had no row,
    which is different from a row saying ``not_legal``. The distinction is load
    bearing — an absent row is a corpus gap and must not read as a ruling.
    """

    LEGAL = "legal"
    NOT_LEGAL = "not_legal"
    RESTRICTED = "restricted"
    BANNED = "banned"
    UNKNOWN = "unknown"


# --- Commander identity ---------------------------------------------------


class CommanderIdentity(BaseModel):
    """One commander, or a legal commander pair, addressed by oracle_id.

    oracle_id rather than name throughout: names are not stable across the
    front-face/full-name split (``Bala Ged Recovery`` vs
    ``Bala Ged Recovery // Bala Ged Sanctuary``) and are not unique keys in the
    contract. Names are carried for display only.
    """

    model_config = ConfigDict(frozen=True)

    oracle_ids: tuple[str, ...] = Field(min_length=1, max_length=2)
    names: tuple[str, ...] = Field(min_length=1, max_length=2)
    color_identity: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _names_match_ids(self) -> CommanderIdentity:
        if len(self.names) != len(self.oracle_ids):
            raise ValueError(
                "CommanderIdentity needs one name per oracle_id "
                f"(got {len(self.names)} names, {len(self.oracle_ids)} ids)"
            )
        return self

    @property
    def key(self) -> str:
        """Stable identity key: sorted oracle_ids joined by '+'.

        Sorted so a partner pair addressed in either order is one identity.
        """
        return "+".join(sorted(self.oracle_ids))

    @property
    def display_name(self) -> str:
        return " + ".join(self.names)


# --- Strategy packs -------------------------------------------------------


class WinPackage(BaseModel):
    """A named way the deck wins, and the cards that assemble it."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: WinPackageKind
    #: Cards that must all be present for the package to be assembled.
    piece_oracle_ids: tuple[str, ...] = ()
    #: How the pilot converts the assembled state into a win. cEDH decks often
    #: assemble a state rather than cast a card that says "you win", and the
    #: simulator measures assembly, not victory — so the conversion is stated.
    converts_via: str = ""
    notes: str = ""


class RoleBudget(BaseModel):
    """Target count per role for the 99. Deterministic, not model-chosen."""

    model_config = ConfigDict(frozen=True)

    targets: dict[str, int]

    @model_validator(mode="after")
    def _known_roles_only(self) -> RoleBudget:
        unknown = set(self.targets) - set(ROLES)
        if unknown:
            raise ValueError(f"unknown roles in budget: {sorted(unknown)}")
        negative = sorted(r for r, n in self.targets.items() if n < 0)
        if negative:
            raise ValueError(f"negative role targets: {negative}")
        return self

    @property
    def total(self) -> int:
        return sum(self.targets.values())

    def target_for(self, role: str) -> int:
        return self.targets.get(role, 0)


class StrategyPack(BaseModel):
    """A curated, supported way to build one commander identity.

    A pack is *authored*, not inferred. Its card pool is the deterministic
    narrowing that the model is never allowed to bypass: the model may choose
    among packs, and may argue about cards inside the pool, but it cannot reach
    the corpus. ``pool`` maps oracle_id -> the roles that card can fill.
    """

    model_config = ConfigDict(frozen=True)

    pack_id: str
    name: str
    commander: CommanderIdentity
    summary: str
    primary_win_package: WinPackage
    secondary_win_packages: tuple[WinPackage, ...] = ()
    role_budget: RoleBudget
    #: oracle_id -> ordered roles that card is eligible for, best fit first.
    pool: dict[str, tuple[str, ...]]
    #: oracle_id -> deterministic priority within its role; higher wins first.
    priority: dict[str, float] = Field(default_factory=dict)
    #: Cards the pack will not build without, in priority order.
    auto_include: tuple[str, ...] = ()
    source: str = ""
    source_url: str = ""
    curated_at: date | None = None
    version: str = "1"

    @model_validator(mode="after")
    def _pool_roles_are_known(self) -> StrategyPack:
        for oracle_id, roles in self.pool.items():
            unknown = set(roles) - set(ROLES)
            if unknown:
                raise ValueError(
                    f"pack {self.pack_id}: card {oracle_id} has unknown "
                    f"roles {sorted(unknown)}"
                )
            if not roles:
                raise ValueError(f"pack {self.pack_id}: card {oracle_id} has no role")
        missing = [c for c in self.auto_include if c not in self.pool]
        if missing:
            raise ValueError(
                f"pack {self.pack_id}: auto_include cards absent from pool: "
                f"{sorted(missing)}"
            )
        return self

    @property
    def win_packages(self) -> tuple[WinPackage, ...]:
        return (self.primary_win_package, *self.secondary_win_packages)


# --- Request constraints --------------------------------------------------


class MetagameWindow(BaseModel):
    """Which tournament evidence counts, and how much of it there has to be.

    Both bounds are stated rather than defaulted silently: an evidence claim
    without a window and a sample size is not a claim about the metagame.
    """

    model_config = ConfigDict(frozen=True)

    days: int = Field(default=180, ge=1)
    min_event_size: int = Field(default=32, ge=0)

    @property
    def label(self) -> str:
        return f"last {self.days}d, events of {self.min_event_size}+"


class BuildConstraints(BaseModel):
    """User-supplied constraints on the build.

    **There is no budget, and there is no price field.** cEDH is proxy-normal:
    the expensive cards are proxied, so what a card costs to buy in paper says
    nothing about whether it wins a tournament round. A price constraint here
    would not trade money for power — it would just remove the best cards and
    call the result a deck.

    **Which cards the user already owns is not a constraint either.** Building
    a new deck means acquiring or proxying cards; that is the normal case, not
    a problem to route around. Preferring what is already in a binder would be
    a price constraint wearing a different hat, and it would make the same
    request produce different decks for different people.

    Note what is *not* in this class as much as what is. Every constraint below
    is about the deck: which cards the pilot refuses, which they insist on, how
    many slots they want left open, and which window of tournament evidence
    counts. The objective is performance and nothing competes with it.

    ``extra="forbid"`` is load-bearing rather than tidiness. Pydantic's default
    is to *ignore* unknown fields, so a caller still passing ``budget_usd`` — an
    old integration, a stale form, a contributor reaching for a setting that
    used to exist — would have it silently dropped and get a deck that ignored
    the constraint it asked for. Forbidding extras turns that into an error
    naming the field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: oracle_ids the user insists on, subject to legality and pool membership.
    must_include_oracle_ids: frozenset[str] = frozenset()
    #: oracle_ids the user refuses.
    excluded_oracle_ids: frozenset[str] = frozenset()
    #: Slots left deliberately open for the pilot. Subtracted from flex.
    flex_slots: int = Field(default=0, ge=0, le=20)
    metagame: MetagameWindow = MetagameWindow()

    @model_validator(mode="after")
    def _no_contradictory_card_lists(self) -> BuildConstraints:
        overlap = self.must_include_oracle_ids & self.excluded_oracle_ids
        if overlap:
            raise ValueError(f"cards both required and excluded: {sorted(overlap)}")
        return self


class LabRequest(BaseModel):
    """One deck-lab request: free text plus the constraints it resolves to."""

    model_config = ConfigDict(frozen=True)

    raw_intent: str = ""
    commander: CommanderIdentity | None = None
    pack_id: str | None = None
    constraints: BuildConstraints = BuildConstraints()
