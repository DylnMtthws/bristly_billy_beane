"""The R0 gate. Exit 0 means R0 is done; anything else means it is not.

    python scripts/check_r0.py            # the gate
    python scripts/check_r0.py --verbose  # show each command's output

Run from the repository root. Uses the interpreter that runs this script, so
invoke it with the venv's python.

Why a script rather than a paragraph: "R0 is done" is otherwise a judgement, and
a judgement cannot be handed off. Every check below is a command with an exit
code, and the two that are *not* checkable this way are listed at the end as
stated remainders rather than quietly omitted.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

#: The pre-R0 baseline, measured on a clean tree before any R0 change.
#: Passed is a FLOOR (adding tests is progress). Skipped is EXACT: a test that
#: starts skipping is a test that stopped running, which is what a count is for.
BASELINE_PASSED = 1288
BASELINE_SKIPPED = 31


class Check:
    def __init__(self, name: str, argv: list[str], *, note: str = "") -> None:
        self.name = name
        self.argv = argv
        self.note = note

    def run(self, verbose: bool) -> tuple[bool, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, (str(ROOT / "src"), env.get("PYTHONPATH", "")))
        )
        proc = subprocess.run(
            self.argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        out = proc.stdout + proc.stderr
        if verbose:
            print(out)
        return proc.returncode == 0, out


def _pytest(*args: str) -> list[str]:
    return [PY, "-m", "pytest", "-q", *args]


CHECKS: list[Check] = [
    Check("lint (ruff)", [PY, "-m", "ruff", "check", "src", "tests"]),
    Check("format (black)", [PY, "-m", "black", "--check", "src", "tests"]),
    Check("types (mypy)", [PY, "-m", "mypy", "src"]),
    Check(
        "D0a: absence of a model is visible",
        _pytest("tests/test_cedh_lab.py", "-k", "AbsenceOfAModel"),
        note="reverting the fix must make these fail",
    ),
    Check(
        "D0a: the candidate page states it",
        _pytest("tests/test_cedh_ui.py", "-k", "unexplained or stated_at_build"),
    ),
    Check("package boundaries", _pytest("tests/test_package_boundaries.py")),
    Check(
        "charter lines bind to code",
        _pytest("tests/test_research_assistant_charter.py"),
    ),
    Check("golden question set and eval rigs", _pytest("tests/test_assistant_eval.py")),
    Check(
        "substrate scorecard zero state",
        [PY, "-m", "sabermetrics.assistant.eval", "--mode", "substrate"],
    ),
    Check(
        "usefulness scorecard zero state",
        [PY, "-m", "sabermetrics.assistant.eval", "--mode", "usefulness"],
    ),
    Check(
        "legacy imports unbroken",
        [
            PY,
            "-c",
            "import sabermetrics.analytics.effective_cost,"
            "sabermetrics.analytics.oracle_patterns,"
            "sabermetrics.analytics.theme_patterns,"
            "sabermetrics.analytics.oracle_keywords,"
            "sabermetrics.analytics.keyword_scoring,"
            "sabermetrics.analytics.cvar,"
            "sabermetrics.analytics.components,"
            "sabermetrics.analytics.role_tagger,"
            "sabermetrics.pipeline.deck_builder,"
            "sabermetrics.pipeline.greedy_optimizer,"
            "sabermetrics.reference_layer.evidence;"
            "from sabermetrics.ui.app import create_app",
        ],
        note="every pre-existing consumer path still imports",
    ),
]


def check_suite(verbose: bool) -> tuple[bool, str]:
    """Full suite, asserting the floor on passed and equality on skipped."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(ROOT / "src"), env.get("PYTHONPATH", "")))
    )
    proc = subprocess.run(
        _pytest(), cwd=ROOT, capture_output=True, text=True, check=False, env=env
    )
    out = proc.stdout + proc.stderr
    if verbose:
        print(out)
    if proc.returncode != 0:
        return False, "suite is red"
    match = re.search(r"(\d+) passed(?:, (\d+) skipped)?", out)
    if not match:
        return False, "could not parse the pytest summary"
    passed = int(match.group(1))
    skipped = int(match.group(2) or 0)
    if passed < BASELINE_PASSED:
        return False, f"{passed} passed, below the {BASELINE_PASSED} baseline"
    if skipped != BASELINE_SKIPPED:
        return False, f"{skipped} skipped, expected exactly {BASELINE_SKIPPED}"
    return True, f"{passed} passed (>= {BASELINE_PASSED}), {skipped} skipped"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    failures: list[str] = []
    for check in CHECKS:
        ok, _ = check.run(args.verbose)
        flag = "PASS" if ok else "FAIL"
        suffix = f"   ({check.note})" if check.note and not ok else ""
        print(f"[{flag}] {check.name}{suffix}")
        if not ok:
            failures.append(check.name)

    ok, detail = check_suite(args.verbose)
    print(f"[{'PASS' if ok else 'FAIL'}] full suite — {detail}")
    if not ok:
        failures.append("full suite")

    print()
    if failures:
        print(f"R0 NOT DONE — {len(failures)} failing: {', '.join(failures)}")
        return 1
    print("R0 DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
