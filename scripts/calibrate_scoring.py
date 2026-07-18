"""Calibrate the CVAR scorer against real decklists (Option A criterion 6).

For a sample of commanders that have real tracked decks, score the full legal
candidate pool with the deterministic CVAR scorer and measure the mean
percentile rank it assigns to cards that actually appear in real decks. A random
scorer would average ~0.50; a good scorer ranks real-deck cards well above that.

DoD target: mean percentile >= 0.70.

Usage:
    python scripts/calibrate_scoring.py [--n 40] [--db data/sabermetrics.db]
"""

import argparse
import bisect
import json
import sqlite3
from pathlib import Path

from sabermetrics.analytics.card_win_equity import load_cwe_for_commander
from sabermetrics.analytics.cvar import ScoringContext, compute_cvar
from sabermetrics.analytics.filters import apply_hard_filters
from sabermetrics.analytics.oracle_keywords import (
    extract_referenced_keywords,
    extract_referenced_mechanics,
)
from sabermetrics.models.deck import CVARWeights

_BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}


def _commander_context(
    conn: sqlite3.Connection, commander_id: str, db_path: Path
) -> ScoringContext | None:
    row = conn.execute(
        "SELECT id, name, color_identity, keywords, oracle_text "
        "FROM cards WHERE id = ? AND is_legal_commander = 1",
        (commander_id,),
    ).fetchone()
    if row is None:
        return None
    colors = json.loads(row[2] or "[]")
    keywords = json.loads(row[3] or "[]")
    oracle = row[4] or ""

    edhrec: dict[str, float] = {}
    er = conn.execute(
        "SELECT top_cards FROM edhrec_commander_data WHERE commander_id = ?",
        (commander_id,),
    ).fetchone()
    if er and er[0]:
        for entry in json.loads(er[0]):
            name = (entry.get("card_name") or "").lower()
            pct = float(entry.get("inclusion_pct", 0) or 0)
            if name and pct > 0:
                edhrec[name] = pct

    cwe_by_card, cwe_sample_by_card = load_cwe_for_commander(db_path, commander_id)

    w = CVARWeights()
    return ScoringContext(
        commander_id=row[0],
        commander_name=row[1],
        commander_colors=colors,
        commander_keywords=keywords,
        commander_oracle_text=oracle,
        referenced_keywords=extract_referenced_keywords(oracle),
        referenced_mechanics=extract_referenced_mechanics(oracle),
        edhrec_top_cards=edhrec,
        cwe_by_card=cwe_by_card,
        cwe_sample_by_card=cwe_sample_by_card,
        weights_synergy=w.synergy,
        weights_mana_efficiency=w.mana_efficiency,
        weights_replacement_value=w.replacement_value,
        weights_price_efficiency=w.price_efficiency,
    )


def _real_deck_card_names(conn: sqlite3.Connection, commander_id: str) -> set[str]:
    # Ground truth excludes TopDeck tournament decks so calibration stays a
    # held-out test independent of the CWE scoring signal (which is derived from
    # those same tournament decks) — otherwise the check would be circular.
    rows = conn.execute(
        "SELECT DISTINCT lower(c.name) FROM deck_cards dk "
        "JOIN decks d ON d.id = dk.deck_id "
        "JOIN cards c ON c.id = dk.card_id "
        "WHERE d.commander_id = ? AND dk.is_commander = 0 "
        "AND d.source != 'topdeck'",
        (commander_id,),
    ).fetchall()
    return {r[0] for r in rows if r[0] and r[0] not in {b.lower() for b in _BASICS}}


def calibrate(db_path: Path, n_commanders: int = 40, seed: int = 42) -> dict:
    """Return calibration stats: pooled mean percentile of real-deck cards."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    commanders = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT d.commander_id FROM decks d "
            "JOIN cards c ON c.id = d.commander_id AND c.is_legal_commander = 1 "
            "WHERE d.source != 'topdeck' "  # held-out: curated decks only
            "ORDER BY d.commander_id"
        ).fetchall()
    ]
    # Deterministic sample.
    import random
    random.Random(seed).shuffle(commanders)
    commanders = commanders[:n_commanders]

    all_percentiles: list[float] = []
    per_commander: list[float] = []
    coverage: list[float] = []

    for cid in commanders:
        ctx = _commander_context(conn, cid, db_path)
        if ctx is None:
            continue
        candidates = apply_hard_filters(db_path, cid, max_budget_usd=None)
        if len(candidates) < 50:
            continue
        scores: dict[str, float] = {}
        for card in candidates:
            name = (card.get("name") or "").lower()
            if not name:
                continue
            scores[name] = compute_cvar(card, ctx, db_path).composite_score
        ordered = sorted(scores.values())
        denom = max(1, len(ordered) - 1)

        real_all = _real_deck_card_names(conn, cid)
        real = real_all & set(scores)
        if not real:
            continue
        pcts = [bisect.bisect_left(ordered, scores[nm]) / denom for nm in real]
        all_percentiles.extend(pcts)
        per_commander.append(sum(pcts) / len(pcts))
        coverage.append(len(real) / max(1, len(real_all)))

    conn.close()
    mean_pct = sum(all_percentiles) / len(all_percentiles) if all_percentiles else 0.0
    return {
        "mean_percentile": round(mean_pct, 4),
        "per_commander_mean": round(
            sum(per_commander) / len(per_commander), 4
        ) if per_commander else 0.0,
        "commanders_scored": len(per_commander),
        "real_cards_evaluated": len(all_percentiles),
        "mean_pool_coverage": round(sum(coverage) / len(coverage), 3) if coverage else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--db", default="data/sabermetrics.db")
    args = ap.parse_args()
    stats = calibrate(Path(args.db), n_commanders=args.n)
    print(json.dumps(stats, indent=2))
    target = 0.70
    verdict = "PASS" if stats["mean_percentile"] >= target else "BELOW TARGET"
    print(f"\nmean_percentile={stats['mean_percentile']} target={target} -> {verdict}")


if __name__ == "__main__":
    main()
