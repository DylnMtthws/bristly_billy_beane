"""The simulator boundary. Two states only: a validated result, or a reason."""

from __future__ import annotations

import json
import os
import stat

import pytest

from sabermetrics.cedh.builder import build_candidate
from sabermetrics.cedh.errors import SimulatorContractError
from sabermetrics.cedh.simulator import (
    RESULT_SCHEMA_ID,
    DisabledSimulatorClient,
    FixtureSimulatorClient,
    NotSimulated,
    SimulationResult,
    SimulatorClient,
    SubprocessSimulatorClient,
    build_simulator,
    parse_result,
)


@pytest.fixture
def candidate(kinnan_pack, cedh_cards):
    return build_candidate(kinnan_pack, cedh_cards)


def _payload(candidate, **overrides):
    body = {
        "schema": RESULT_SCHEMA_ID,
        "candidate_id": candidate.candidate_id,
        "deck_sha256": candidate.deck_sha256,
        "simulator_version": "test/1",
        "games": 1000,
        "objective_turn": 3,
        "metric": "goldfish_turns_to_assembly",
        "measures": "assembly, alone",
        "does_not_measure": "deck quality.",
        "assembly": [{"turn": 3, "probability": 0.01}],
        "censored_fraction": 0.2,
    }
    body.update(overrides)
    return body


class TestContractValidation:
    def test_a_valid_payload_parses(self, candidate):
        result = parse_result(_payload(candidate), candidate)
        assert isinstance(result, SimulationResult)
        assert result.headline.turn == 3

    def test_the_wrong_schema_id_is_rejected(self, candidate):
        with pytest.raises(SimulatorContractError, match="expected"):
            parse_result(_payload(candidate, schema="something-else.v9"), candidate)

    def test_a_result_for_a_different_deck_is_rejected(self, candidate):
        """A stored simulation must be known to describe this list."""
        with pytest.raises(SimulatorContractError, match="different deck"):
            parse_result(_payload(candidate, deck_sha256="0" * 64), candidate)

    def test_missing_honesty_fields_are_rejected(self, candidate):
        """The framing is required, so a number cannot be shown without it."""
        body = _payload(candidate)
        del body["does_not_measure"]
        with pytest.raises(SimulatorContractError, match="failed validation"):
            parse_result(body, candidate)

    def test_an_out_of_range_probability_is_rejected(self, candidate):
        with pytest.raises(SimulatorContractError):
            parse_result(
                _payload(candidate, assembly=[{"turn": 3, "probability": 1.7}]),
                candidate,
            )


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
        result = FixtureSimulatorClient().simulate(candidate)
        assert result.inert_cards
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
        payload = json.dumps(_payload(candidate))
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
        payload = json.dumps(_payload(candidate, deck_sha256="f" * 64))
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
            f'  if [ "$1" = "--candidate" ]; then cp "$2" {out}; fi\n'
            "  shift\ndone\nexit 1\n",
        )
        SubprocessSimulatorClient(binary).simulate(candidate)
        document = json.loads(out.read_text())
        assert document["schema"] == "cedh-deck-candidate.v1"
        assert all(card["oracle_id"] for card in document["cards"])

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
        ],
    )
    def test_mode_selects_the_client(self, mode, expected):
        from sabermetrics.cedh.settings import SimulatorSettings

        assert isinstance(build_simulator(SimulatorSettings(mode=mode)), expected)

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
