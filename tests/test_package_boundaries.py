"""The Research Assistant's import rules, asserted rather than documented.

Three new packages each have a stated import contract (spec §7.2). A contract
nobody checks is a comment. These tests are the check.

The walker lives in ``tests/_import_graph.py``; see its docstring for the four
evasions it closes that the older inline version allowed.
"""

from __future__ import annotations

import pytest

from tests._import_graph import STDLIB, covers, violations

#: The legacy casual/budget path. Its judgements answer a different question
#: than "does this list win a tournament round" (ADR-019), so the new path does
#: not import it — not even for a helper.
LEGACY = (
    "sabermetrics.pipeline",
    "sabermetrics.reasoning",
    "sabermetrics.ingestion",
    "sabermetrics.analytics",
)

#: Provider neutrality is an import rule, not a naming convention (ADR-021).
VENDOR_SDKS = (
    "anthropic",
    "openai",
    "google.generativeai",
    "google.genai",
    "cohere",
    "mistralai",
    "ollama",
    "litellm",
    "langchain",
    "mtg_parser",
    "mtgsdk",
)


#: ``mechanics/`` may import itself. R0 shipped the package as four independent
#: modules, so "imports nothing from ``sabermetrics``" and "imports nothing from
#: a sibling package" were the same rule and were written as the former. R1 adds
#: ``mechanics/tags/``, where the plan requires one definition file per tag
#: family and every family shares one predicate algebra — so the two rules come
#: apart, and the one that was always meant is the latter.
#:
#: The three invariants the purity rule exists for are untouched and still
#: asserted below: no sibling package, no third-party import, no filesystem.
SELF = ("sabermetrics.mechanics",)


class TestMechanicsIsPure:
    """stdlib + ``re`` only. The strictest rule in the repo, and deliberately so.

    A single config read added "just for a default" turns a total function into
    one that behaves differently depending on where it runs, and every caller
    inherits that without being asked.
    """

    def test_mechanics_imports_only_stdlib(self):
        found = violations("mechanics", allowed=SELF)
        assert not found, "mechanics/ may import stdlib only:\n" + "\n".join(found)

    def test_mechanics_does_not_import_sabermetrics(self):
        found = violations("mechanics", banned=("sabermetrics",), exempt=SELF)
        assert (
            not found
        ), "mechanics/ must not import the rest of the package:\n" + "\n".join(found)

    def test_the_self_exemption_does_not_spare_a_sibling(self, tmp_path, monkeypatch):
        """The exemption is for ``mechanics`` itself, not for a lookalike.

        Without segment-bounded matching, exempting ``sabermetrics.mechanics``
        would also exempt ``sabermetrics.mechanics_v2`` — and a package named to
        slip past a boundary check is exactly what a boundary check is for.
        """
        import tests._import_graph as ig

        pkg = tmp_path / "sabermetrics" / "mechanics"
        pkg.mkdir(parents=True)
        (pkg / "mod.py").write_text(
            "from sabermetrics.mechanics.tags import predicates\n"
            "from sabermetrics.mechanics_v2 import predicates as p2\n"
            "from sabermetrics.analytics import cvar\n"
        )
        monkeypatch.setattr(ig, "SRC", tmp_path)
        found = ig.violations("mechanics", banned=("sabermetrics",), exempt=SELF)
        assert len(found) == 2, found
        assert any("mechanics_v2" in line for line in found)
        assert any(":3 " in line for line in found)

    def test_mechanics_reads_no_files(self):
        """No ``open``/``Path.read_*``/``yaml.safe_load`` anywhere in the package.

        This is why ``role_tagger`` was not promoted: it re-reads
        ``config/role_tag_overrides.yaml`` from disk on every call.
        """
        import ast
        from pathlib import Path as P

        from tests._import_graph import SRC

        offenders = []
        for path in sorted((SRC / "sabermetrics" / "mechanics").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = (
                        fn.id
                        if isinstance(fn, ast.Name)
                        else (fn.attr if isinstance(fn, ast.Attribute) else "")
                    )
                    if name in {"open", "read_text", "read_bytes", "safe_load", "load"}:
                        offenders.append(
                            f"{path.relative_to(P(SRC))}:{node.lineno} {name}()"
                        )
        assert not offenders, "mechanics/ must not touch the filesystem:\n" + "\n".join(
            offenders
        )


class TestSubstrateBoundary:
    def test_substrate_does_not_import_the_legacy_path(self):
        found = violations("substrate", banned=LEGACY)
        assert not found, "\n".join(found)

    def test_substrate_does_not_import_a_vendor_sdk(self):
        found = violations("substrate", banned=VENDOR_SDKS)
        assert not found, "\n".join(found)


class TestAssistantBoundary:
    def test_assistant_does_not_import_the_legacy_path(self):
        found = violations("assistant", banned=LEGACY)
        assert not found, "\n".join(found)

    def test_assistant_does_not_import_a_vendor_sdk(self):
        found = violations("assistant", banned=VENDOR_SDKS)
        assert not found, "\n".join(found)


class TestCedhDoesNotImportTheAssistant:
    """The dependency points one way only (ADR-030).

    ``assistant/`` borrows the gateway, the ledger and the simulator client.
    If ``cedh/`` reached back, the deterministic generation path would acquire a
    model-planning dependency and ADR-022's guarantee — that selection finishes
    before the first model call — would stop being structural.
    """

    def test_cedh_does_not_import_assistant(self):
        found = violations("cedh", banned=("sabermetrics.assistant",))
        assert not found, "\n".join(found)


class TestTheWalkerCatchesEvasions:
    """The gate is only worth what it cannot be walked around.

    Each case here is a real way to import a banned module that the repo's
    older inline walker did not see.
    """

    @pytest.mark.parametrize(
        "source,why",
        [
            ("from sabermetrics import pipeline\n", "aliased submodule import"),
            (
                "import importlib\nimportlib.import_module('sabermetrics.reasoning')\n",
                "dynamic import",
            ),
            ("__import__('sabermetrics.analytics')\n", "__import__ builtin"),
            (
                "def f():\n    from sabermetrics import ingestion\n",
                "lazy import in a function",
            ),
        ],
    )
    def test_evasion_is_detected(self, tmp_path, monkeypatch, source, why):
        import tests._import_graph as ig

        pkg = tmp_path / "sabermetrics" / "probe"
        pkg.mkdir(parents=True)
        (pkg / "mod.py").write_text(source)
        monkeypatch.setattr(ig, "SRC", tmp_path)
        found = ig.violations("probe", banned=LEGACY)
        assert found, f"walker missed a {why}"

    def test_a_similar_prefix_is_not_a_false_positive(self, tmp_path, monkeypatch):
        """``pipelines_new`` is not ``pipeline``. Segment boundaries matter."""
        import tests._import_graph as ig

        pkg = tmp_path / "sabermetrics" / "probe"
        pkg.mkdir(parents=True)
        (pkg / "mod.py").write_text("from sabermetrics import pipelines_new\n")
        monkeypatch.setattr(ig, "SRC", tmp_path)
        assert not ig.violations("probe", banned=LEGACY)

    def test_future_import_is_not_a_violation(self):
        assert "__future__" in STDLIB

    def test_covers_matches_on_segment_boundaries(self):
        assert covers("a.b", "a.b")
        assert covers("a.b.c", "a.b")
        assert not covers("a.bc", "a.b")

    def test_import_statements_finds_relative_imports(self, tmp_path, monkeypatch):
        import tests._import_graph as ig

        pkg = tmp_path / "sabermetrics" / "probe"
        pkg.mkdir(parents=True)
        (pkg / "mod.py").write_text("from ..analytics import cvar\n")
        monkeypatch.setattr(ig, "SRC", tmp_path)
        names = [c for _, cands in ig.import_statements(pkg / "mod.py") for c in cands]
        assert any("analytics" in n for n in names)
