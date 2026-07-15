"""Diagnose land scoring for Krenko: compute mana_score, CVAR, combined."""

import json
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

from sabermetrics.analytics.cvar import ScoringContext, compute_cvar
from sabermetrics.models.deck import CVARWeights
from sabermetrics.pipeline.mana_base import (
    _score_land,
    compute_color_targets,
    parse_land_colors,
)


DB = ROOT / "data" / "sabermetrics.db"
KRENKO_ID = "0b9c68ff-1fe4-42ef-8d1f-43120de5c1ff"

SUSPECT_LANDS = [
    "Branch of Vitu-Ghazi",
    "Daily Bugle Building",
    "Guildmages' Forum",
    "Hall of Oracles",
    "Mirrex",
    "Springjack Pasture",
    "The Grey Havens",
    "Naya Panorama",
    "Riveteers Overlook",
    "Maestros Theater",
]

REFERENCE_LANDS = [
    "Mountain",  # the staple
    "Snow-Covered Mountain",
    "Castle Embereth",  # 38% EDHREC for Krenko
    "Arena of Glory",  # 23% EDHREC for Krenko
    "Sokenzan, Crucible of Defiance",
    "Sulfurous Springs",  # off-color BR (irrelevant)
    "Shivan Reef",  # off-color UR (irrelevant)
    "Cavern of Souls",  # any color, but premium and tribal
]


def _load_card(conn: sqlite3.Connection, name: str) -> dict | None:
    row = conn.execute(
        "SELECT id, name, oracle_text, type_line, cmc, color_identity, "
        "       mana_cost, keywords, role_tags, functional_categories, rarity "
        "FROM cards WHERE name = ? LIMIT 1",
        (name,),
    ).fetchone()
    if not row:
        return None
    card = {
        "id": row[0], "name": row[1], "oracle_text": row[2],
        "type_line": row[3], "cmc": row[4], "color_identity": row[5],
        "mana_cost": row[6], "keywords": row[7], "role_tags": row[8],
        "functional_categories": row[9], "rarity": row[10],
    }
    price_row = conn.execute(
        "SELECT price_usd FROM card_prices WHERE card_id = ? "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (row[0],),
    ).fetchone()
    card["price_usd"] = price_row[0] if price_row else 0.0
    return card


def main() -> None:
    conn = sqlite3.connect(str(DB))
    krenko = conn.execute(
        "SELECT name, oracle_text, color_identity, keywords FROM cards "
        "WHERE id = ?", (KRENKO_ID,),
    ).fetchone()
    krenko_oracle = krenko[1] or ""
    krenko_colors = json.loads(krenko[2])
    krenko_keywords = json.loads(krenko[3] or "[]")

    # Build EDHREC top-card map for Krenko
    edhrec_row = conn.execute(
        "SELECT top_cards FROM edhrec_commander_data WHERE commander_id = ?",
        (KRENKO_ID,),
    ).fetchone()
    edhrec_top: dict[str, float] = {}
    if edhrec_row and edhrec_row[0]:
        for entry in json.loads(edhrec_row[0]):
            name_l = (entry.get("card_name") or "").lower()
            pct = float(entry.get("inclusion_pct", 0))
            if name_l:
                edhrec_top[name_l] = pct

    # Build context
    weights = CVARWeights()
    ctx = ScoringContext(
        commander_id=KRENKO_ID,
        commander_name="Krenko, Mob Boss",
        commander_colors=krenko_colors,
        commander_keywords=krenko_keywords,
        commander_oracle_text=krenko_oracle,
        referenced_keywords=[],
        referenced_mechanics=[],
        engine_keywords=[],
        output_keywords=[],
        edhrec_top_cards=edhrec_top,
        desired_card_traits=[],
        weights_synergy=weights.synergy,
        weights_mana_efficiency=weights.mana_efficiency,
        weights_replacement_value=weights.replacement_value,
        weights_price_efficiency=weights.price_efficiency,
        max_budget=200.0,
    )

    # Simulated post-spells, pre-lands state for Krenko:
    # 36 lands, mono-R, target ~14 R sources (Karsten hardest cast).
    # Use a fake spells list with mostly R pips at cmc 3 for realism.
    fake_spells = [
        {"mana_cost": "{R}{R}", "cmc": 2, "type_line": "Creature"}
    ] * 30
    color_targets = compute_color_targets(fake_spells, ["R"], 36)
    print(f"Krenko color_targets (Karsten): {color_targets}\n")
    color_deficit = {c: float(t) for c, t in color_targets.items()}

    print(f"{'Card':<35} {'EDHREC%':>8} {'r_cvar':>8} {'mana_s':>8} "
          f"{'combined':>10} {'colors_prod':<20} {'flags'}")
    print("-" * 120)

    rows: list[tuple] = []
    for label, names in [("SUSPECT", SUSPECT_LANDS), ("REFERENCE", REFERENCE_LANDS)]:
        print(f"\n== {label} ==")
        for name in names:
            card = _load_card(conn, name)
            if not card:
                print(f"  {name}: NOT FOUND")
                continue
            # Compute CVAR
            card["edhrec_inclusion_pct"] = edhrec_top.get(name.lower(), 0.0)
            cvar_result = compute_cvar(card, ctx, DB)
            raw_cvar = cvar_result.composite_score

            # Parse land
            info = parse_land_colors(
                oracle_text=card["oracle_text"] or "",
                type_line=card["type_line"] or "",
                commander_colors=["R"],
            )
            info.card = card
            mana_s = _score_land(info, color_deficit, ["R"], avg_cmc=3.0)
            combined = 0.7 * mana_s + 0.3 * raw_cvar * 10

            flags = []
            if info.produces_any_color:
                flags.append("ANY_COLOR")
            if info.is_fetch:
                flags.append(f"FETCH→{','.join(info.fetch_targets)}")
            if info.is_basic:
                flags.append("BASIC")
            if info.enters_tapped:
                flags.append("TAPPED")
            elif info.is_conditional_tapped:
                flags.append("COND_TAPPED")
            if info.has_mana_restriction:
                flags.append("RESTRICTED")

            print(f"  {name:<33} {card['edhrec_inclusion_pct']:>7.1f}% "
                  f"{raw_cvar:>8.3f} {mana_s:>8.3f} {combined:>10.3f} "
                  f"{','.join(info.colors_produced):<20} {','.join(flags)}")
            rows.append((name, raw_cvar, mana_s, combined))

    conn.close()


if __name__ == "__main__":
    main()
