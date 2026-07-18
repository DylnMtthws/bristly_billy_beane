"""Quick 5-commander validation: print final card counts only."""

import logging
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

logging.basicConfig(level=logging.WARNING, format="%(name)s | %(message)s")
logging.getLogger("sabermetrics.pipeline.generators.removal").setLevel(logging.INFO)
logging.getLogger("sabermetrics.pipeline.deck_builder").setLevel(logging.INFO)

from sabermetrics.pipeline.deck_builder import DeckBuilder, DeckBuildRequest


COMMANDERS = [
    ("Atraxa, Praetors' Voice", None),
    ("Lathril, Blade of the Elves", "Elf"),
    ("Eriette of the Charmed Apple", None),
    ("Krenko, Mob Boss", "Goblin"),
    ("Arcades, the Strategist", "Wall"),
]

UNIVERSAL_JUNK_LANDS = {
    "Branch of Vitu-Ghazi", "Daily Bugle Building", "Guildmages' Forum",
    "Hall of Oracles", "Mirrex", "Springjack Pasture", "The Grey Havens",
}


def main() -> None:
    db_path = ROOT / "data" / "sabermetrics.db"
    results: list[dict] = []

    for cmdr, tribe in COMMANDERS:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT id, name FROM cards "
            "WHERE name LIKE ? AND is_legal_commander = 1 LIMIT 1",
            (f"%{cmdr}%",),
        ).fetchone()
        conn.close()
        if not row:
            print(f"NOT FOUND: {cmdr}")
            continue
        commander_id, full_name = row
        print(f"\n=== Building: {full_name} ===")

        builder = DeckBuilder(db_path)
        request = DeckBuildRequest(
            commander_id=commander_id,
            budget_usd=200.0,
            power_target=3,
        )
        try:
            result = builder.build(request)
            cards = result.deck.cards
            n_cards = len(cards)
            land_cards = [c for c in cards if c.slot_role == "land"]
            nonbasic_lands = [
                c for c in land_cards
                if "basic" not in (c.card.type_line or "").lower()
            ]
            basic_lands = [
                c for c in land_cards
                if "basic" in (c.card.type_line or "").lower()
            ]
            junk_present = sorted(
                {c.card.name for c in land_cards if c.card.name in UNIVERSAL_JUNK_LANDS}
            )

            tribe_count = 0
            if tribe:
                tribe_count = sum(
                    1 for c in cards
                    if tribe.lower() in (c.card.type_line or "").lower()
                )

            top_lands = sorted(
                land_cards, key=lambda c: -c.cvar_score
            )[:8]
            top_lands_names = [c.card.name for c in top_lands]

            res = {
                "name": full_name,
                "n_cards": n_cards,
                "n_nonbasic": len(nonbasic_lands),
                "n_basic": len(basic_lands),
                "junk": junk_present,
                "tribe": tribe,
                "tribe_count": tribe_count,
                "top_lands": top_lands_names,
            }
            results.append(res)
            print(f"  → {n_cards} cards | "
                  f"lands={len(land_cards)} ({len(nonbasic_lands)} nonbasic + {len(basic_lands)} basic)")
            print(f"     junk lands present: {junk_present or 'NONE'}")
            if tribe:
                print(f"     {tribe} count: {tribe_count}")
            print(f"     top lands by score: {', '.join(top_lands_names)}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  → FAILED: {e}")
            results.append({"name": full_name, "n_cards": -1})

    print("\n" + "=" * 80)
    print(f"{'Commander':<32s} {'Cards':>6s} {'Lands':>6s} {'Junk':>5s} {'Tribe':>10s}")
    print("-" * 80)
    for r in results:
        if r.get("n_cards", -1) < 0:
            print(f"{r['name']:<32s} FAILED")
            continue
        tribe_str = f"{r['tribe_count']} {r['tribe']}" if r.get("tribe") else "—"
        print(f"{r['name']:<32s} {r['n_cards']:>6d} "
              f"{r['n_nonbasic']}+{r['n_basic']:<3d} "
              f"{len(r['junk']):>5d} {tribe_str:>10s}")
    print("\nJunk lands still present:")
    for r in results:
        if r.get("junk"):
            print(f"  {r['name']}: {', '.join(r['junk'])}")


if __name__ == "__main__":
    main()
