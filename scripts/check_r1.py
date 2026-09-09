"""The R1 gate. Exit 0 means R1 is done; anything else means it is not.

    python scripts/check_r1.py            # the gate
    python scripts/check_r1.py --verbose  # show each command's output

Same shape and same reasoning as ``scripts/check_r0.py``: "R1 is done" is
otherwise a judgement, and a judgement cannot be handed off. R1's acceptance
(spec §9) is four claims, and each one below is a command with an exit code:

* **>= 25 tags across two complete families** — checked against the shipped
  registry, not against a file count.
* **Every tag at >= 0.95 precision** — recomputed from real oracle text in
  ``fixtures/mechanics/tag_cards.json``, and cross-checked against the figure
  each definition declares, so a declared number cannot drift from its
  measurement.
* **A coverage report is printed** — the untagged set grouped by type line, the
  direct analogue of the simulator's inert table.
* **The rebuild is deterministic and content-hashed** — built twice, the two
  content hashes must agree.

R0's gate is run first and in full. R1 sits on top of R0; a green R1 over a red
R0 would be measuring the wrong thing.
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

#: The suite floor after R0. Passed is a FLOOR; skipped is EXACT, because a test
#: that starts skipping is a test that stopped running.
BASELINE_PASSED = 1325
BASELINE_SKIPPED = 31

#: Spec §9, R1 acceptance.
MINIMUM_TAGS = 25

FIXTURES = "fixtures/mechanics/tag_cards.json"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(ROOT / "src"), env.get("PYTHONPATH", "")))
    )
    return env


def run(argv: list[str], verbose: bool) -> tuple[bool, str]:
    proc = subprocess.run(
        argv, cwd=ROOT, capture_output=True, text=True, check=False, env=_env()
    )
    out = proc.stdout + proc.stderr
    if verbose:
        print(out)
    return proc.returncode == 0, out


def _pytest(*args: str) -> list[str]:
    return [PY, "-m", "pytest", "-q", *args]


def _cli(*args: str) -> list[str]:
    """Invoke the click CLI through its entry point.

    ``python -m sabermetrics.main`` cannot be used: ``main.py`` has an
    ``if __name__ == "__main__": cli()`` block partway down the file, so running
    it as a module dispatches before the later command groups are registered.
    That is a pre-existing quirk of the module, not something R1 introduced.
    """
    body = "from sabermetrics.main import cli; cli()"
    return [PY, "-c", body, *args]


class Check:
    def __init__(self, name: str, argv: list[str], *, note: str = "") -> None:
        self.name = name
        self.argv = argv
        self.note = note

    def run(self, verbose: bool) -> tuple[bool, str]:
        return run(self.argv, verbose)


CHECKS: list[Check] = [
    Check("lint (ruff)", [PY, "-m", "ruff", "check", "src", "tests"]),
    Check("format (black)", [PY, "-m", "black", "--check", "src", "tests"]),
    Check("types (mypy)", [PY, "-m", "mypy", "src"]),
    Check("package boundaries", _pytest("tests/test_package_boundaries.py")),
    Check("mechanic tag suite", _pytest("tests/test_mechanic_tags.py")),
    Check(
        "every tag measured at >= 0.95 on real oracle text",
        _cli("tags", "verify", "--fixtures", FIXTURES),
        note="`tags verify` recomputes precision and fails on declared-vs-measured drift",
    ),
    Check(
        "coverage report prints, grouped by type line",
        _cli("tags", "coverage", "--snapshot", FIXTURES),
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
            "sabermetrics.analytics.role_tagger,"
            "sabermetrics.pipeline.deck_builder,"
            "sabermetrics.reference_layer.evidence;"
            "from sabermetrics.ui.app import create_app",
        ],
    ),
]


def check_library(verbose: bool) -> tuple[bool, str]:
    """Tag count and family completeness, read from the registry."""
    ok, out = run(
        [
            PY,
            "-c",
            "from sabermetrics.mechanics.tags.registry import ALL_TAGS, "
            "SHIPPED_FAMILIES, by_family;"
            "print(len(ALL_TAGS));"
            "print(','.join(f'{f}={len(by_family(f))}' for f in SHIPPED_FAMILIES))",
        ],
        verbose,
    )
    if not ok:
        return False, "the tag registry does not import"
    lines = out.strip().splitlines()
    total = int(lines[0])
    families = {
        part.split("=")[0]: int(part.split("=")[1]) for part in lines[1].split(",")
    }
    if total < MINIMUM_TAGS:
        return False, f"{total} tags, below the {MINIMUM_TAGS} acceptance floor"
    empty = sorted(name for name, count in families.items() if count == 0)
    if empty:
        return False, f"shipped families with no tags: {', '.join(empty)}"
    detail = ", ".join(f"{name} {count}" for name, count in sorted(families.items()))
    return True, f"{total} tags ({detail})"


def check_determinism(verbose: bool) -> tuple[bool, str]:
    """Two dry-run builds of the same library and snapshot must agree."""
    hashes = []
    for _ in range(2):
        ok, out = run(
            _cli("tags", "build", "--snapshot", FIXTURES, "--dry-run"), verbose
        )
        if not ok:
            return False, "the build did not complete"
        match = re.search(r"content\s+([0-9a-f]{64})", out)
        if not match:
            return False, "the build printed no content hash"
        hashes.append(match.group(1))
    if hashes[0] != hashes[1]:
        return False, f"content hash differs between builds: {hashes}"
    return True, f"content {hashes[0][:12]} on both builds"


def check_suite(verbose: bool) -> tuple[bool, str]:
    ok, out = run(_pytest(), verbose)
    if not ok:
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
    parser.add_argument(
        "--skip-r0", action="store_true", help="do not re-run the R0 gate first"
    )
    args = parser.parse_args()

    failures: list[str] = []

    if not args.skip_r0:
        ok, _ = run([PY, str(ROOT / "scripts" / "check_r0.py")], args.verbose)
        print(f"[{'PASS' if ok else 'FAIL'}] R0 gate still green")
        if not ok:
            failures.append("R0 gate")

    for check in CHECKS:
        ok, _ = check.run(args.verbose)
        suffix = f"   ({check.note})" if check.note and not ok else ""
        print(f"[{'PASS' if ok else 'FAIL'}] {check.name}{suffix}")
        if not ok:
            failures.append(check.name)

    for name, fn in (
        (">= 25 tags across two complete families", check_library),
        ("rebuild is deterministic and content-hashed", check_determinism),
    ):
        ok, detail = fn(args.verbose)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")
        if not ok:
            failures.append(name)

    ok, detail = check_suite(args.verbose)
    print(f"[{'PASS' if ok else 'FAIL'}] full suite — {detail}")
    if not ok:
        failures.append("full suite")

    print()
    if failures:
        print(f"R1 NOT DONE — {len(failures)} failing: {', '.join(failures)}")
        return 1
    print("R1 DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
