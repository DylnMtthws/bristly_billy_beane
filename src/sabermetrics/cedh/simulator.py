"""The simulator boundary: JSON in, validated JSON out.

This repository orchestrates simulation. It does not own simulation mechanics
or strategy definitions, does not import the simulator's source, and does not
reimplement any part of its model.

Two states, and only two:

* :class:`SimulationResult` — a validated ``cedh-simulation-result.v3``
  document from the simulator.
* :class:`NotSimulated` — a named reason why there is no result.

There is deliberately no third state where a missing simulator becomes a
neutral score. A neutral number is indistinguishable from a measured one once
it is in a table, and it would be the only number in the product that means
nothing. An unsupported commander is likewise shown as unsupported, not as a
zero.

**Deck identity is checked on one field, and it is the simulator's own.**
Every result carries ``candidate.deck_sha256``: the simulator's independent
recomputation of the deck hash from the list it actually ran, using the same
ADR-025 algorithm this repository uses. We compare that, and only that, to
decide whether a result describes the deck in front of the user.

The field it replaces, ``candidate_hash``, hashed the deck list *together with*
the strategy pack. This repository compared it to a deck-only hash, so it never
matched, and the mismatch was reported as "different deck" when the deck was
identical. The pack-inclusive fingerprint still exists and is still useful —
it is ``simulation_input_sha256``, it covers every input that can change the
numbers, and it is the right cache key. It is simply not a deck identity, and
the two now have names that say which is which.

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
import time
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal, Protocol, cast, runtime_checkable

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, Field

from sabermetrics.cedh.candidate import DeckCandidate
from sabermetrics.cedh.errors import SimulatorContractError
from sabermetrics.cedh.settings import SimulatorSettings
from sabermetrics.cedh.wire import simulation_request

logger = logging.getLogger(__name__)

#: The one result contract. There is deliberately no second accepted shape:
#: this repository previously also described a flat ``cedh-simulation-result.v1``
#: that the simulator never emitted, and three candidate meanings for one
#: document is how the field-name collision survived as long as it did.
RESULT_SCHEMA_ID: Final = "cedh-simulation-result.v3"
HTTP_RESULT_SCHEMA_ID: Final = RESULT_SCHEMA_ID

#: Why a candidate was not simulated. A closed set: an unmodelled reason is a
#: reason nobody chose to state.
NotSimulatedReason = Literal[
    "simulator_disabled",
    "simulator_unavailable",
    "commander_unsupported",
    #: The simulator ran a different list than we submitted. This is the ONLY
    #: reason that means the deck is not what we think it is. An unsupported
    #: pack, a stale card snapshot or a schema disagreement are execution-context
    #: and contract faults and must never arrive here — a user told their deck
    #: changed when it did not cannot act on that, and will not trust the next
    #: such message either.
    "deck_mismatch",
    "simulator_failed",
    "contract_violation",
    "invalid_request",
    "unsupported",
    "busy",
    "card_data_unavailable",
    "timeout",
    "unsupported_schema",
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

    schema_id: Literal["cedh-simulation-result.v3"] = RESULT_SCHEMA_ID
    status: Literal["simulated"] = "simulated"
    candidate_id: str
    #: The simulator's OWN recomputation of the deck hash, in bare hex. Deck
    #: identity is decided on this and nothing else.
    deck_sha256: str
    #: The simulator's fingerprint of every behaviour-affecting input: this
    #: deck, the resolved strategy pack and its content hash, the simulator and
    #: card-data versions, the scenario, seed, games, turn, sweep and
    #: ablations. Two results sharing it are the same measurement, which makes
    #: it the correct cache key — and makes deck_sha256 the wrong one.
    simulation_input_sha256: str = ""
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
    inert_card_count: int = 0
    unauthored_card_count: int = 0
    cards_sha256: str = ""
    result_schema: str = ""
    simulator_threads: str = ""
    raw_document: dict[str, Any] | None = None

    @property
    def headline(self) -> AssemblyPoint | None:
        for point in self.assembly:
            if point.turn == self.objective_turn:
                return point
        return self.assembly[0] if self.assembly else None

    @property
    def unseen_card_count(self) -> int:
        return max(self.inert_card_count, len(self.inert_cards)) + max(
            self.unauthored_card_count, len(self.unauthored_cards)
        )


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


@lru_cache(maxsize=1)
def _http_result_validator() -> Draft202012Validator:
    """Load the vendored result schema used for every successful response."""
    path = (
        Path(__file__).resolve().parents[3]
        / "fixtures"
        / "cedh"
        / "contracts"
        / "cedh-simulation-result.v3.schema.json"
    )
    schema = json.loads(path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _parse_http_result(
    payload: dict[str, Any], candidate: DeckCandidate, headers: httpx.Headers
) -> SimulationResult:
    """Validate and adapt a v2 service document to the UI domain model."""
    try:
        _http_result_validator().validate(payload)
    except ValidationError as exc:
        raise SimulatorContractError(
            f"simulator result failed vendored schema validation: {exc.message}"
        ) from exc

    # The one deck-identity check. The simulator recomputed this from the list
    # it ran; we recompute ours from the list we built. Nothing about the
    # strategy pack, the corpus or the run parameters is in either value, so a
    # mismatch here means the LIST differs and cannot mean anything else.
    ran = str(payload["candidate"]["deck_sha256"])
    submitted = candidate.deck_sha256_wire
    if ran != submitted:
        raise SimulatorContractError(
            "simulator result describes a different deck: it ran "
            f"{ran[:19]}, we submitted {submitted[:19]}"
        )

    coverage = payload["coverage"]
    simulation = payload["simulation"]
    metric = payload["metric"]
    assembly = tuple(
        AssemblyPoint(
            turn=point["turn"],
            probability=point["probability"],
            ci_low=point["wilson_95"]["low"],
            ci_high=point["wilson_95"]["high"],
        )
        for point in payload["assembly_cdf"]
    )
    return SimulationResult(
        schema_id=RESULT_SCHEMA_ID,
        candidate_id=str(payload["candidate"]["candidate_id"]),
        deck_sha256=ran.removeprefix("sha256:"),
        simulation_input_sha256=str(payload["simulation_input_sha256"]),
        simulator_version=headers.get(
            "X-Sim-Version", str(payload["simulator"]["version"])
        ),
        games=simulation["games"],
        objective_turn=simulation["objective_turn"],
        metric=metric["id"],
        measures=metric["measures"],
        does_not_measure=metric["does_not_measure"],
        assembly=assembly,
        censored_fraction=payload["censored"]["rate"],
        modeled_cards=coverage["modeled_cards"],
        inert_card_count=coverage["inert_cards"],
        unauthored_card_count=coverage["unauthored_cards"],
        known_misclassifications=tuple(payload["warnings"]),
        cards_sha256=headers.get("X-Cards-Sha256", ""),
        result_schema=headers.get("X-Sim-Result-Schema", ""),
        simulator_threads=headers.get("X-Sim-Threads", ""),
        raw_document=payload,
    )


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
        # Fixture-only keys. The vendored schema is additionalProperties:false,
        # so these must go before validation — which is the point: a fixture is
        # held to the same contract as a live response.
        payload.pop("commander_keys", None)
        payload.pop("_README", None)
        # A fixture is a shape to develop against, not a measurement of the
        # deck in front of it, so the deck hash is rewritten to the submitted
        # candidate's. The substitution is stated in simulator_version below,
        # so a fixture result is never mistaken for a live one.
        payload["candidate"]["deck_sha256"] = candidate.deck_sha256_wire
        payload["candidate"]["candidate_id"] = candidate.candidate_id
        try:
            headers = httpx.Headers(
                {
                    "X-Sim-Version": f"fixture:{payload['simulator']['version']}",
                    "X-Sim-Result-Schema": RESULT_SCHEMA_ID,
                    "X-Cards-Sha256": str(payload["card_data"]["cards_sha256"]),
                }
            )
            return _parse_http_result(payload, candidate, headers)
        except (SimulatorContractError, KeyError) as exc:
            return NotSimulated(reason="contract_violation", detail=str(exc))


class HttpSimulatorClient:
    """Call the frozen private-network simulation service contract."""

    def __init__(
        self,
        base_url: str,
        *,
        games: int = 20000,
        objective_turn: int = 3,
        timeout_seconds: float = 330.0,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/simulate"
        self._games = games
        self._turn = objective_turn
        self._timeout = timeout_seconds
        self._transport = transport
        self._sleep = sleeper

    def supported_commander_keys(self) -> frozenset[str]:
        """The HTTP contract discovers support by attempting the request."""
        return frozenset()

    @staticmethod
    def _error(response: httpx.Response) -> NotSimulated:
        try:
            body = response.json()
        except ValueError:
            body = {}
        fallback = {
            400: "invalid_request",
            422: "unsupported",
            429: "busy",
            503: "card_data_unavailable",
            504: "timeout",
            500: "simulator_failed",
        }.get(response.status_code, "simulator_failed")
        declared = body.get("error") if isinstance(body, dict) else None
        # The service's machine codes, mapped to this repository's vocabulary.
        # Only deck_hash_mismatch becomes deck_mismatch: an unsupported pack or
        # a stale snapshot arrives as "unsupported", because telling a user
        # their deck changed when only the execution context did is both wrong
        # and something they cannot act on.
        translated: dict[str, NotSimulatedReason] = {
            "deck_hash_mismatch": "deck_mismatch",
            "contract_violation": "contract_violation",
            "invalid_request": "invalid_request",
            "unsupported": "unsupported",
            "busy": "busy",
            "card_data_unavailable": "card_data_unavailable",
            "timeout": "timeout",
            "simulator_failed": "simulator_failed",
        }
        reason = translated.get(
            declared if isinstance(declared, str) else "",
            cast(NotSimulatedReason, fallback),
        )
        detail = body.get("detail", "") if isinstance(body, dict) else ""
        stderr = body.get("stderr", "") if isinstance(body, dict) else ""
        if stderr:
            detail = f"{detail} (stderr: {stderr})".strip()
        if not detail:
            detail = f"simulation service returned HTTP {response.status_code}"
        return NotSimulated(reason=reason, detail=detail)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        try:
            return min(max(float(response.headers.get("Retry-After", "1")), 0), 10)
        except ValueError:
            return 1.0

    def simulate(self, candidate: DeckCandidate) -> SimulationOutcome:
        request = simulation_request(candidate, games=self._games, turn=self._turn)
        timeout = httpx.Timeout(self._timeout, connect=15.0)
        with httpx.Client(timeout=timeout, transport=self._transport) as client:
            response: httpx.Response | None = None
            for attempt in range(2):
                try:
                    response = client.post(self._url, json=request)
                except (httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                    # The server may already be running this simulation. Retrying
                    # would submit it twice and can hide the timeout behind busy.
                    return NotSimulated(
                        reason="timeout",
                        detail=str(exc) or "simulation service request timed out",
                    )
                except httpx.RequestError as exc:
                    if attempt == 0:
                        self._sleep(1.0)
                        continue
                    return NotSimulated(reason="simulator_unavailable", detail=str(exc))
                if response.status_code == 429 and attempt == 0:
                    self._sleep(self._retry_after(response))
                    continue
                break

        assert response is not None
        if response.status_code != 200:
            return self._error(response)
        result_schema = response.headers.get("X-Sim-Result-Schema", "")
        if result_schema != HTTP_RESULT_SCHEMA_ID:
            return NotSimulated(
                reason="unsupported_schema",
                detail=(
                    f"simulation service returned schema {result_schema!r}; "
                    f"supported: {HTTP_RESULT_SCHEMA_ID}"
                ),
            )
        try:
            payload = response.json()
        except ValueError as exc:
            return NotSimulated(reason="contract_violation", detail=str(exc))
        if not isinstance(payload, dict):
            return NotSimulated(
                reason="contract_violation", detail="simulator result is not an object"
            )
        try:
            return _parse_http_result(payload, candidate, response.headers)
        except SimulatorContractError as exc:
            reason: NotSimulatedReason = (
                "deck_mismatch"
                if "different deck" in str(exc)
                else "contract_violation"
            )
            return NotSimulated(reason=reason, detail=str(exc))


class SubprocessSimulatorClient:
    """Runs the simulator binary and validates its JSON on stdout.

    This legacy/local adapter uses the real binary flags. Production prefers
    :class:`HttpSimulatorClient`, which keeps card data with the simulator.
    """

    def __init__(
        self,
        binary_path: Path | str,
        *,
        games: int = 20000,
        objective_turn: int = 3,
        timeout_seconds: float = 330.0,
        cards_path: Path | str = "fixtures/cedh/cards.json",
        supported: frozenset[str] = frozenset(),
    ) -> None:
        self._binary = Path(binary_path)
        self._games = games
        self._turn = objective_turn
        self._timeout = timeout_seconds
        self._cards = Path(cards_path)
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
            path = Path(tmp) / "request.json"
            path.write_text(
                json.dumps(
                    simulation_request(candidate, games=self._games, turn=self._turn)[
                        "candidate"
                    ]
                ),
                encoding="utf-8",
            )
            cmd = [
                str(self._binary),
                "--request",
                str(path),
                "--cards",
                str(self._cards),
                "--output-json",
                "-",
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
                    "emit a versioned JSON result; a human report is not a "
                    "contract and is not parsed."
                ),
            )
        try:
            headers = httpx.Headers(
                {
                    "X-Sim-Version": str(payload["simulator"]["version"]),
                    "X-Sim-Result-Schema": RESULT_SCHEMA_ID,
                    "X-Cards-Sha256": str(payload["card_data"]["cards_sha256"]),
                }
            )
            return _parse_http_result(payload, candidate, headers)
        except (SimulatorContractError, KeyError) as exc:
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
            cards_path=settings.cards_path,
        )
    if settings.mode == "http":
        if not settings.url:
            return DisabledSimulatorClient()
        return HttpSimulatorClient(
            settings.url,
            games=settings.games,
            objective_turn=settings.objective_turn,
            timeout_seconds=settings.timeout_seconds,
        )
    return FixtureSimulatorClient(settings.fixture_dir)
