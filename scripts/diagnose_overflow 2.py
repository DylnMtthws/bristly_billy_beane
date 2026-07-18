"""Diagnose where card count overflows 99 in the deck builder pipeline."""

import logging
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Load .env manually
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Set up verbose logging to capture counts at each stage
logging.basicConfig(
    level=logging.INFO,
    format="%(name)s | %(message)s",
)

from sabermetrics.pipeline.deck_builder import DeckBuilder, DeckBuildRequest


def trace_build(commander_name: str, db_path: Path) -> None:
    """Run a build with monkey-patched stage hooks that print counts."""
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT id, name FROM cards "
        "WHERE name LIKE ? AND is_legal_commander = 1 LIMIT 1",
        (f"%{commander_name}%",),
    ).fetchone()
    conn.close()
    if not row:
        print(f"NOT FOUND: {commander_name}")
        return
    commander_id, full_name = row
    print(f"\n{'=' * 70}\nTRACING: {full_name}\n{'=' * 70}")

    builder = DeckBuilder(db_path)

    # Monkey-patch the infrastructure fill to print stage counts
    orig_fill = builder._fill_infrastructure

    def traced_fill(candidates, commander, request, template):
        print(f"\n--- TEMPLATE ---")
        print(f"  land_count            = {template.land_count}")
        print(f"  ramp_count            = {template.ramp_count}")
        print(f"  draw_count            = {template.draw_count}")
        print(f"  removal_count         = {template.removal_count}")
        print(f"  board_wipe_count      = {template.board_wipe_count}")
        print(f"  differentiator_slots  = {template.differentiator_slots}")
        prot = min(4, max(2, template.differentiator_slots // 10))
        print(f"  protection_target     = {prot}")
        expected = (
            template.land_count + template.ramp_count + template.draw_count
            + template.removal_count + template.board_wipe_count
            + template.differentiator_slots
        )
        print(f"  EXPECTED TOTAL        = {expected}")

        result, used = orig_fill(candidates, commander, request, template)
        by_role: dict[str, int] = {}
        for a in result:
            by_role[a.slot_role] = by_role.get(a.slot_role, 0) + 1
        print(f"\n--- INFRASTRUCTURE PLACED (total {len(result)}) ---")
        for role, n in sorted(by_role.items()):
            print(f"  {role:12s} = {n}")
        return result, used

    builder._fill_infrastructure = traced_fill

    # Monkey-patch _optimize_differentiators to print final counts
    orig_opt = builder._optimize_differentiators

    def traced_opt(candidates, infrastructure, profile_result, commander,
                   request, template, budget_used):
        print(f"\n--- ENTERING OPTIMIZER with {len(infrastructure)} cards ---")
        protection_placed = sum(
            1 for a in infrastructure if a.slot_role == "protection"
        )
        diff_slots = max(0, template.differentiator_slots - protection_placed)
        print(f"  protection_placed = {protection_placed}")
        print(f"  diff_slots passed to greedy_fill = {diff_slots}")

        result, metrics = orig_opt(
            candidates, infrastructure, profile_result, commander,
            request, template, budget_used,
        )
        by_role: dict[str, int] = {}
        for a in result:
            by_role[a.slot_role] = by_role.get(a.slot_role, 0) + 1
        print(f"\n--- AFTER OPTIMIZER (total {len(result)}) ---")
        for role, n in sorted(by_role.items()):
            print(f"  {role:12s} = {n}")
        return result, metrics

    builder._optimize_differentiators = traced_opt

    # Patch _redistribute_budget too
    orig_redist = builder._redistribute_budget

    def traced_redist(deck, budget, candidates, protected_names=None):
        result = orig_redist(deck, budget, candidates, protected_names=protected_names)
        print(f"\n--- AFTER BUDGET REDISTRIBUTION (total {len(result)}) ---")
        return result

    builder._redistribute_budget = traced_redist

    request = DeckBuildRequest(
        commander_id=commander_id,
        budget_usd=200.0,
        power_target=3,
    )
    try:
        result = builder.build(request)
        print(f"\n=== FINAL DECK: {len(result.deck.cards)} cards ===")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"BUILD FAILED: {e}")


if __name__ == "__main__":
    db_path = ROOT / "data" / "sabermetrics.db"
    target = sys.argv[1] if len(sys.argv) > 1 else "Atraxa, Praetors' Voice"
    trace_build(target, db_path)
