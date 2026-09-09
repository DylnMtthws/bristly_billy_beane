"""D1 corpus-depth census — read-only, against the live ``mtg_v1`` contract.

Run from the repository root with a consumer DSN::

    MTG_V1_DSN='postgresql://...?sslmode=require' python scripts/d1_census.py
    MTG_V1_DSN='...' python scripts/d1_census.py --json census.json

**This script produces no numbers without a live database.** It is checked in so
that the census is a *reproducible measurement* rather than a one-off paste into
a document: whoever has the DSN runs it, commits the JSON beside
``docs/d1-corpus-census.md``, and the thresholds in ``config/`` are set from the
result. Re-running it later shows corpus drift for free.

Scope and method are documented in ``docs/d1-corpus-census.md``. The short
version, because it changes what the numbers mean:

* Every query reads ``mtg_v1`` only, as ``mtg_consumer``, and is checked by
  :func:`~sabermetrics.cedh.repositories.assert_v1_only` before execution.
* One ``REPEATABLE READ READ ONLY`` transaction wraps the whole census, so every
  figure describes the same snapshot. Counting tournaments and decks in separate
  transactions during a nightly import produces a ratio that never existed.
* Commander populations are grouped by ``deck.commander_identity``, which is the
  sorted-oracle_id key, so a partner pair is one identity rather than two.
* Identities containing an ``unresolved:`` part are counted and reported
  **separately**, never merged into the totals. They are decks whose commander
  the pipeline declined to guess at, and folding them in would inflate the
  identity count with names rather than commanders.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sabermetrics.cedh.repositories import assert_v1_only

#: Deck counts at or above which a commander population is treated as usable.
#: These are the thresholds the census exists to inform; they are *reported
#: against*, not asserted, because the whole point is to learn the real shape.
DECK_FLOORS = (10, 30, 100)

CENSUS_QUERIES: dict[str, str] = {
    # -- Event diversity. Archetype clustering needs events, not just decks. --
    "tournaments": """
        SELECT count(*)                    AS tournaments,
               count(DISTINCT source)      AS sources,
               min(event_date)             AS earliest,
               max(event_date)             AS latest,
               count(*) FILTER (WHERE player_count IS NULL) AS null_player_count,
               percentile_disc(0.5) WITHIN GROUP (ORDER BY player_count)
                                           AS median_player_count,
               min(player_count)           AS min_player_count,
               max(player_count)           AS max_player_count
        FROM mtg_v1.tournament
    """,
    "tournaments_by_source": """
        SELECT source,
               count(*)        AS tournaments,
               min(event_date) AS earliest,
               max(event_date) AS latest
        FROM mtg_v1.tournament
        GROUP BY source
        ORDER BY tournaments DESC
    """,
    # -- The inclusion denominator. An entry with a null deck_id is a standing
    # -- with no list behind it and can never contribute to an inclusion rate.
    "entries": """
        SELECT count(*)                                      AS entries,
               count(*) FILTER (WHERE deck_id IS NOT NULL)   AS entries_with_deck,
               count(DISTINCT deck_id)                       AS distinct_decks
        FROM mtg_v1.tournament_entry
    """,
    # -- Commander populations, resolved identities only. --
    "commander_depth": """
        WITH per_identity AS (
            SELECT d.commander_identity                AS identity,
                   count(DISTINCT d.deck_id)           AS decks,
                   count(DISTINCT e.tournament_id)     AS events
            FROM mtg_v1.deck AS d
            JOIN mtg_v1.tournament_entry AS e ON e.deck_id = d.deck_id
            WHERE d.commander_identity NOT LIKE '%%unresolved:%%'
            GROUP BY d.commander_identity
        )
        SELECT count(*)                                        AS identities,
               count(*) FILTER (WHERE decks >= 10)             AS identities_ge_10,
               count(*) FILTER (WHERE decks >= 30)             AS identities_ge_30,
               count(*) FILTER (WHERE decks >= 100)            AS identities_ge_100,
               count(*) FILTER (WHERE decks >= 30 AND events >= 5)
                                                               AS identities_ge_30_and_5_events,
               max(decks)                                      AS deepest_identity_decks
        FROM per_identity
    """,
    "top_identities": """
        SELECT d.commander_identity AS identity,
               string_agg(DISTINCT dc.submitted_name, ' / '
                          ORDER BY dc.submitted_name)          AS commanders,
               count(DISTINCT d.deck_id)                       AS decks,
               count(DISTINCT e.tournament_id)                 AS events,
               count(DISTINCT d.deck_id) FILTER (WHERE d.is_complete) AS complete_decks
        FROM mtg_v1.deck AS d
        JOIN mtg_v1.deck_commander AS dc ON dc.deck_id = d.deck_id
        JOIN mtg_v1.tournament_entry AS e ON e.deck_id = d.deck_id
        WHERE d.commander_identity NOT LIKE '%%unresolved:%%'
        GROUP BY d.commander_identity
        ORDER BY decks DESC, events DESC
        LIMIT 40
    """,
    # -- Reported separately, never folded into the totals above. --
    "unresolved_identities": """
        SELECT count(DISTINCT d.commander_identity) AS identities,
               count(DISTINCT d.deck_id)            AS decks
        FROM mtg_v1.deck AS d
        WHERE d.commander_identity LIKE '%%unresolved:%%'
    """,
    # -- The D6 basic-land bias, quantified for THIS corpus rather than in
    # -- general. Colour identity comes from the commander card, so a partner
    # -- pair contributes the union across its rows.
    "completeness_by_colour_count": """
        WITH deck_colours AS (
            SELECT d.deck_id,
                   d.is_complete,
                   count(DISTINCT colour) AS colours
            FROM mtg_v1.deck AS d
            JOIN mtg_v1.deck_commander AS dc ON dc.deck_id = d.deck_id
            JOIN mtg_v1.card_any_medium AS c ON c.oracle_id = dc.oracle_id
            LEFT JOIN LATERAL unnest(c.color_identity) AS colour ON TRUE
            GROUP BY d.deck_id, d.is_complete
        )
        SELECT colours,
               count(*)                                  AS decks,
               count(*) FILTER (WHERE is_complete)       AS complete,
               round(100.0 * count(*) FILTER (WHERE is_complete)
                     / nullif(count(*), 0), 1)           AS pct_complete
        FROM deck_colours
        GROUP BY colours
        ORDER BY colours
    """,
    # -- Corpus size, so the census also re-checks the verifier floors. --
    "card_corpus": """
        SELECT (SELECT count(*) FROM mtg_v1.card_any_medium) AS card_any_medium,
               (SELECT count(*) FROM mtg_v1.card_legality)   AS card_legality
    """,
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def run_census(dsn: str) -> dict[str, Any]:
    """Execute every census query in one read-only repeatable-read snapshot.

    Args:
        dsn: A ``mtg_consumer`` DSN for the database publishing ``mtg_v1``.

    Returns:
        Mapping of query name to a list of row dicts.

    Raises:
        SchemaBoundaryViolation: If any query names a non-public schema.
    """
    import psycopg
    from psycopg.rows import dict_row

    for sql in CENSUS_QUERIES.values():
        assert_v1_only(sql)

    out: dict[str, Any] = {}
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        role = conn.execute("SELECT current_user AS who").fetchone()
        out["_role"] = role["who"] if role else None
        if out["_role"] != "mtg_consumer":
            print(
                f"WARNING: connected as {out['_role']!r}, not 'mtg_consumer'. "
                "The census is read-only regardless, but the permission "
                "surface being measured is not the production one.",
                file=sys.stderr,
            )
        for name, sql in CENSUS_QUERIES.items():
            out[name] = [dict(r) for r in conn.execute(sql).fetchall()]
    return out


def _render(results: dict[str, Any]) -> None:
    for name, rows in results.items():
        if name.startswith("_"):
            continue
        print(f"\n=== {name} ===")
        if not rows:
            print("  (no rows)")
            continue
        for row in rows:
            print("  " + "  ".join(f"{k}={row[k]}" for k in row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="Write the raw result here.")
    args = parser.parse_args()

    dsn = os.environ.get("MTG_V1_DSN", "").strip()
    if not dsn:
        print(
            "MTG_V1_DSN is not set. This census reads the live corpus and has "
            "no fixture mode on purpose: a census of a fixture measures the "
            "fixture. See docs/d1-corpus-census.md.",
            file=sys.stderr,
        )
        return 2

    results = run_census(dsn)
    _render(results)
    if args.json:
        args.json.write_text(json.dumps(_jsonable(results), indent=2) + "\n")
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
