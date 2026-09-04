"""The simulator boundary. Two states only: a validated result, or a reason."""

from __future__ import annotations

import json
import os
import stat

import httpx
import pytest

from sabermetrics.cedh.builder import build_candidate
from sabermetrics.cedh.errors import SimulatorContractError
from sabermetrics.cedh.simulator import (
    HTTP_RESULT_SCHEMA_ID,
    DisabledSimulatorClient,
    FixtureSimulatorClient,
    HttpSimulatorClient,
    NotSimulated,
    SimulationResult,
    SimulatorClient,
    SubprocessSimulatorClient,
    _parse_http_result,
    build_simulator,
)
from sabermetrics.cedh.wire import (
    CANDIDATE_SCHEMA_ID,
    REQUEST_SCHEMA_ID,
    candidate_document,
    simulation_request,
)

LIVE_SIMULATOR_URL = os.environ.get("CEDH_SIMULATOR_URL", "").strip()


@pytest.fixture
def candidate(kinnan_pack, cedh_cards):
    return build_candidate(kinnan_pack, cedh_cards)


def _result_payload(candidate):
    percentiles = []
    for percentile in (0.1, 0.25, 0.5, 0.75, 0.9):
        percentiles.append(
            {
                "percentile": percentile,
                "status": "observed",
                "turn": 3,
                "interval_95": {"low_turn": 2, "high_turn": 4},
            }
        )
    return {
        "schema_version": HTTP_RESULT_SCHEMA_ID,
        "run_id": "run-cccccccccccccccccccccccc",
        "timestamps": {
            "started_at": "2026-09-04T12:00:00Z",
            "completed_at": "2026-09-04T12:00:01Z",
        },
        "simulator": {"version": "2.3.4", "build_flavour": "release"},
        "metric": {
            "id": "goldfish_turns_to_assembly",
            "measures": "assembly while playing alone",
            "does_not_measure": "matchup win rate or deck quality",
        },
        "candidate": {
            "candidate_id": candidate.candidate_id,
            "deck_sha256": candidate.deck_sha256_wire,
        },
        "simulation_input_sha256": f"sha256:{'c' * 64}",
        "strategy_pack": {
            "id": "kinnan-midrange-goldfish",
            "version": "1.0.0",
            "content_sha256": f"sha256:{'d' * 64}",
            "derived": False,
            "play_policy": "goldfish",
            "assembly_objectives": [],
            "patterns": [],
            "inherited_patterns": [],
            "rank_overrides": [],
            "declared_table_assumptions": [],
            "known_blind_spots": [],
        },
        "card_data": {
            "manifest_hash": f"sha256:{'a' * 64}",
            "cards_sha256": "b" * 64,
            "source_view": "mtg_v1.card_any_medium",
            "max_content_updated_at": "2026-09-04T00:00:00Z",
            "corpus_row_count": 30000,
        },
        "simulation": {
            "games": 20000,
            "seed": int(candidate.deck_sha256[:16], 16),
            "objective_turn": 3,
            "requested_scenario": {"id": "goldfish_assembly", "version": "1.0.0"},
        },
        "coverage": {
            "total_cards": 100,
            "library_cards": 99,
            "commander_cards": 1,
            "modeled_cards": 91,
            "inert_cards": 8,
            "unauthored_cards": 2,
            "inert_by_reason": {"interaction": 8},
        },
        "assembly_cdf": [
            {
                "turn": 3,
                "assembled_games": 282,
                "probability": 0.0141,
                "wilson_95": {"low": 0.0126, "high": 0.0158},
            }
        ],
        "censored": {
            "games": 4000,
            "rate": 0.2,
            "wilson_95": {"low": 0.19, "high": 0.21},
        },
        "percentiles": percentiles,
        "ablation_results": [],
        "warnings": ["two cards are unauthored"],
        "unsupported_assumptions": [
            {"code": "no_opponents", "detail": "goldfish only"}
        ],
        "determinism": {
            "seed_scheme": "splitmix64(base_seed ^ splitmix64(game_index)) + xoshiro256++",
            "result_digest": "0123456789abcdef",
        },
    }


def _result_headers():
    return {
        "X-Sim-Version": "2.3.4",
        "X-Sim-Result-Schema": HTTP_RESULT_SCHEMA_ID,
        "X-Cards-Sha256": "b" * 64,
        "X-Sim-Threads": "4",
    }


class TestContractValidation:
    def test_a_valid_payload_parses(self, candidate):
        result = _parse_http_result(
            _result_payload(candidate), candidate, httpx.Headers(_result_headers())
        )
        assert isinstance(result, SimulationResult)
        assert result.headline.turn == 3

    def test_deck_identity_is_decided_on_the_simulators_own_recomputation(
        self, candidate
    ):
        """Not on an echo of what we sent, and not on a pack-inclusive hash."""
        result = _parse_http_result(
            _result_payload(candidate), candidate, httpx.Headers(_result_headers())
        )
        assert result.deck_sha256 == candidate.deck_sha256

    def test_the_input_fingerprint_is_carried_and_is_not_the_deck_hash(
        self, candidate
    ):
        result = _parse_http_result(
            _result_payload(candidate), candidate, httpx.Headers(_result_headers())
        )
        assert result.simulation_input_sha256 == f"sha256:{'c' * 64}"
        assert result.simulation_input_sha256 != candidate.deck_sha256_wire

    def test_a_result_for_a_different_deck_is_rejected(self, candidate):
        """A stored simulation must be known to describe this list."""
        payload = _result_payload(candidate)
        payload["candidate"]["deck_sha256"] = f"sha256:{'0' * 64}"
        with pytest.raises(SimulatorContractError, match="different deck"):
            _parse_http_result(payload, candidate, httpx.Headers(_result_headers()))

    def test_a_different_strategy_pack_is_not_a_different_deck(self, candidate):
        """ADR-025 on the consumer side.

        The regression this whole contract exists for: an identical list run
        under another pack used to fail deck identity, and the user was told
        their deck had changed when only the execution context had.
        """
        payload = _result_payload(candidate)
        payload["strategy_pack"]["id"] = "derived-generic"
        payload["strategy_pack"]["derived"] = True
        payload["strategy_pack"]["content_sha256"] = f"sha256:{'e' * 64}"
        result = _parse_http_result(
            payload, candidate, httpx.Headers(_result_headers())
        )
        assert result.deck_sha256 == candidate.deck_sha256

    def test_missing_honesty_fields_are_rejected(self, candidate):
        """The framing is required, so a number cannot be shown without it."""
        payload = _result_payload(candidate)
        del payload["metric"]["does_not_measure"]
        with pytest.raises(SimulatorContractError, match="schema validation"):
            _parse_http_result(payload, candidate, httpx.Headers(_result_headers()))

    def test_an_out_of_range_probability_is_rejected(self, candidate):
        payload = _result_payload(candidate)
        payload["assembly_cdf"][0]["probability"] = 1.7
        with pytest.raises(SimulatorContractError, match="schema validation"):
            _parse_http_result(payload, candidate, httpx.Headers(_result_headers()))

    def test_the_removed_v1_candidate_hash_does_not_satisfy_the_contract(
        self, candidate
    ):
        """A v2 result cannot be quietly accepted as if it were a v3 one."""
        payload = _result_payload(candidate)
        del payload["candidate"]["deck_sha256"]
        payload["candidate"]["candidate_hash"] = candidate.deck_sha256_wire
        with pytest.raises(SimulatorContractError, match="schema validation"):
            _parse_http_result(payload, candidate, httpx.Headers(_result_headers()))


class TestFixtureClient:
    def test_satisfies_the_protocol(self):
        assert isinstance(FixtureSimulatorClient(), SimulatorClient)

    def test_kinnan_is_the_supported_commander(self, kinnan_pack):
        client = FixtureSimulatorClient()
        assert kinnan_pack.commander.key in client.supported_commander_keys()

    def test_a_supported_commander_is_simulated(self, candidate):
        result = FixtureSimulatorClient().simulate(candidate)
        assert result.status == "simulated"
        assert result.headline.probability == pytest.approx(0.0141)

    def test_the_fixture_result_is_labelled_as_a_fixture(self, candidate):
        """A fixture number must never be mistaken for a live run."""
        result = FixtureSimulatorClient().simulate(candidate)
        assert result.simulator_version.startswith("fixture:")

    def test_the_fixture_carries_what_the_model_cannot_see(self, candidate):
        """The result contract reports coverage as counts plus warnings.

        There is no per-card inert list on the wire: the simulator publishes
        ``coverage.inert_cards``/``unauthored_cards`` as counts and names the
        specific cards in ``warnings``. Both are surfaced, so the user still
        sees that the figure was computed with cards invisible to the model.
        """
        result = FixtureSimulatorClient().simulate(candidate)
        assert result.inert_card_count > 0
        assert result.unauthored_card_count > 0
        assert result.known_misclassifications
        assert result.unseen_card_count > 0

    def test_an_unsupported_commander_says_so(self, candidate):
        other = candidate.model_copy(
            update={
                "commander": candidate.commander.model_copy(
                    update={"oracle_ids": ("unknown-commander",)}
                )
            }
        )
        result = FixtureSimulatorClient().simulate(other)
        assert isinstance(result, NotSimulated)
        assert result.reason == "commander_unsupported"

    def test_a_missing_fixture_directory_is_unsupported_not_a_crash(
        self, candidate, tmp_path
    ):
        result = FixtureSimulatorClient(tmp_path / "nope").simulate(candidate)
        assert isinstance(result, NotSimulated)


class TestHttpClient:
    def _client(self, handler, sleeps=None):
        sleeps = sleeps if sleeps is not None else []
        return HttpSimulatorClient(
            "http://sim.internal:8080/",
            transport=httpx.MockTransport(handler),
            sleeper=sleeps.append,
        )

    def test_200_validates_and_captures_provenance(self, candidate):
        seen = {}

        def handler(request):
            seen["document"] = json.loads(request.content)
            seen["timeout"] = request.extensions["timeout"]
            return httpx.Response(
                200, json=_result_payload(candidate), headers=_result_headers()
            )

        result = self._client(handler).simulate(candidate)
        assert isinstance(result, SimulationResult)
        assert seen["document"] == simulation_request(candidate, games=20000, turn=3)
        assert seen["document"]["schema_version"] == REQUEST_SCHEMA_ID
        assert seen["document"]["candidate"] == candidate_document(candidate)
        assert seen["document"]["games"] == 20000
        assert seen["document"]["turn"] == 3
        assert seen["document"]["seed"] == int(candidate.deck_sha256[:16], 16)
        assert seen["timeout"]["connect"] == 15.0
        assert seen["timeout"]["read"] == 330.0
        assert result.simulator_version == "2.3.4"
        assert result.result_schema == HTTP_RESULT_SCHEMA_ID
        assert result.cards_sha256 == "b" * 64
        assert result.simulator_threads == "4"
        assert result.unseen_card_count == 10

    @pytest.mark.parametrize(
        ("status", "code"),
        [
            (400, "invalid_request"),
            (422, "unsupported"),
            (429, "busy"),
            (503, "card_data_unavailable"),
            (504, "timeout"),
            (500, "simulator_failed"),
        ],
    )
    def test_every_service_error_is_visible(self, candidate, status, code):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(
                status,
                json={"error": code, "detail": "detail", "stderr": "tail"},
                headers={"Retry-After": "0"},
            )

        result = self._client(handler).simulate(candidate)
        assert isinstance(result, NotSimulated)
        assert result.reason == code
        assert "detail" in result.detail
        assert "tail" in result.detail
        assert len(calls) == (2 if status == 429 else 1)

    def test_429_retries_once_honors_retry_after_and_caps_it(self, candidate):
        sleeps = []
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(
                    429,
                    json={"error": "busy", "detail": "one at a time"},
                    headers={"Retry-After": "99"},
                )
            return httpx.Response(
                200, json=_result_payload(candidate), headers=_result_headers()
            )

        result = self._client(handler, sleeps).simulate(candidate)
        assert result.status == "simulated"
        assert calls == 2
        assert sleeps == [10]

    def test_cold_start_connection_error_retries_once(self, candidate):
        sleeps = []
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("machine starting", request=request)
            return httpx.Response(
                200, json=_result_payload(candidate), headers=_result_headers()
            )

        result = self._client(handler, sleeps).simulate(candidate)
        assert result.status == "simulated"
        assert sleeps == [1.0]
        assert calls == 2

    @pytest.mark.parametrize("timeout_type", [httpx.ReadTimeout, httpx.WriteTimeout])
    def test_timeout_is_not_resubmitted(self, candidate, timeout_type):
        calls = []
        sleeps = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise timeout_type("simulation timed out", request=request)
            return httpx.Response(429, json={"error": "busy"})

        result = self._client(handler, sleeps).simulate(candidate)
        assert isinstance(result, NotSimulated)
        assert result.reason == "timeout"
        assert result.detail == "simulation timed out"
        assert len(calls) == 1
        assert sleeps == []

    def test_two_connection_failures_are_unavailable(self, candidate):
        def handler(request):
            raise httpx.ConnectError("still starting", request=request)

        result = self._client(handler, []).simulate(candidate)
        assert result.reason == "simulator_unavailable"

    def test_unsupported_result_schema_is_not_used(self, candidate):
        headers = _result_headers()
        headers["X-Sim-Result-Schema"] = "cedh-simulation-result.v99"
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=_result_payload(candidate), headers=headers
            )
        )
        result = HttpSimulatorClient("http://sim", transport=transport).simulate(
            candidate
        )
        assert result.reason == "unsupported_schema"

    def test_schema_validation_failure_is_not_used(self, candidate):
        payload = _result_payload(candidate)
        del payload["metric"]
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, headers=_result_headers())
        )
        result = HttpSimulatorClient("http://sim", transport=transport).simulate(
            candidate
        )
        assert result.reason == "contract_violation"

    def test_deck_hash_mismatch_is_visible(self, candidate):
        payload = _result_payload(candidate)
        payload["candidate"]["deck_sha256"] = f"sha256:{'0' * 64}"
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, headers=_result_headers())
        )
        result = HttpSimulatorClient("http://sim", transport=transport).simulate(
            candidate
        )
        assert result.reason == "deck_mismatch"

    def test_non_json_200_is_contract_violation(self, candidate):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="not-json", headers=_result_headers())
        )
        result = HttpSimulatorClient("http://sim", transport=transport).simulate(
            candidate
        )
        assert result.reason == "contract_violation"


class TestNeverInventsAScore:
    @pytest.mark.parametrize(
        "client_factory",
        [
            lambda tmp: DisabledSimulatorClient(),
            lambda tmp: FixtureSimulatorClient(tmp / "absent"),
            lambda tmp: SubprocessSimulatorClient(tmp / "no-binary"),
        ],
    )
    def test_absence_never_produces_a_neutral_number(
        self, candidate, tmp_path, client_factory
    ):
        """A neutral score is indistinguishable from a measured one in a table."""
        result = client_factory(tmp_path).simulate(candidate)
        assert isinstance(result, NotSimulated)
        assert result.headline is None
        assert not hasattr(result, "probability")
        assert result.detail

    def test_every_not_simulated_reason_is_from_the_closed_set(self):
        from typing import get_args

        from sabermetrics.cedh.simulator import NotSimulatedReason

        assert set(get_args(NotSimulatedReason)) == {
            "simulator_disabled",
            "simulator_unavailable",
            "commander_unsupported",
            "deck_mismatch",
            "simulator_failed",
            "contract_violation",
            "invalid_request",
            "unsupported",
            "busy",
            "card_data_unavailable",
            "timeout",
            "unsupported_schema",
        }


def _fake_binary(tmp_path, script: str):
    path = tmp_path / "cs"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class TestSubprocessClient:
    def test_a_missing_binary_is_reported(self, candidate, tmp_path):
        result = SubprocessSimulatorClient(tmp_path / "nope").simulate(candidate)
        assert result.reason == "simulator_unavailable"

    def test_valid_json_on_stdout_is_parsed(self, candidate, tmp_path):
        payload = json.dumps(_result_payload(candidate))
        binary = _fake_binary(tmp_path, f"#!/bin/sh\ncat <<'EOF'\n{payload}\nEOF\n")
        result = SubprocessSimulatorClient(binary).simulate(candidate)
        assert result.status == "simulated"

    def test_a_human_report_is_not_parsed(self, candidate, tmp_path):
        """Today's binary prints a report. A report is not a contract."""
        binary = _fake_binary(
            tmp_path,
            "#!/bin/sh\necho 'P(assembled by turn 3)  1.41%'\n",
        )
        result = SubprocessSimulatorClient(binary).simulate(candidate)
        assert result.reason == "contract_violation"
        assert "not JSON" in result.detail

    def test_a_nonzero_exit_is_reported_with_stderr(self, candidate, tmp_path):
        binary = _fake_binary(tmp_path, "#!/bin/sh\necho 'deck rejected' >&2\nexit 3\n")
        result = SubprocessSimulatorClient(binary).simulate(candidate)
        assert result.reason == "simulator_failed"
        assert "deck rejected" in result.detail

    def test_a_result_for_another_deck_becomes_a_visible_failure(
        self, candidate, tmp_path
    ):
        """Never silently repair a malformed simulator result."""
        body = _result_payload(candidate)
        body["candidate"]["deck_sha256"] = f"sha256:{'f' * 64}"
        payload = json.dumps(body)
        binary = _fake_binary(tmp_path, f"#!/bin/sh\ncat <<'EOF'\n{payload}\nEOF\n")
        result = SubprocessSimulatorClient(binary).simulate(candidate)
        assert result.reason == "contract_violation"

    def test_a_timeout_is_reported(self, candidate, tmp_path):
        binary = _fake_binary(tmp_path, "#!/bin/sh\nsleep 5\n")
        result = SubprocessSimulatorClient(binary, timeout_seconds=0.2).simulate(
            candidate
        )
        assert result.reason == "simulator_failed"
        assert "timed out" in result.detail

    def test_the_candidate_reaches_the_binary_as_versioned_json(
        self, candidate, tmp_path
    ):
        out = tmp_path / "seen.json"
        binary = _fake_binary(
            tmp_path,
            "#!/bin/sh\nwhile [ $# -gt 0 ]; do\n"
            f'  if [ "$1" = "--request" ]; then cp "$2" {out}; fi\n'
            "  shift\ndone\nexit 1\n",
        )
        SubprocessSimulatorClient(binary).simulate(candidate)
        document = json.loads(out.read_text())
        assert document == candidate_document(candidate)
        assert document["schema_version"] == CANDIDATE_SCHEMA_ID
        assert all(card["oracle_id"] for card in document["library"])

    def test_uses_the_real_binary_flags(self, candidate, tmp_path):
        out = tmp_path / "args.txt"
        binary = _fake_binary(
            tmp_path, f"#!/bin/sh\nprintf '%s\n' \"$@\" > {out}\nexit 1\n"
        )
        cards = tmp_path / "cards.json"
        cards.write_text("{}")
        SubprocessSimulatorClient(binary, cards_path=cards).simulate(candidate)
        args = out.read_text().splitlines()
        assert args[0] == "--request"
        assert args[2:] == ["--cards", str(cards), "--output-json", "-"]

    def test_an_unsupported_commander_short_circuits(self, candidate, tmp_path):
        binary = _fake_binary(tmp_path, "#!/bin/sh\nexit 0\n")
        client = SubprocessSimulatorClient(binary, supported=frozenset({"other"}))
        assert client.simulate(candidate).reason == "commander_unsupported"


class TestConfiguredClient:
    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            ("off", DisabledSimulatorClient),
            ("fixture", FixtureSimulatorClient),
            ("subprocess", SubprocessSimulatorClient),
            ("http", HttpSimulatorClient),
        ],
    )
    def test_mode_selects_the_client(self, mode, expected):
        from sabermetrics.cedh.settings import SimulatorSettings

        settings = SimulatorSettings(mode=mode, url="http://sim")
        assert isinstance(build_simulator(settings), expected)

    def test_the_shipped_default_is_the_fixture(self):
        from sabermetrics.cedh.settings import load_cedh_settings

        assert load_cedh_settings().simulator.mode == "fixture"


def test_this_repository_does_not_import_the_simulator():
    """We orchestrate the simulator; we do not embed it."""
    from pathlib import Path

    root = Path("src/sabermetrics")
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "import mtgsim_export" not in source
        assert "from mtgsim_export" not in source
    assert os.path.exists(root)


@pytest.mark.simulator
@pytest.mark.skipif(not LIVE_SIMULATOR_URL, reason="CEDH_SIMULATOR_URL is not set")
def test_live_http_simulator_contract(candidate):
    result = HttpSimulatorClient(LIVE_SIMULATOR_URL).simulate(candidate)
    assert result.status in {"simulated", "not_simulated"}
