"""Orchestration: what the model may do, and what happens when it misbehaves."""

from __future__ import annotations

import pytest

from sabermetrics.cedh.domain import BuildConstraints, LabRequest
from sabermetrics.cedh.gateway_fixture import FixtureGateway
from sabermetrics.cedh.lab import CedhDeckLab, build_lab
from sabermetrics.cedh.simulator import DisabledSimulatorClient

GOOD_INTENT = {
    "pack_id": "kinnan_basalt",
    "restated_intent": "a fast Kinnan list",
    "confidence": "high",
}
GOOD_SUMMARY = {
    "summary": "Kinnan appeared 42 times across 6 events.",
    "events_cited": 5,
    "decks_cited": 42,
    "caveats": ["presence is exposure, not win rate"],
    "chunk_ids": ["meta-presence"],
}
GOOD_EXPLANATION = {
    "game_plan": "Assemble Basalt Monolith under Kinnan.",
    "primary_line": "Ramp, resolve the engine, protect it.",
    "weaknesses": ["a two-card engine is fragile"],
    "key_oracle_ids": [],
    "mulligan_guidance": "Keep hands with two mana sources.",
}


def _gateway(**overrides):
    scripts = {
        "cedh:intent": GOOD_INTENT,
        "cedh:evidence_summary": GOOD_SUMMARY,
        "cedh:explanation": GOOD_EXPLANATION,
    }
    scripts.update(overrides)
    return FixtureGateway(scripts)


def _lab(cards, meta, simulator, gateway=None):
    return build_lab(cards=cards, meta=meta, simulator=simulator, gateway=gateway)


@pytest.fixture
def lab(cedh_cards, cedh_meta_populated, cedh_simulator):
    return _lab(cedh_cards, cedh_meta_populated, cedh_simulator, _gateway())


class TestHappyPath:
    def test_builds_explains_and_simulates(self, lab):
        result = lab.run(LabRequest(pack_id="kinnan_basalt"))
        assert result.supported
        assert sum(c.quantity for c in result.candidate.cards) == 99
        assert result.simulated
        assert result.explanation is not None
        assert result.evidence_summary is not None
        assert result.total_cost_usd > 0

    def test_free_text_intent_selects_a_pack(self, lab):
        result = lab.run(LabRequest(raw_intent="I want a fast Kinnan deck"))
        assert result.pack_id == "kinnan_basalt"
        assert result.intent.confidence == "high"

    def test_intent_classification_uses_the_lowest_reasoning_mode(self, lab):
        lab.run(LabRequest(raw_intent="fast Kinnan"))
        intent_call = next(
            r for r in lab.gateway.requests if r.call_type == "cedh:intent"
        )
        assert intent_call.mode.value == "classify"

    def test_no_call_uses_elevated_reasoning_without_a_stated_ambiguity(self, lab):
        lab.run(LabRequest(raw_intent="fast Kinnan"))
        for request in lab.gateway.requests:
            if request.mode.value == "adjudicate":
                assert request.ambiguity_note

    def test_the_evidence_hash_travels_onto_the_candidate(self, lab):
        result = lab.run(LabRequest(pack_id="kinnan_basalt"))
        assert (
            result.candidate.provenance.evidence_hash == result.evidence.evidence_hash
        )

    def test_http_simulator_headers_travel_onto_candidate_provenance(
        self, cedh_cards, cedh_meta_populated, cedh_simulator
    ):
        class HeaderSimulator:
            def supported_commander_keys(self):
                return cedh_simulator.supported_commander_keys()

            def simulate(self, candidate):
                result = cedh_simulator.simulate(candidate)
                return result.model_copy(
                    update={
                        "simulator_version": "2.3.4",
                        "result_schema": "cedh-simulation-result.v3",
                        "cards_sha256": "a" * 64,
                        "simulator_threads": "4",
                    }
                )

        result = _lab(cedh_cards, cedh_meta_populated, HeaderSimulator()).run(
            LabRequest(pack_id="kinnan_basalt")
        )
        provenance = result.candidate.provenance
        assert provenance.simulator_version == "2.3.4"
        assert provenance.simulator_result_schema == "cedh-simulation-result.v3"
        assert provenance.simulator_cards_sha256 == "a" * 64
        assert provenance.simulator_threads == "4"


class TestModelIsNotOnTheCriticalPath:
    def test_the_deck_is_built_with_no_model_at_all(
        self, cedh_cards, cedh_meta_populated, cedh_simulator
    ):
        result = _lab(cedh_cards, cedh_meta_populated, cedh_simulator).run(
            LabRequest(pack_id="kinnan_basalt")
        )
        assert result.supported
        assert sum(c.quantity for c in result.candidate.cards) == 99
        assert result.explanation is None
        assert result.total_cost_usd == 0

    def test_a_provider_failure_costs_prose_and_nothing_else(
        self, cedh_cards, cedh_meta_populated, cedh_simulator
    ):
        gateway = FixtureGateway(
            {"cedh:intent": GOOD_INTENT},
            fail_call_types=frozenset({"cedh:evidence_summary", "cedh:explanation"}),
        )
        result = _lab(cedh_cards, cedh_meta_populated, cedh_simulator, gateway).run(
            LabRequest(pack_id="kinnan_basalt")
        )
        assert result.candidate is not None
        assert result.explanation is None
        assert any("deck below is unaffected" in w for w in result.warnings)

    def test_the_cost_ceiling_stops_prose_not_the_build(
        self, cedh_cards, cedh_meta_populated, cedh_simulator
    ):
        from sabermetrics.errors import LLMCostCeilingExceeded

        class _Broke:
            provider = "x"
            model_id = "y"

            def generate(self, request):
                raise LLMCostCeilingExceeded("ceiling reached")

        result = CedhDeckLab(
            cards=cedh_cards,
            meta=cedh_meta_populated,
            registry=__import__(
                "sabermetrics.cedh.packs", fromlist=["PackRegistry"]
            ).PackRegistry(cedh_cards),
            simulator=cedh_simulator,
            gateway=_Broke(),
        ).run(LabRequest(pack_id="kinnan_basalt"))
        assert result.candidate is not None
        assert any("cost ceiling" in w for w in result.warnings)


class TestModelCrossChecks:
    def test_an_explanation_citing_a_card_not_in_the_deck_is_pruned(
        self, cedh_cards, cedh_meta_populated, cedh_simulator
    ):
        gateway = _gateway(
            **{
                "cedh:explanation": {
                    **GOOD_EXPLANATION,
                    "key_oracle_ids": ["a-card-that-is-not-here"],
                }
            }
        )
        result = _lab(cedh_cards, cedh_meta_populated, cedh_simulator, gateway).run(
            LabRequest(pack_id="kinnan_basalt")
        )
        assert result.explanation.key_oracle_ids == []
        assert any("not in this deck" in w for w in result.warnings)

    def test_a_real_card_reference_survives(
        self, cedh_cards, cedh_meta_populated, cedh_simulator, kinnan_pack
    ):
        real = kinnan_pack.auto_include[0]
        gateway = _gateway(
            **{
                "cedh:explanation": {
                    **GOOD_EXPLANATION,
                    "key_oracle_ids": [real],
                }
            }
        )
        result = _lab(cedh_cards, cedh_meta_populated, cedh_simulator, gateway).run(
            LabRequest(pack_id="kinnan_basalt")
        )
        assert result.explanation.key_oracle_ids == [real]

    def test_an_invented_chunk_citation_is_dropped(
        self, cedh_cards, cedh_meta_populated, cedh_simulator
    ):
        gateway = _gateway(
            **{
                "cedh:evidence_summary": {
                    **GOOD_SUMMARY,
                    "chunk_ids": ["meta-presence", "invented-chunk"],
                }
            }
        )
        result = _lab(cedh_cards, cedh_meta_populated, cedh_simulator, gateway).run(
            LabRequest(pack_id="kinnan_basalt")
        )
        assert result.evidence_summary.chunk_ids == ["meta-presence"]
        assert any("not sent to it" in w for w in result.warnings)

    def test_an_inflated_sample_size_is_corrected_downward(
        self, cedh_cards, cedh_meta_populated, cedh_simulator
    ):
        """A model may not make the evidence look stronger than it is."""
        gateway = _gateway(
            **{
                "cedh:evidence_summary": {
                    **GOOD_SUMMARY,
                    "events_cited": 900,
                    "decks_cited": 9000,
                }
            }
        )
        result = _lab(cedh_cards, cedh_meta_populated, cedh_simulator, gateway).run(
            LabRequest(pack_id="kinnan_basalt")
        )
        assert result.evidence_summary.events_cited <= result.evidence.events_seen
        assert result.evidence_summary.decks_cited <= result.evidence.decks_seen
        assert sum("corrected downward" in w for w in result.warnings) == 2

    def test_an_unknown_pack_id_is_unsupported_not_a_substitution(
        self, cedh_cards, cedh_meta_populated, cedh_simulator
    ):
        gateway = _gateway(
            **{"cedh:intent": {**GOOD_INTENT, "pack_id": "invented_pack"}}
        )
        result = _lab(cedh_cards, cedh_meta_populated, cedh_simulator, gateway).run(
            LabRequest(raw_intent="something else entirely")
        )
        assert result.supported is False
        assert result.candidate is None
        assert any("not a supported pack" in w for w in result.warnings)


class TestDegradedStates:
    def test_absent_tournament_data_is_warned_about(
        self, cedh_cards, cedh_meta_absent, cedh_simulator
    ):
        result = _lab(cedh_cards, cedh_meta_absent, cedh_simulator).run(
            LabRequest(pack_id="kinnan_basalt")
        )
        assert result.candidate is not None
        assert result.candidate.provenance.meta_available is False
        assert any("No tournament evidence" in w for w in result.warnings)

    def test_a_disabled_simulator_yields_not_simulated(
        self, cedh_cards, cedh_meta_populated
    ):
        result = _lab(cedh_cards, cedh_meta_populated, DisabledSimulatorClient()).run(
            LabRequest(pack_id="kinnan_basalt")
        )
        assert result.simulated is False
        assert result.simulation.reason == "simulator_disabled"
        assert any("Not simulated" in w for w in result.warnings)

    def test_the_model_is_told_not_to_invent_a_simulation_figure(
        self, cedh_cards, cedh_meta_populated
    ):
        gateway = _gateway()
        _lab(cedh_cards, cedh_meta_populated, DisabledSimulatorClient(), gateway).run(
            LabRequest(pack_id="kinnan_basalt")
        )
        explain = next(r for r in gateway.requests if r.call_type == "cedh:explanation")
        assert "NOT SIMULATED" in explain.user
        assert "Do not estimate, invent" in explain.user

    def test_a_simulated_figure_reaches_the_model_with_its_framing(self, lab):
        lab.run(LabRequest(pack_id="kinnan_basalt"))
        explain = next(
            r for r in lab.gateway.requests if r.call_type == "cedh:explanation"
        )
        assert "does not measure" in explain.user

    def test_free_text_without_a_model_asks_for_an_explicit_pack(
        self, cedh_cards, cedh_meta_populated, cedh_simulator
    ):
        lab = _lab(cedh_cards, cedh_meta_populated, cedh_simulator)
        # Two packs, so the single-pack shortcut cannot apply.
        lab.registry._definitions["second"] = lab.registry._definitions["kinnan_basalt"]
        result = lab.run(LabRequest(raw_intent="something"))
        assert result.supported is False
        assert any("No model gateway" in w for w in result.warnings)

    def test_impossible_constraints_report_rather_than_build(
        self, cedh_cards, cedh_meta_populated, cedh_simulator, kinnan_pack
    ):
        result = _lab(cedh_cards, cedh_meta_populated, cedh_simulator).run(
            LabRequest(
                pack_id="kinnan_basalt",
                constraints=BuildConstraints(
                    excluded_oracle_ids=frozenset(list(kinnan_pack.pool)[:90])
                ),
            )
        )
        assert result.candidate is None
        assert "cannot produce a deck" in result.unsupported_detail


class TestPackSupport:
    def test_summaries_report_simulator_support_per_pack(self, lab):
        summaries = lab.pack_summaries()
        assert summaries
        assert all(s.simulator_supported for s in summaries if s.supported)

    def test_an_unresolvable_pack_is_listed_as_unsupported_not_hidden(
        self, cedh_cards, cedh_meta_populated, cedh_simulator, tmp_path
    ):
        import yaml

        from sabermetrics.cedh.packs import PackRegistry, summarize

        (tmp_path / "ghost.yaml").write_text(
            yaml.safe_dump(
                {
                    "pack_id": "ghost",
                    "name": "Ghost pack",
                    "summary": "names a card that does not exist",
                    "commander_names": ["Kinnan, Bonder Prodigy"],
                    "primary_win_package": {"name": "w", "kind": "combo", "pieces": []},
                    "role_targets": {"flex": 1},
                    "cards": [
                        {"name": "A Card That Does Not Exist", "roles": ["flex"]}
                    ],
                }
            )
        )
        registry = PackRegistry(cedh_cards, tmp_path)
        summaries = summarize(registry)
        assert len(summaries) == 1
        assert summaries[0].supported is False
        assert "A Card That Does Not Exist" in summaries[0].missing_names

    def test_a_pack_never_silently_shortens_when_a_card_is_missing(
        self, cedh_cards, tmp_path
    ):
        """A name that does not resolve is a defect, not 98 cards."""
        import yaml

        from sabermetrics.cedh.errors import UnsupportedCommander
        from sabermetrics.cedh.packs import PackRegistry

        (tmp_path / "ghost.yaml").write_text(
            yaml.safe_dump(
                {
                    "pack_id": "ghost",
                    "name": "Ghost",
                    "summary": "s",
                    "commander_names": ["Kinnan, Bonder Prodigy"],
                    "primary_win_package": {"name": "w", "kind": "combo", "pieces": []},
                    "role_targets": {"flex": 1},
                    "cards": [{"name": "Nope", "roles": ["flex"]}],
                }
            )
        )
        with pytest.raises(UnsupportedCommander, match="did not resolve"):
            PackRegistry(cedh_cards, tmp_path).get("ghost")


def test_the_shipped_pack_matches_the_list_the_simulator_models():
    """The pack's 99 are the real list, not an approximation of it."""
    import os
    import tomllib
    from pathlib import Path

    import yaml

    pack = yaml.safe_load(
        Path("config/cedh_packs/kinnan_basalt.yaml").read_text(encoding="utf-8")
    )
    assert len(pack["cards"]) == 99
    assert sum(pack["role_targets"].values()) == 99
    assert "cards_sha256" in pack["source"]

    configured_path = os.environ.get("CEDH_KINNAN_DECK_TOML")
    if not configured_path:
        pytest.skip("CEDH_KINNAN_DECK_TOML is not set")
    simulator_list = Path(configured_path)
    if not simulator_list.is_file():
        pytest.skip("CEDH_KINNAN_DECK_TOML does not name a file")
    with simulator_list.open("rb") as handle:
        mainboard = set(tomllib.load(handle)["cards"]["mainboard"])
    assert {c["name"] for c in pack["cards"]} == mainboard


def _imported_modules(path) -> set[str]:
    """Every module name imported by a file, from its AST.

    The AST rather than the text: these files *describe* the sources they must
    not call, and a substring search over the source would match the prose that
    states the rule.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _string_constants(path) -> list[str]:
    """Every string literal in a file except module/class/function docstrings."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_generation_path_never_calls_a_deck_site():
    """No EDHTop16, Scryfall, TopDeck or deck-hosting host in the cEDH path.

    Hostnames, not bare words: these modules discuss those sources by name in
    their own documentation, and the rule is about what they connect to.
    """
    from pathlib import Path

    hosts = (
        "edhtop16.com",
        "scryfall.com",
        "topdeck.gg",
        "moxfield.com",
        "archidekt.com",
        "deckstats.net",
        "edhrec.com",
        "reddit.com",
        "commanderspellbook.com",
        "magicthegathering.io",
    )
    for path in Path("src/sabermetrics/cedh").rglob("*.py"):
        for literal in _string_constants(path):
            lowered = literal.lower()
            for host in hosts:
                assert host not in lowered, f"{path} contains {host!r}"


def test_the_cedh_path_does_not_depend_on_legacy_ingestion():
    """Existing ingestion modules may remain; the new cEDH path must not use them."""
    from pathlib import Path

    forbidden = (
        "sabermetrics.ingestion",
        "sabermetrics.pipeline",
        "sabermetrics.reasoning",
        "sabermetrics.analytics",
        "sabermetrics.reference_layer",
        "anthropic",
        "mtg_parser",
        "mtgsdk",
    )
    for path in Path("src/sabermetrics/cedh").rglob("*.py"):
        for module in _imported_modules(path):
            for banned in forbidden:
                assert not module.startswith(banned), f"{path} imports {module}"


def test_the_cedh_path_does_not_import_a_vendor_model_sdk():
    """Provider neutrality is an import rule, not a naming convention."""
    from pathlib import Path

    for path in Path("src/sabermetrics/cedh").rglob("*.py"):
        modules = _imported_modules(path)
        assert not any(
            m.startswith(("anthropic", "openai", "google.generativeai"))
            for m in modules
        ), f"{path} imports a vendor SDK"


class TestAbsenceOfAModelIsVisible:
    """`absence_is_visible` applied to the narrative layer.

    The deck is deterministic, so running with no model provider is a supported
    mode rather than a broken one — but a supported mode still has to say what
    it did not do. Before this was fixed the run returned an empty ``warnings``
    list while both narrative fields were ``None``, so a reader met a deck with
    no explanation and nothing telling them why.

    Note the contrast these tests pin: a provider *failure* already warned
    (``_call``); only the never-configured path was silent.
    """

    def test_a_missing_gateway_warns_that_there_is_no_explanation(
        self, cedh_cards, cedh_meta_populated, cedh_simulator
    ):
        result = _lab(cedh_cards, cedh_meta_populated, cedh_simulator, None).run(
            LabRequest(pack_id="kinnan_basalt")
        )
        assert result.explanation is None
        assert any(
            "no written explanation" in w for w in result.warnings
        ), f"an absent explanation must be stated, got {result.warnings!r}"

    def test_a_missing_gateway_warns_that_evidence_was_not_summarised(
        self, cedh_cards, cedh_meta_populated, cedh_simulator
    ):
        result = _lab(cedh_cards, cedh_meta_populated, cedh_simulator, None).run(
            LabRequest(pack_id="kinnan_basalt")
        )
        assert result.evidence_summary is None
        assert any("not summarised" in w for w in result.warnings)

    def test_an_empty_evidence_package_is_not_also_called_unsummarised(
        self, cedh_cards, cedh_meta_populated, cedh_simulator
    ):
        """The chunks guard runs before the gateway guard, so gaps never double up.

        Tested directly rather than through a fixture: an absent *tournament*
        corpus still yields curated strategy chunks, so no repository fixture
        reaches zero chunks. The ordering is a real invariant even though the
        fixtures cannot reach it, and asserting it here is how it survives
        someone reordering the two guards later.
        """
        from types import SimpleNamespace

        lab = _lab(cedh_cards, cedh_meta_populated, cedh_simulator, None)
        warnings: list[str] = []
        assert lab._summarise_evidence(SimpleNamespace(chunks=[]), [], warnings) is None
        assert warnings == []

    def test_absent_tournament_data_still_reports_both_gaps_separately(
        self, cedh_cards, cedh_meta_absent, cedh_simulator
    ):
        """Two distinct gaps read as two distinct sentences, not one merged claim.

        No tournament corpus and no model provider are different failures with
        different fixes, and the curated chunks that remain really were left
        unsummarised.
        """
        result = _lab(cedh_cards, cedh_meta_absent, cedh_simulator, None).run(
            LabRequest(pack_id="kinnan_basalt")
        )
        assert any("No tournament evidence is available" in w for w in result.warnings)
        assert any("not summarised" in w for w in result.warnings)

    def test_a_configured_gateway_adds_neither_warning(self, lab):
        result = lab.run(LabRequest(pack_id="kinnan_basalt"))
        assert result.explanation is not None
        assert not any("No model provider is configured" in w for w in result.warnings)
