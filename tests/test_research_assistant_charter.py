"""Doc claims bound to the code that decides them.

A charter line asserting "v2" while the code pins v3 is not a typo — it is a
reader being told the wrong thing about a contract. These tests bind each claim
to its source of truth, so the *next* drift fails a test instead of rotting for
a release.

Deliberately NOT literal-string matching. Asserting ``"v3" in charter`` would go
stale the same way the "v2" line did. Asserting ``RESULT_SCHEMA_ID in charter``
cannot: change the pin and this test tells you which doc to update.
"""

from __future__ import annotations

from pathlib import Path

from sabermetrics.cedh.simulator import RESULT_SCHEMA_ID

ROOT = Path(__file__).resolve().parent.parent
CHARTER = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
SPEC = (ROOT / "RESEARCH_ASSISTANT_SPEC.md").read_text(encoding="utf-8")


def test_the_charter_names_the_result_schema_the_code_actually_pins():
    line = next(l for l in CHARTER.splitlines() if "cloud_alignment:" in l)
    assert RESULT_SCHEMA_ID in line, (
        f"CLAUDE.md cloud_alignment names a different schema than the code pins "
        f"({RESULT_SCHEMA_ID}). coverage.inert_by_reason only exists in v3, so a "
        f"stale line here tells a reader to design around its absence."
    )


def test_the_simulator_docstrings_name_the_schema_they_validate():
    source = (ROOT / "src/sabermetrics/cedh/simulator.py").read_text(encoding="utf-8")
    stale = [
        f"{i}: {line.strip()}"
        for i, line in enumerate(source.splitlines(), 1)
        # Only lines that CLAIM to validate. simulator.py:65 legitimately
        # mentions v1 as history ("previously also described"), and rewriting
        # history to match the present is its own kind of drift.
        if (
            "A validated ``cedh-simulation-result." in line
            or "Validate and adapt a v" in line
        )
        and RESULT_SCHEMA_ID.rsplit(".", 1)[-1] not in line
    ]
    assert (
        not stale
    ), "simulator.py names a schema version it does not validate:\n" + "\n".join(stale)


def test_the_charter_does_not_claim_desktop_only_while_shipping_a_breakpoint():
    css = (ROOT / "src/sabermetrics/ui/static/deck-lab.css").read_text(encoding="utf-8")
    if "@media (max-width: 767px)" not in css:
        return  # no mobile shipped; the charter line would be correct
    ui_scope = next(l for l in CHARTER.splitlines() if "ui_scope:" in l)
    assert "No mobile" not in ui_scope, (
        "deck-lab.css ships a mobile breakpoint but the charter still says "
        "'No mobile'. One of them is wrong, and the CSS is the one that runs."
    )


def test_the_adr_pointer_does_not_promise_prose_that_is_not_there():
    """CLAUDE.md must not claim design.md holds ADRs it does not hold."""
    import re

    design = (ROOT / "docs/project_plan/design.md").read_text(encoding="utf-8")
    present = {int(m) for m in re.findall(r"ADR-0(\d\d)", design)}
    # Only the "Full text for X..Y is in design.md" claim. Prose *about* the
    # gap (e.g. "ADR-015..027 have no prose") must not read as a promise.
    for claimed in re.findall(
        r"Full text for ADR-0(\d\d)\.\.(?:ADR-)?0?(\d\d) is in `design\.md`", CHARTER
    ):
        if True:
            lo, hi = int(claimed[0]), int(claimed[1])
            missing = sorted(n for n in range(lo, hi + 1) if n not in present)
            assert not missing, (
                f"CLAUDE.md points at design.md for ADR-{lo:03d}..{hi:03d} but it "
                f"has no prose for {[f'ADR-{n:03d}' for n in missing]}"
            )


def test_the_r0_definition_of_done_has_no_unchecked_boxes_when_r0_is_claimed_done():
    """The checklist is honest about what is not finished.

    This does not require R0 to be done. It requires the checklist to *say* so:
    unticked boxes are allowed, but the spec must then not also claim R0 is
    complete. The failure this prevents is a doc that reads finished while the
    gate script reports otherwise.
    """
    r0_definition = SPEC.split("#### R0 definition of done", 1)[1].split("### R1", 1)[0]
    unchecked = r0_definition.count("- [ ]")
    claims_done = "R0 is complete" in SPEC or "R0 complete" in SPEC
    assert not (
        unchecked and claims_done
    ), f"{unchecked} unticked R0 boxes while the spec claims R0 is complete"


def test_research_assistant_adrs_are_recorded_and_linked():
    decisions = (ROOT / "docs/research-assistant-adrs.md").read_text(encoding="utf-8")
    for number in (29, 30):
        marker = f"ADR-0{number}"
        assert marker in CHARTER
        assert f"## {marker}" in decisions
    assert "docs/research-assistant-adrs.md" in CHARTER
