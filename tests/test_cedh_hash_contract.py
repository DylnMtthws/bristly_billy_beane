"""The Deck Lab's half of the cross-repository hash and wire contracts.

Two things are checked here, and both exist because of the same failure.

**The hash contract.** ``fixtures/cedh/contracts/hash-golden-vectors.json`` is
vendored verbatim from ``commander_simulator``, where the C++ simulator and the
Python exporter are checked against the same file. This module checks *this*
repository's ADR-025 implementation against it too. A hash tested only against
itself agrees with itself forever; that is how one field name came to mean "the
deck" here and "the deck plus the strategy pack" there.

**The wire contract, in the direction we send.** The documents this repository
produces are validated against the schemas the simulator publishes for them.
The previous revision pinned only the response, so the request body was prose
on both sides and every real build failed with ``422 unsupported candidate
schema_version`` while both suites stayed green.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from sabermetrics.cedh.builder import build_candidate
from sabermetrics.cedh.candidate import DeckCandidate
from sabermetrics.cedh.wire import (
    CANDIDATE_SCHEMA_ID,
    REQUEST_SCHEMA_ID,
    candidate_document,
    simulation_request,
)

CONTRACTS = Path(__file__).resolve().parents[1] / "fixtures" / "cedh" / "contracts"
GOLDEN = json.loads((CONTRACTS / "hash-golden-vectors.json").read_text())
VECTORS: dict[str, dict[str, Any]] = {v["name"]: v for v in GOLDEN["vectors"]}


def _schema(name: str) -> dict[str, Any]:
    return json.loads((CONTRACTS / name).read_text())


def _deck_sha256(commander_oracle_ids, library) -> str:
    """This repository's ADR-025 algorithm, exercised without a DeckCandidate.

    ``DeckCandidate`` cannot hold the golden vectors: it validates that the
    library sums to exactly 99, and the vectors are deliberately tiny so a
    human can read them. The preimage below is the same one
    ``DeckCandidate.deck_sha256`` builds, and
    :func:`test_the_candidate_property_and_this_helper_agree` pins them
    together so this helper cannot drift into being a second definition.
    """
    digest = hashlib.sha256()
    for oracle_id in sorted(commander_oracle_ids):
        digest.update(f"C:{oracle_id}\n".encode())
    for card in sorted(library, key=lambda c: c["oracle_id"]):
        digest.update(f"{card['oracle_id']}:{card['quantity']}\n".encode())
    return "sha256:" + digest.hexdigest()


def deck_hash_of(name: str) -> str:
    vector = VECTORS[name]
    return _deck_sha256(vector["commander_oracle_ids"], vector["library"])


class TestTheSharedHashVectors:
    @pytest.mark.parametrize("name", sorted(VECTORS))
    def test_every_published_deck_digest_is_reproduced_exactly(self, name):
        assert deck_hash_of(name) == VECTORS[name]["deck_sha256"]

    def test_ordering_does_not_affect_the_deck_hash(self):
        assert deck_hash_of("base") == deck_hash_of("reordered_inputs")
        assert deck_hash_of("two_commanders") == deck_hash_of(
            "two_commanders_reordered"
        )

    def test_changing_the_commander_changes_the_deck_hash(self):
        assert deck_hash_of("base") != deck_hash_of("commander_changed")

    def test_changing_a_card_changes_the_deck_hash(self):
        assert deck_hash_of("base") != deck_hash_of("card_changed")

    def test_changing_a_quantity_changes_the_deck_hash(self):
        assert deck_hash_of("base") != deck_hash_of("quantity_changed")

    def test_changing_the_strategy_pack_does_not_change_the_deck_hash(self):
        """ADR-025: a deck hash identifies a LIST, not a list plus its pack."""
        assert deck_hash_of("base") == deck_hash_of("pack_changed")

    def test_changing_the_strategy_pack_does_change_the_input_fingerprint(self):
        """The other half of the pair, read from the shared vectors.

        This repository does not compute ``simulation_input_sha256`` — it
        cannot, because the resolved pack's content hash and the card-data
        version are the simulator's to know. It consumes the value, so what it
        pins here is the *property*: same deck, different pack, same deck hash
        and a different measurement.
        """
        assert (
            VECTORS["base"]["simulation_input_sha256"]
            != VECTORS["pack_changed"]["simulation_input_sha256"]
        )
        assert VECTORS["base"]["deck_sha256"] == VECTORS["pack_changed"]["deck_sha256"]

    def test_run_parameters_move_only_the_input_fingerprint(self):
        assert deck_hash_of("base") == deck_hash_of("seed_changed")
        assert (
            VECTORS["base"]["simulation_input_sha256"]
            != VECTORS["seed_changed"]["simulation_input_sha256"]
        )

    @pytest.mark.parametrize(
        "rule", GOLDEN["assertions"], ids=lambda r: r["requirement"]
    )
    def test_the_shared_files_own_deck_hash_assertions_hold(self, rule):
        """Walks the vectors' ``assertions`` block, so a requirement added on
        the simulator side is executed here on the next re-vendor."""
        if "equal_deck_sha256" in rule:
            left, right = rule["equal_deck_sha256"]
            assert deck_hash_of(left) == deck_hash_of(right), rule["requirement"]
        if "different_deck_sha256" in rule:
            left, right = rule["different_deck_sha256"]
            assert deck_hash_of(left) != deck_hash_of(right), rule["requirement"]

    def test_the_candidate_property_and_this_helper_agree(
        self, kinnan_pack, cedh_cards
    ):
        """Pins the readable helper above to the production property."""
        candidate = build_candidate(kinnan_pack, cedh_cards)
        assert candidate.deck_sha256_wire == _deck_sha256(
            candidate.commander.oracle_ids,
            [
                {"oracle_id": c.oracle_id, "quantity": c.quantity}
                for c in candidate.cards
            ],
        )
        assert candidate.deck_sha256_wire == f"sha256:{candidate.deck_sha256}"


class TestTheRequestWeSend:
    """The direction that was never pinned, and so was never right."""

    @pytest.fixture
    def candidate(self, kinnan_pack, cedh_cards) -> DeckCandidate:
        return build_candidate(kinnan_pack, cedh_cards)

    def test_the_candidate_document_validates_against_the_vendored_schema(
        self, candidate
    ):
        schema = _schema("cedh-deck-candidate.v2.schema.json")
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(
            candidate_document(candidate)
        )

    def test_the_request_envelope_validates_against_the_vendored_schema(
        self, candidate
    ):
        """Validates the whole envelope, resolving its $ref to the vendored
        candidate schema rather than reaching for the network."""
        candidate_schema = _schema("cedh-deck-candidate.v2.schema.json")
        envelope_schema = _schema("cedh-simulation-request.v1.schema.json")
        Draft202012Validator.check_schema(envelope_schema)
        registry = Resource.from_contents(candidate_schema) @ Registry()
        validator = Draft202012Validator(
            envelope_schema, format_checker=FormatChecker(), registry=registry
        )

        document = simulation_request(candidate, games=20000, turn=3)
        validator.validate(document)

        assert document["schema_version"] == REQUEST_SCHEMA_ID
        assert set(document) == {
            "schema_version",
            "candidate",
            "games",
            "turn",
            "seed",
            "scenario",
            "sweep",
            "ablate",
        }
        assert 0 <= document["seed"] <= 2**64 - 1

    def test_an_envelope_missing_its_version_fails_the_vendored_schema(self, candidate):
        """The exact shape that used to go on the wire, refused here."""
        candidate_schema = _schema("cedh-deck-candidate.v2.schema.json")
        envelope_schema = _schema("cedh-simulation-request.v1.schema.json")
        registry = Resource.from_contents(candidate_schema) @ Registry()
        validator = Draft202012Validator(
            envelope_schema, format_checker=FormatChecker(), registry=registry
        )
        document = simulation_request(candidate, games=20000, turn=3)
        del document["schema_version"]
        assert list(validator.iter_errors(document))

    def test_this_repositorys_own_export_document_is_not_wire_shaped(self, candidate):
        """``to_document()`` is a different artifact and must not be sent.

        Posting it is precisely what happened before: it is keyed ``schema``/
        ``cards``/``commander`` where the simulator requires ``schema_version``/
        ``library``/``commander_oracle_ids``, so every real build was refused.
        """
        candidate_schema = _schema("cedh-deck-candidate.v2.schema.json")
        validator = Draft202012Validator(
            candidate_schema, format_checker=FormatChecker()
        )
        assert list(validator.iter_errors(candidate.to_document()))
        assert not list(validator.iter_errors(candidate_document(candidate)))

    def test_the_deck_hash_we_send_is_the_deck_hash_we_computed(self, candidate):
        document = candidate_document(candidate)
        assert document["deck_sha256"] == candidate.deck_sha256_wire
        assert document["schema_version"] == CANDIDATE_SCHEMA_ID

    def test_the_strategy_pack_is_requested_but_is_not_part_of_the_deck_hash(
        self, candidate
    ):
        """The precise confusion v1 encoded, asserted from this side.

        The pack travels in the request as an execution-context *request*. It
        does not participate in deck identity, so asking for a different pack
        cannot make this look like a different deck.
        """
        document = candidate_document(candidate)
        assert document["strategy_pack_id"] == "kinnan-midrange-goldfish"

        other = candidate.model_copy(
            update={
                "provenance": candidate.provenance.model_copy(
                    update={"simulator_pack_id": "derived-generic"}
                )
            }
        )
        other_document = candidate_document(other)
        assert other_document["strategy_pack_id"] == "derived-generic"
        assert other_document["deck_sha256"] == document["deck_sha256"]

    def test_the_removed_v1_candidate_hash_is_not_sent(self, candidate):
        assert "candidate_hash" not in candidate_document(candidate)

    def test_cards_travel_by_oracle_id_and_names_do_not(self, candidate):
        """A name-keyed handoff loses multi-faced cards at the far end."""
        document = candidate_document(candidate)
        assert sum(card["quantity"] for card in document["library"]) == 99
        for card in document["library"]:
            assert set(card) == {"oracle_id", "quantity"}

    def test_sweep_and_ablate_cannot_both_be_requested(self, candidate):
        with pytest.raises(ValueError, match="mutually exclusive"):
            simulation_request(
                candidate, games=10, turn=3, sweep=True, ablate=("Sol Ring",)
            )
