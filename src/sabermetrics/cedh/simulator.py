"""The simulator boundary: JSON in, JSON out, subprocess or fixture.

This repository orchestrates simulation. It does not own simulation mechanics
or strategy definitions, does not import the simulator's source, and does not
reimplement any part of its model.

Two states, and only two:

* :class:`SimulationResult` — a validated ``cedh-simulation-result.v1``
  document from the simulator.
* :class:`NotSimulated` — a named reason why there is no result.

There is deliberately no third state where a missing simulator becomes a
neutral score. A neutral number is indistinguishable from a measured one once
it is in a table, and it would be the only number in the product that means
nothing. An unsupported commander is likewise shown as unsupported, not as a
zero.

The simulator's own framing travels with its numbers: the metric is
``goldfish_turns_to_assembly``, it plays alone, and it cannot see every card in
a list. :class:`SimulationResult` carries the honesty fields as required
values, so a caller cannot render the probability without having been handed
what it excludes.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Final, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.cedh.candidate import DeckCandidate
from sabermetrics.cedh.errors import SimulatorContractError
from sabermetrics.cedh.settings import SimulatorSettings

logger = logging.getLogger(__name__)

RESULT_SCHEMA_ID: Final = "cedh-simulation-result.v1"

#: Why a candidate was not simulated. A closed set: an unmodelled reason is a
#: reason nobody chose to state.
NotSimulatedReason = Literal[
    "simulator_disabled",
    "simulator_unavailable",
    "commander_unsupported",
    "deck_mismatch",
    "simulator_failed",
    "contract_violation",
]


class InertCard(BaseModel):
    """A card the simulator's model cannot see, and why."""

    model_config = ConfigDict(frozen=True)

    oracle_id: str = ""
    name: str
    reason: str
    category: str


class AssemblyPoint(BaseModel):
    """P(assembled by turn N), with its interval."""

    model_config = ConfigDict(frozen=True)

    turn: int = Field(ge=1)
    probability: float = Field(ge=0.0, le=1.0)
    ci_low: float | None = None
    ci_high: float | None = None


class SimulationResult(BaseModel):
    """A validated ``cedh-simulation-result.v1`` document.

    ``metric``, ``measures`` and ``does_not_measure`` are required. The
    simulator prints its honesty header before any figure for a reason, and a
    consumer that could drop it while keeping the number would undo that.
    """

    model_config = ConfigDict(frozen=True)

    schema_id: Literal["cedh-simulation-result.v1"] = RESULT_SCHEMA_ID
    status: Literal["simulated"] = "simulated"
    candidate_id: str
    #: The simulator's hash of the list it actually ran.
    deck_sha256: str
    simulator_version: str
    games: int = Field(gt=0)
    objective_turn: int = Field(ge=1)
    metric: str
    measures: str
    does_not_measure: str
    assembly: tuple[AssemblyPoint, ...] = ()
    censored_fraction: float = Field(ge=0.0, le=1.0)
    modeled_cards: int = 0
    inert_cards: tuple[InertCard, ...] = ()
    #: Inert classifications the simulator's author knows are wrong and has
    #: left in place. Surfaced, never dropped.
    known_misclassifications: tuple[str, ...] = ()
    unauthored_cards: tuple[str, ...] = ()

    @property
    def headline(self) -> AssemblyPoint | None:
        for point in self.assembly:
            if point.turn == self.objective_turn:
                return point
        return self.assembly[0] if self.assembly else None

    @property
    def unseen_card_count(self) -> int:
        return len(self.inert_cards) + len(self.unauthored_cards)


class NotSimulated(BaseModel):
    """No simulation happened, and this is why."""

    model_config = ConfigDict(frozen=True)

    status: Literal["not_simulated"] = "not_simulated"
    reason: NotSimulatedReason
    detail: str = ""

    @property
    def headline(self) -> None:
        return None


#: What :meth:`SimulatorClient.simulate` returns.
SimulationOutcome = SimulationResult | NotSimulated


@runtime_checkable
class SimulatorClient(Protocol):
    """Orchestration boundary to ``commander_simulator``."""

    def supported_commander_keys(self) -> frozenset[str]:
        """Identity keys the simulator has a model for.

        Kinnan is the only one today. Callers show anything outside this set as
        unsupported rather than simulating it badly.
        """
        ...

    def simulate(self, candidate: DeckCandidate) -> SimulationOutcome:
        """Simulate a candidate, or say why it was not simulated."""
        ...


def parse_result(payload: dict, candidate: DeckCandidate) -> SimulationResult:
    """Validate a simulator payload against the versioned contract.

    Args:
        payload: The decoded JSON document.
        candidate: The candidate that was submitted.

    Returns:
        The validated result.

    Raises:
        SimulatorContractError: The payload declares the wrong schema, fails
            validation, or describes a different deck. A malformed result is
            never silently repaired — the caller turns this into a visible
            ``not_simulated`` state.
    """
    declared = payload.get("schema") or payload.get("schema_id")
    if declared != RESULT_SCHEMA_ID:
        raise SimulatorContractError(
            f"simulator returned schema {declared!r}, expected " f"{RESULT_SCHEMA_ID!r}"
        )
    body = {k: v for k, v in payload.items() if k != "schema"}
    body["schema_id"] = RESULT_SCHEMA_ID
    try:
        result = SimulationResult.model_validate(body)
    except Exception as exc:
        raise SimulatorContractError(
            f"simulator result failed validation: {exc}"
        ) from exc
    if result.deck_sha256 != candidate.deck_sha256:
        raise SimulatorContractError(
            "simulator result describes a different deck: it ran "
            f"{result.deck_sha256[:12]}, we submitted "
            f"{candidate.deck_sha256[:12]}"
        )
    return result


class FixtureSimulatorClient:
    """Reads checked-in results instead of running the simulator.

    The default until ``commander_simulator`` publishes the versioned JSON
    contract. Fixtures are keyed by commander identity key; the deck hash in a
    fixture is rewritten to the submitted candidate's hash on load, because a
    fixture is a shape to develop against and not a measurement of the deck in
    front of it. That substitution is stated in the returned
    ``simulator_version``, so a fixture result is never mistaken for a real one.
    """

    def __init__(
        self,
        fixture_dir: Path | str = "fixtures/cedh/simulation",
        *,
        supported: frozenset[str] | None = None,
    ) -> None:
        self._dir = Path(fixture_dir)
        self._supported = supported

    def _index(self) -> dict[str, Path]:
        """Map commander identity key -> fixture path.

        Keys come from a ``commander_keys`` array inside each fixture rather
        than from the filename, so one fixture can serve both the synthetic id
        the fixture corpus assigns and the real oracle_id from ``mtg_v1``
        without being duplicated or renamed when the live corpus arrives.
        """
        if not self._dir.exists():
            return {}
        index: dict[str, Path] = {}
        for path in sorted(self._dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.warning("cEDH simulator fixture is not JSON: %s", path)
                continue
            keys = payload.get("commander_keys") or [path.stem]
            for key in keys:
                index[str(key)] = path
        return index

    def supported_commander_keys(self) -> frozenset[str]:
        if self._supported is not None:
            return self._supported
        return frozenset(self._index())

    def simulate(self, candidate: DeckCandidate) -> SimulationOutcome:
        key = candidate.commander.key
        path = self._index().get(key)
        if path is None:
            return NotSimulated(
                reason="commander_unsupported",
                detail=(
                    f"no simulator model exists for "
                    f"{candidate.commander.display_name}"
                ),
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("commander_keys", None)
        payload["candidate_id"] = candidate.candidate_id
        payload["deck_sha256"] = candidate.deck_sha256
        version = payload.get("simulator_version", "unknown")
        if not version.startswith("fixture:"):
            payload["simulator_version"] = f"fixture:{version}"
        try:
            return parse_result(payload, candidate)
        except SimulatorContractError as exc:
            return NotSimulated(reason="contract_violation", detail=str(exc))


class SubprocessSimulatorClient:
    """Runs the simulator binary and validates its JSON on stdout.

    Written against the contract requested in ``docs/integration-handoff.md``:
    the binary accepts ``--candidate <path>`` holding a
    ``cedh-deck-candidate.v1`` document and writes a
    ``cedh-simulation-result.v1`` document to stdout. That interface does not
    exist yet — today's binary reads a hand-authored TOML deck and prints a
    human report — so this class is unusable until it does, and says so rather
    than parsing a report.
    """

    def __init__(
        self,
        binary_path: Path | str,
        *,
        games: int = 20000,
        objective_turn: int = 3,
        timeout_seconds: float = 120.0,
        supported: frozenset[str] = frozenset(),
    ) -> None:
        self._binary = Path(binary_path)
        self._games = games
        self._turn = objective_turn
        self._timeout = timeout_seconds
        self._supported = supported

    def supported_commander_keys(self) -> frozenset[str]:
        return self._supported

    def simulate(self, candidate: DeckCandidate) -> SimulationOutcome:
        if not self._binary.exists():
            return NotSimulated(
                reason="simulator_unavailable",
                detail=f"simulator binary not found at {self._binary}",
            )
        if self._supported and candidate.commander.key not in self._supported:
            return NotSimulated(
                reason="commander_unsupported",
                detail=(
                    f"no simulator model exists for "
                    f"{candidate.commander.display_name}"
                ),
            )
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate.json"
            path.write_text(candidate.to_json(), encoding="utf-8")
            cmd = [
                str(self._binary),
                "--candidate",
                str(path),
                "--games",
                str(self._games),
                "--turn",
                str(self._turn),
                "--format",
                "json",
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return NotSimulated(
                    reason="simulator_failed",
                    detail=f"simulator timed out after {self._timeout}s",
                )
            except OSError as exc:
                return NotSimulated(reason="simulator_unavailable", detail=str(exc))

        if proc.returncode != 0:
            return NotSimulated(
                reason="simulator_failed",
                detail=(
                    f"simulator exited {proc.returncode}: "
                    f"{proc.stderr.strip()[:500]}"
                ),
            )
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return NotSimulated(
                reason="contract_violation",
                detail=(
                    f"simulator stdout was not JSON ({exc}). The binary must "
                    "emit cedh-simulation-result.v1; a human report is not a "
                    "contract and is not parsed."
                ),
            )
        try:
            return parse_result(payload, candidate)
        except SimulatorContractError as exc:
            return NotSimulated(reason="contract_violation", detail=str(exc))


class DisabledSimulatorClient:
    """Simulation switched off by configuration."""

    def supported_commander_keys(self) -> frozenset[str]:
        return frozenset()

    def simulate(self, candidate: DeckCandidate) -> SimulationOutcome:
        return NotSimulated(
            reason="simulator_disabled",
            detail="simulation is disabled in config/cedh.yaml",
        )


def build_simulator(
    settings: SimulatorSettings | None = None,
) -> SimulatorClient:
    """Construct the configured simulator client."""
    settings = settings or SimulatorSettings()
    if settings.mode == "off":
        return DisabledSimulatorClient()
    if settings.mode == "subprocess":
        return SubprocessSimulatorClient(
            settings.binary_path,
            games=settings.games,
            objective_turn=settings.objective_turn,
            timeout_seconds=settings.timeout_seconds,
        )
    return FixtureSimulatorClient(settings.fixture_dir)
