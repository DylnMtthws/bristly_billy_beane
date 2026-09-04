"""Strategy packs: the deterministic narrowing the model cannot bypass.

A pack is authored **by card name**, because that is what a person curating a
cEDH list actually writes, and resolved to **oracle_ids** at load time through
:class:`~sabermetrics.cedh.repositories.CardRepository`. Everything downstream
of the registry — the builder, the candidate, the simulator handoff — is
oracle_id only.

Resolution failures are reported, not dropped. A pack that names a card the
corpus cannot resolve is a defect in the pack or a gap in the corpus, and both
are worth seeing; silently building a 99 out of the names that happened to
resolve is how a list quietly becomes a different list.

Packs are the answer to "select from the entire card corpus": the model may
choose *among* packs and argue about cards *inside* one, and has no path to the
corpus at all.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.cedh.domain import (
    CommanderIdentity,
    RoleBudget,
    StrategyPack,
    WinPackage,
    WinPackageKind,
)
from sabermetrics.cedh.errors import UnsupportedCommander
from sabermetrics.cedh.repositories import CardRepository

logger = logging.getLogger(__name__)

PACKS_DIR = Path(__file__).resolve().parents[3] / "config" / "cedh_packs"


class PackCardEntry(BaseModel):
    """One card in an authored pack, by name."""

    model_config = ConfigDict(frozen=True)

    name: str
    roles: tuple[str, ...]
    #: Higher wins its role slot first. Deterministic; nothing samples it.
    priority: float = 0.0


class PackWinPackage(BaseModel):
    """An authored win package, by card name."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: WinPackageKind
    pieces: tuple[str, ...] = ()
    converts_via: str = ""
    notes: str = ""


class PackDefinition(BaseModel):
    """The checked-in YAML form of a pack: names, not ids."""

    model_config = ConfigDict(frozen=True)

    pack_id: str
    name: str
    summary: str
    commander_names: tuple[str, ...]
    primary_win_package: PackWinPackage
    secondary_win_packages: tuple[PackWinPackage, ...] = ()
    role_targets: dict[str, int]
    cards: tuple[PackCardEntry, ...]
    auto_include: tuple[str, ...] = ()
    source: str = ""
    source_url: str = ""
    curated_at: date | None = None
    version: str = "1"
    #: Which strategy pack the *simulator* should run this list under. Its
    #: pack namespace is not ours: our ``kinnan_basalt`` is the simulator's
    #: ``kinnan-midrange-goldfish``, and nothing derives one from the other.
    #: The default asks for explicit derived execution, so a pack nobody has
    #: deliberately mapped is never run under commander-specific logic.
    simulator_pack_id: str = "derived-generic"
    simulator_pack_version: str = "1.0.0"


class UnresolvedPack(BaseModel):
    """A pack that could not be fully resolved against the corpus."""

    model_config = ConfigDict(frozen=True)

    pack_id: str
    missing_names: tuple[str, ...]
    detail: str = ""


def load_definitions(directory: Path | None = None) -> list[PackDefinition]:
    """Load every authored pack definition from ``directory``."""
    root = directory or PACKS_DIR
    if not root.exists():
        return []
    out: list[PackDefinition] = []
    for path in sorted(root.glob("*.yaml")):
        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        raw.setdefault("pack_id", path.stem)
        out.append(PackDefinition(**raw))
    return out


class PackRegistry:
    """Resolves authored packs against a card repository, and reports failures.

    Args:
        cards: The card repository used to resolve names to oracle_ids.
        directory: Where the authored YAML lives.
    """

    def __init__(self, cards: CardRepository, directory: Path | None = None) -> None:
        self._cards = cards
        self._definitions = {d.pack_id: d for d in load_definitions(directory)}
        self._resolved: dict[str, StrategyPack] = {}
        self._unresolved: dict[str, UnresolvedPack] = {}

    # -- inspection -------------------------------------------------------

    @property
    def definition_ids(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def definition(self, pack_id: str) -> PackDefinition | None:
        return self._definitions.get(pack_id)

    def supported_pack_ids(self) -> tuple[str, ...]:
        """Pack ids that resolve cleanly against the current corpus."""
        return tuple(
            pack_id for pack_id in self._definitions if self._try(pack_id) is not None
        )

    def unresolved(self, pack_id: str) -> UnresolvedPack | None:
        """Why a pack did not resolve, if it did not."""
        self._try(pack_id)
        return self._unresolved.get(pack_id)

    def packs_for_commander(self, identity_key: str) -> list[StrategyPack]:
        """Every resolved pack whose commander identity matches."""
        out = []
        for pack_id in self._definitions:
            pack = self._try(pack_id)
            if pack is not None and pack.commander.key == identity_key:
                out.append(pack)
        return out

    # -- resolution -------------------------------------------------------

    def get(self, pack_id: str) -> StrategyPack:
        """Return a resolved pack.

        Raises:
            UnsupportedCommander: The pack does not exist, or does not resolve.
        """
        pack = self._try(pack_id)
        if pack is None:
            failure = self._unresolved.get(pack_id)
            if failure is None:
                raise UnsupportedCommander(
                    f"no strategy pack named {pack_id!r}. Supported: "
                    f"{sorted(self._definitions)}"
                )
            raise UnsupportedCommander(
                f"strategy pack {pack_id!r} does not resolve against the "
                f"current corpus: {failure.detail}"
            )
        return pack

    def _try(self, pack_id: str) -> StrategyPack | None:
        if pack_id in self._resolved:
            return self._resolved[pack_id]
        if pack_id in self._unresolved:
            return None
        definition = self._definitions.get(pack_id)
        if definition is None:
            return None
        try:
            pack = resolve_pack(definition, self._cards)
        except UnsupportedCommander as exc:
            missing = getattr(exc, "missing_names", ())
            self._unresolved[pack_id] = UnresolvedPack(
                pack_id=pack_id,
                missing_names=tuple(missing),
                detail=str(exc),
            )
            logger.warning("cEDH pack %s did not resolve: %s", pack_id, exc)
            return None
        self._resolved[pack_id] = pack
        return pack


class _PackResolutionError(UnsupportedCommander):
    """Internal: carries the unresolved names alongside the message."""

    def __init__(self, message: str, missing_names: Sequence[str]) -> None:
        super().__init__(message)
        self.missing_names = tuple(missing_names)


def resolve_pack(definition: PackDefinition, cards: CardRepository) -> StrategyPack:
    """Resolve an authored pack to oracle_ids.

    Args:
        definition: The authored pack.
        cards: Repository used for name resolution.

    Returns:
        The resolved pack, keyed by oracle_id throughout.

    Raises:
        UnsupportedCommander: Any commander name, pool card or win-package
            piece failed to resolve. The message names every missing card.
    """
    wanted = [
        *definition.commander_names,
        *[c.name for c in definition.cards],
    ]
    for package in (
        definition.primary_win_package,
        *definition.secondary_win_packages,
    ):
        wanted.extend(package.pieces)

    resolved = cards.resolve_names(list(dict.fromkeys(wanted)))
    missing = [name for name in dict.fromkeys(wanted) if name not in resolved]
    if missing:
        raise _PackResolutionError(
            f"pack {definition.pack_id!r} names {len(missing)} card(s) the "
            f"corpus did not resolve: {sorted(missing)}",
            missing,
        )

    commander_facts = [resolved[n] for n in definition.commander_names]
    identity = CommanderIdentity(
        oracle_ids=tuple(f.oracle_id for f in commander_facts),
        names=tuple(f.name for f in commander_facts),
        color_identity=tuple(
            sorted({c for f in commander_facts for c in f.color_identity})
        ),
    )

    pool: dict[str, tuple[str, ...]] = {}
    priority: dict[str, float] = {}
    for entry in definition.cards:
        oracle_id = resolved[entry.name].oracle_id
        pool[oracle_id] = tuple(entry.roles)
        priority[oracle_id] = entry.priority

    def _package(package: PackWinPackage) -> WinPackage:
        return WinPackage(
            name=package.name,
            kind=package.kind,
            piece_oracle_ids=tuple(resolved[p].oracle_id for p in package.pieces),
            converts_via=package.converts_via,
            notes=package.notes,
        )

    return StrategyPack(
        pack_id=definition.pack_id,
        name=definition.name,
        commander=identity,
        summary=definition.summary,
        primary_win_package=_package(definition.primary_win_package),
        secondary_win_packages=tuple(
            _package(p) for p in definition.secondary_win_packages
        ),
        role_budget=RoleBudget(targets=dict(definition.role_targets)),
        pool=pool,
        priority=priority,
        auto_include=tuple(
            resolved[name].oracle_id for name in definition.auto_include
        ),
        source=definition.source,
        source_url=definition.source_url,
        curated_at=definition.curated_at,
        version=definition.version,
        simulator_pack_id=definition.simulator_pack_id,
        simulator_pack_version=definition.simulator_pack_version,
    )


class PackSummary(BaseModel):
    """Display-facing summary of a pack's support status."""

    model_config = ConfigDict(frozen=True)

    pack_id: str
    name: str
    commander_names: tuple[str, ...]
    summary: str
    supported: bool
    detail: str = ""
    simulator_supported: bool = False
    missing_names: tuple[str, ...] = Field(default_factory=tuple)


def summarize(
    registry: PackRegistry, simulator_keys: frozenset[str] = frozenset()
) -> list[PackSummary]:
    """Summarise every authored pack, supported or not.

    Unsupported packs are listed rather than hidden: "we do not support this
    commander" is an answer, and an empty list is not.
    """
    out: list[PackSummary] = []
    for pack_id in registry.definition_ids:
        definition = registry.definition(pack_id)
        if definition is None:  # pragma: no cover - definition_ids guarantees it
            continue
        failure = registry.unresolved(pack_id)
        if failure is None:
            pack = registry.get(pack_id)
            out.append(
                PackSummary(
                    pack_id=pack_id,
                    name=definition.name,
                    commander_names=definition.commander_names,
                    summary=definition.summary,
                    supported=True,
                    simulator_supported=pack.commander.key in simulator_keys,
                )
            )
        else:
            out.append(
                PackSummary(
                    pack_id=pack_id,
                    name=definition.name,
                    commander_names=definition.commander_names,
                    summary=definition.summary,
                    supported=False,
                    detail=failure.detail,
                    missing_names=failure.missing_names,
                )
            )
    return out
