"""Read-only local research queries with explicit metric denominators."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sabermetrics.card_discovery import (
    CARD_TYPES,
    PRIMARY_TYPES,
    RARITIES,
    SUPERTYPES,
    apply_colors,
    apply_discrete_range,
    apply_type_token,
    commander_eligible_sql,
    format_legal_sql,
    full_range,
    numeric_stat_sql,
    primary_type,
)
from sabermetrics.research_identities import attach_members


def _colors(value: Any) -> list[str]:
    try:
        return (
            json.loads(value or "[]") if isinstance(value, str) else list(value or [])
        )
    except (json.JSONDecodeError, TypeError):
        return []


# Same membership/representative-printing rules as research_commanders. Materialize
# identity sets once instead of scanning tournament identities for every candidate.
# Keep this equivalent to research_identities.ensure_schema's public view.
_COMMANDER_CATALOG = """fast_commanders AS MATERIALIZED (
 SELECT c.id,c.oracle_id,c.name,c.type_line,c.mana_cost,c.cmc,c.oracle_text,c.color_identity,c.image_uri,json_array(c.id) AS card_ids
 FROM commander_candidates c
 WHERE c.id NOT IN (SELECT m.value FROM research_commander_pairs p,json_each(p.card_ids) m WHERE m.value IS NOT NULL)
 OR c.id IN (SELECT COALESCE(commander_identity_id,commander_id) FROM tournament_results)
 UNION ALL SELECT p.id,NULL,p.name,'Commander pair',NULL,p.cmc,NULL,p.color_identity,NULL,p.card_ids
 FROM research_commander_pairs p WHERE p.id IN (SELECT commander_identity_id FROM tournament_results)
),"""


class ResearchRepo:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def commander_choices(self, *, limit: int = 250) -> list[dict[str, str]]:
        """Return a bounded alphabetical list of format-legal commander-eligible cards."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT c.id,c.name FROM commander_candidates c "
                f"WHERE {commander_eligible_sql('c')} "
                "ORDER BY c.name COLLATE NOCASE LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [{"id": str(row["id"]), "name": str(row["name"])} for row in rows]

    def commander_catalog(
        self,
        *,
        query: str = "",
        colors: list[str] | None = None,
        color_mode: str = "all",
        mana_min: float | None = None,
        mana_max: float | None = None,
        mana_min_bound: int | None = None,
        mana_max_bound: int | None = None,
        favorites: set[str] | None = None,
        favorite_only: bool = False,
        page: int = 1,
        per_page: int = 24,
    ) -> dict[str, Any]:
        """Alphabetical catalog of legal commander-eligible cards, no meta join."""
        page = max(1, page)
        per_page = max(1, min(per_page, 100))
        where = [commander_eligible_sql("c"), "c.name LIKE ?"]
        params: list[Any] = [f"%{query}%"]
        apply_colors(where, params, list(colors or []), color_mode)
        if mana_min_bound is not None or mana_max_bound is not None:
            apply_discrete_range(where, params, "c.cmc", mana_min_bound, mana_max_bound)
        else:
            if mana_min is not None:
                where.append("c.cmc>=?")
                params.append(mana_min)
            if mana_max is not None:
                where.append("c.cmc<=?")
                params.append(mana_max)
        if favorite_only:
            ids = sorted(favorites or set())
            if not ids:
                return {
                    "results": [],
                    "total": 0,
                    "page": page,
                    "has_next": False,
                    "recorded_entries": None,
                    "window_days": None,
                }
            where.append(f"c.id IN ({','.join('?' for _ in ids)})")
            params.extend(ids)
        with self._connect() as conn:
            count = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM commander_candidates c WHERE {' AND '.join(where)}",
                    params,
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""SELECT c.id,c.oracle_id,c.name,c.type_line,c.mana_cost,c.cmc,
                          c.oracle_text,c.color_identity,c.image_uri
                    FROM commander_candidates c
                    WHERE {' AND '.join(where)}
                    ORDER BY c.name COLLATE NOCASE LIMIT ? OFFSET ?""",
                [*params, per_page, (page - 1) * per_page],
            ).fetchall()
        results = [dict(row) for row in rows]
        for row in results:
            row["color_identity"] = _colors(row.get("color_identity"))
            row["favorited"] = row["id"] in (favorites or set())
            row["meta_share"] = None
            row["top16_rate"] = None
            row["entries"] = None
            row["trend"] = None
            row["finish_coverage"] = None
        return {
            "results": results,
            "total": count,
            "page": page,
            "has_next": page * per_page < count,
            "recorded_entries": None,
            "window_days": None,
        }

    @staticmethod
    def _scope(window_days: int) -> tuple[str, str, str]:
        end = date.today() + timedelta(days=1)
        if window_days == 0:
            beginning = date.min.isoformat()
            return beginning, end.isoformat(), beginning
        start = end - timedelta(days=window_days)
        prior = start - timedelta(days=window_days)
        return start.isoformat(), end.isoformat(), prior.isoformat()

    def commanders(
        self,
        *,
        query: str = "",
        colors: list[str] | None = None,
        color_mode: str = "all",
        mana_min: float | None = None,
        mana_max: float | None = None,
        meta_min: float | None = None,
        meta_max: float | None = None,
        favorites: set[str] | None = None,
        favorite_only: bool = False,
        plays_card: str = "",
        window_days: int = 90,
        sort: str = "meta",
        page: int = 1,
        per_page: int = 24,
        observed_only: bool = False,
    ) -> dict[str, Any]:
        window_days = 0 if window_days == 0 else max(7, min(window_days, 365))
        page = max(1, page)
        per_page = max(1, min(per_page, 100))
        start, end, prior = self._scope(window_days)
        where = ["cc.name LIKE ?"]
        params: list[Any] = [f"%{query}%"]
        selected_colors = [color for color in colors or [] if color in set("WUBRG")]
        if selected_colors and color_mode == "any":
            where.append(
                f"({' OR '.join('cc.color_identity LIKE ?' for _ in selected_colors)})"
            )
            params.extend(f'%"{color}"%' for color in selected_colors)
        else:
            for color in selected_colors:
                where.append("cc.color_identity LIKE ?")
                params.append(f'%"{color}"%')
            if selected_colors and color_mode == "exact":
                where.append("json_array_length(cc.color_identity)=?")
                params.append(len(selected_colors))
        if mana_min is not None:
            where.append("cc.cmc>=?")
            params.append(mana_min)
        if mana_max is not None:
            where.append("cc.cmc<=?")
            params.append(mana_max)
        if plays_card:
            where.append(
                "EXISTS (SELECT 1 FROM research_results trp "
                "JOIN deck_cards dcp ON dcp.deck_id=trp.deck_id "
                "JOIN cards cp ON cp.id=dcp.card_id "
                "WHERE trp.commander_id=cc.id AND cp.name LIKE ?)"
            )
            params.append(f"%{plays_card}%")
        if favorite_only:
            ids = sorted(favorites or set())
            if not ids:
                return {
                    "results": [],
                    "total": 0,
                    "recorded_entries": 0,
                    "window_days": window_days,
                    "page": page,
                    "has_next": False,
                }
            where.append(f"cc.id IN ({','.join('?' for _ in ids)})")
            params.extend(ids)
        order = {
            "name": "cc.name COLLATE NOCASE ASC",
            "top16": "top16_rate DESC, entries DESC, cc.name",
            "decks": "entries DESC, cc.name",
            "trend": "trend DESC, cc.name",
        }.get(sort, "meta_share DESC, cc.name")
        with self._connect() as conn:
            total_entries = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT COALESCE(source_entry_id,id)) FROM research_results WHERE tournament_date>=? AND tournament_date<?",
                    (start, end),
                ).fetchone()[0]
            )
            prior_entries = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT COALESCE(source_entry_id,id)) FROM research_results WHERE tournament_date>=? AND tournament_date<?",
                    (prior, start),
                ).fetchone()[0]
            )
            having: list[str] = []
            having_params: list[Any] = []
            if meta_min is not None:
                having.append("meta_share>=?")
                having_params.append(max(0.0, min(meta_min, 1.0)))
            if meta_max is not None:
                having.append("meta_share<=?")
                having_params.append(max(0.0, min(meta_max, 1.0)))
            if observed_only:
                having.append(
                    "COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? THEN 1 END)>0"
                )
                having_params.extend([start, end])
            having_sql = f" HAVING {' AND '.join(having)}" if having else ""
            count = int(
                conn.execute(
                    f"""WITH {_COMMANDER_CATALOG} cohort_results AS MATERIALIZED (SELECT * FROM research_results)
                    SELECT COUNT(*) FROM (
                        SELECT cc.id,
                          COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? THEN 1 END) * 1.0 / NULLIF(?,0) AS meta_share
                        FROM fast_commanders cc
                        LEFT JOIN cohort_results tr ON tr.commander_id=cc.id
                        WHERE {' AND '.join(where)} GROUP BY cc.id{having_sql}
                    )""",
                    [start, end, total_entries, *params, *having_params],
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""WITH {_COMMANDER_CATALOG} cohort_results AS MATERIALIZED (SELECT * FROM research_results)
                    SELECT cc.id, cc.oracle_id, cc.name, cc.type_line,
                    cc.color_identity, cc.mana_cost, cc.cmc, cc.image_uri, cc.card_ids,
                    COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? THEN 1 END) AS entries,
                    COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? AND tr.standing IS NOT NULL THEN 1 END) AS finish_coverage,
                    COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? AND tr.standing BETWEEN 1 AND 16 THEN 1 END) AS top16,
                    COUNT(DISTINCT CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? THEN tr.tournament_id END) AS events,
                    COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? THEN 1 END) * 1.0 / NULLIF(?,0) AS meta_share,
                    COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? AND tr.standing BETWEEN 1 AND 16 THEN 1 END) * 1.0 /
                        NULLIF(COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? AND tr.standing IS NOT NULL THEN 1 END),0) AS top16_rate,
                    (COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? THEN 1 END) * 1.0 / NULLIF(?,0)) -
                    (COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? THEN 1 END) * 1.0 / NULLIF(?,0)) AS trend
                    FROM fast_commanders cc
                    LEFT JOIN cohort_results tr ON tr.commander_id=cc.id
                    WHERE {' AND '.join(where)} GROUP BY cc.id{having_sql}
                    ORDER BY {order} LIMIT ? OFFSET ?""",
                [
                    start,
                    end,
                    start,
                    end,
                    start,
                    end,
                    start,
                    end,
                    start,
                    end,
                    total_entries,
                    start,
                    end,
                    start,
                    end,
                    start,
                    end,
                    total_entries,
                    prior,
                    start,
                    prior_entries,
                    *params,
                    *having_params,
                    per_page,
                    (page - 1) * per_page,
                ],
            ).fetchall()
        results = [dict(row) for row in rows]
        with self._connect() as conn:
            for row in results:
                attach_members(conn, row)
        for row in results:
            row["color_identity"] = _colors(row.get("color_identity"))
            row["favorited"] = row["id"] in (favorites or set())
        return {
            "results": results,
            "total": count,
            "recorded_entries": total_entries,
            "window_days": window_days,
            "page": page,
            "has_next": page * per_page < count,
        }

    def cards(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int = 24,
        oracle_text: str = "",
        type_line: str = "",
        super_type: str = "",
        super_op: str = "is",
        card_type: str = "",
        type_op: str = "is",
        sub_type: str = "",
        sub_op: str = "is",
        colors: list[str] | None = None,
        color_mode: str = "include",
        mana_operator: str = "lte",
        mana_value: float | None = None,
        mana_min_bound: int | None = None,
        mana_max_bound: int | None = None,
        power_min_bound: int | None = None,
        power_max_bound: int | None = None,
        toughness_min_bound: int | None = None,
        toughness_max_bound: int | None = None,
        rarity: str = "",
    ) -> dict[str, Any]:
        page = max(page, 1)
        where = [format_legal_sql("c"), "c.name LIKE ?"]
        values: list[Any] = [f"%{query}%"]
        if oracle_text:
            where.append("c.oracle_text LIKE ?")
            values.append(f"%{oracle_text}%")
        if type_line:
            where.append("c.type_line LIKE ?")
            values.append(f"%{type_line}%")
        apply_type_token(where, values, super_type, super_op, allowed=SUPERTYPES)
        apply_type_token(where, values, card_type, type_op, allowed=CARD_TYPES)
        apply_type_token(where, values, sub_type, sub_op, subtype=True)
        apply_colors(where, values, list(colors or []), color_mode)
        if not full_range(mana_min_bound, mana_max_bound):
            apply_discrete_range(where, values, "c.cmc", mana_min_bound, mana_max_bound)
        elif mana_value is not None:
            operator = {"eq": "=", "gte": ">=", "lte": "<="}.get(mana_operator, "<=")
            where.append(f"c.cmc {operator} ?")
            values.append(mana_value)
        apply_discrete_range(
            where,
            values,
            numeric_stat_sql("c.power"),
            power_min_bound,
            power_max_bound,
        )
        apply_discrete_range(
            where,
            values,
            numeric_stat_sql("c.toughness"),
            toughness_min_bound,
            toughness_max_bound,
        )
        if rarity in RARITIES:
            where.append("c.rarity=?")
            values.append(rarity)
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT c.id,c.oracle_id,c.name,c.type_line,c.mana_cost,c.cmc,
                          c.oracle_text,c.color_identity,c.image_uri,c.rarity,
                          c.power,c.toughness
                   FROM cards c WHERE {' AND '.join(where)}
                     AND c.id=(SELECT c2.id FROM cards c2 WHERE c2.name=c.name
                               ORDER BY c2.image_uri IS NULL,c2.id LIMIT 1)
                   ORDER BY c.name COLLATE NOCASE LIMIT ? OFFSET ?""",
                [*values, per_page + 1, (page - 1) * per_page],
            ).fetchall()
            coverage = conn.execute(
                f"""SELECT COUNT(*) AS total,
                          SUM(CASE WHEN {numeric_stat_sql("power")} IS NOT NULL
                                   THEN 1 ELSE 0 END) AS numeric_power,
                          SUM(CASE WHEN {numeric_stat_sql("toughness")} IS NOT NULL
                                   THEN 1 ELSE 0 END) AS numeric_toughness
                   FROM cards c WHERE is_legal_in_99=1
                     AND c.id=(SELECT c2.id FROM cards c2 WHERE c2.name=c.name
                               ORDER BY c2.image_uri IS NULL,c2.id LIMIT 1)"""
            ).fetchone()
        has_next = len(rows) > per_page
        results = [dict(row) for row in rows[:per_page]]
        for row in results:
            row["color_identity"] = _colors(row.get("color_identity"))
        return {
            "results": results,
            "page": page,
            "has_next": has_next,
            "stat_coverage": {
                "legal_cards": int(coverage["total"] or 0),
                "numeric_power": int(coverage["numeric_power"] or 0),
                "numeric_toughness": int(coverage["numeric_toughness"] or 0),
            },
        }

    def commander_detail(
        self, card_id: str, *, window_days: int = 90
    ) -> dict[str, Any] | None:
        window_days = 0 if window_days == 0 else max(7, min(window_days, 365))
        start, end, prior = self._scope(window_days)
        with self._connect() as conn:
            card = conn.execute(
                "SELECT id,oracle_id,name,type_line,mana_cost,cmc,oracle_text,color_identity,image_uri,card_ids "
                "FROM research_commanders WHERE id=?",
                (card_id,),
            ).fetchone()
            if card is None:
                card = conn.execute(
                    "SELECT id,oracle_id,name,type_line,mana_cost,cmc,oracle_text,"
                    "color_identity,image_uri,json_array(id) AS card_ids "
                    "FROM commander_candidates WHERE id=?",
                    (card_id,),
                ).fetchone()
            if card is None:
                return None
            current_total = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT COALESCE(source_entry_id,id)) FROM research_results WHERE tournament_date>=? AND tournament_date<?",
                    (start, end),
                ).fetchone()[0]
            )
            prior_total = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT COALESCE(source_entry_id,id)) FROM research_results WHERE tournament_date>=? AND tournament_date<?",
                    (prior, start),
                ).fetchone()[0]
            )
            metrics = conn.execute(
                """SELECT COUNT(*) AS entries,
                    COUNT(CASE WHEN standing IS NOT NULL THEN 1 END) AS finish_coverage,
                    COUNT(CASE WHEN standing BETWEEN 1 AND 16 THEN 1 END) AS top16,
                    COUNT(DISTINCT tournament_id) AS events
                   FROM research_results WHERE commander_id=?
                   AND tournament_date>=? AND tournament_date<?""",
                (card_id, start, end),
            ).fetchone()
            previous = int(
                conn.execute(
                    "SELECT COUNT(*) FROM research_results WHERE commander_id=? "
                    "AND tournament_date>=? AND tournament_date<?",
                    (card_id, prior, start),
                ).fetchone()[0]
            )
            denominator = int(
                conn.execute(
                    """SELECT COUNT(DISTINCT tr.deck_id)
                       FROM research_results tr
                       WHERE tr.commander_id=? AND tr.deck_id IS NOT NULL
                         AND tr.tournament_date>=? AND tr.tournament_date<?
                         AND EXISTS (SELECT 1 FROM deck_cards dc WHERE dc.deck_id=tr.deck_id)""",
                    (card_id, start, end),
                ).fetchone()[0]
            )
            mana_value = conn.execute(
                """SELECT AVG(deck_mv) AS average_mv, COUNT(*) AS list_count
                   FROM (
                     SELECT tr.deck_id,
                       SUM(c.cmc * dc.quantity) * 1.0 / NULLIF(SUM(dc.quantity),0) AS deck_mv
                     FROM research_results tr
                     JOIN deck_cards dc ON dc.deck_id=tr.deck_id AND dc.is_commander=0
                     JOIN cards c ON c.id=dc.card_id
                     WHERE tr.commander_id=? AND tr.tournament_date>=? AND tr.tournament_date<?
                       AND c.type_line NOT LIKE '%Land%'
                     GROUP BY tr.deck_id
                   )""",
                (card_id, start, end),
            ).fetchone()
            representative = self._representative_recorded_list(
                conn, card_id, start, end
            )
            rulings = conn.execute(
                "SELECT ruling_date,ruling_text FROM card_rulings WHERE card_oracle_id IN "
                "(SELECT oracle_id FROM cards WHERE id IN (SELECT value FROM json_each(?))) "
                "ORDER BY ruling_date DESC LIMIT 20",
                (card["card_ids"],),
            ).fetchall()
            recent_lists = conn.execute(
                """SELECT deck_id,player_name,standing,tournament_date
                   FROM research_results WHERE commander_id=? AND deck_id IS NOT NULL
                     AND tournament_date>=? AND tournament_date<?
                   ORDER BY tournament_date DESC,standing IS NULL,standing LIMIT 12""",
                (card_id, start, end),
            ).fetchall()
        result = dict(card)
        with self._connect() as conn:
            attach_members(conn, result)
        result["color_identity"] = _colors(result.get("color_identity"))
        result["metrics"] = {
            **dict(metrics),
            "meta_share": (
                (metrics["entries"] / current_total) if current_total else None
            ),
            "top16_rate": (
                (metrics["top16"] / metrics["finish_coverage"])
                if metrics["finish_coverage"]
                else None
            ),
            "trend": (
                (metrics["entries"] / current_total - previous / prior_total)
                if current_total and prior_total
                else None
            ),
            "window_days": window_days,
            "average_nonland_mv": mana_value["average_mv"],
            "mv_list_count": int(mana_value["list_count"]),
        }
        result["inclusion_denominator"] = denominator
        result["inclusions"] = []
        result["representative_list"] = representative
        result["rulings"] = [dict(row) for row in rulings]
        result["recent_lists"] = [dict(row) for row in recent_lists]
        return result

    @staticmethod
    def _merge_recorded_printings(
        rows: list[sqlite3.Row],
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        """Collapse duplicate oracle/name printings using recorded quantities only."""
        commanders: list[dict[str, Any]] = []
        groups: dict[str, list[dict[str, Any]]] = {}
        seen_commanders: dict[str, dict[str, Any]] = {}
        seen_library: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = str(row["oracle_id"] or row["name"] or row["id"])
            item = {
                "id": row["id"],
                "name": row["name"] or "Unknown card",
                "type_line": row["type_line"] or "",
                "image_uri": row["image_uri"],
                "quantity": int(row["quantity"] or 1),
            }
            if row["is_commander"]:
                existing = seen_commanders.get(key)
                if existing is None:
                    seen_commanders[key] = item
                    commanders.append(item)
                else:
                    existing["quantity"] += item["quantity"]
                continue
            kind = primary_type(item["type_line"])
            existing = seen_library.get((kind, key))
            if existing is None:
                seen_library[(kind, key)] = item
                groups.setdefault(kind, []).append(item)
            else:
                existing["quantity"] += item["quantity"]
        return commanders, groups

    @staticmethod
    def _representative_recorded_list(
        conn: sqlite3.Connection, commander_id: str, start: str, end: str
    ) -> dict[str, Any] | None:
        """Pick one recorded list by completeness, then overlap, then deck id."""
        chosen = conn.execute(
            """WITH cohort AS (
                 SELECT DISTINCT tr.deck_id
                 FROM research_results tr
                 WHERE tr.commander_id=? AND tr.deck_id IS NOT NULL
                   AND tr.tournament_date>=? AND tr.tournament_date<?
                   AND EXISTS (SELECT 1 FROM deck_cards dc WHERE dc.deck_id=tr.deck_id)
               ),
               freq AS (
                 SELECT COALESCE(NULLIF(c.oracle_id,''), dc.card_id) AS oracle_key,
                        COUNT(DISTINCT dc.deck_id) AS decks_including
                 FROM cohort
                 JOIN deck_cards dc
                   ON dc.deck_id=cohort.deck_id AND dc.is_commander=0
                 JOIN cards c ON c.id=dc.card_id
                 GROUP BY COALESCE(NULLIF(c.oracle_id,''), dc.card_id)
               ),
               scored AS (
                 SELECT cohort.deck_id,
                   COALESCE(SUM(freq.decks_including),0) AS overlap,
                   COALESCE(SUM(CASE WHEN dc.is_commander=0 THEN dc.quantity ELSE 0 END),0)
                     AS library_count,
                   COALESCE(SUM(CASE WHEN dc.is_commander=1 THEN dc.quantity ELSE 0 END),0)
                     AS commander_count
                 FROM cohort
                 LEFT JOIN deck_cards dc ON dc.deck_id=cohort.deck_id
                 LEFT JOIN cards c ON c.id=dc.card_id
                 LEFT JOIN freq
                   ON freq.oracle_key=COALESCE(NULLIF(c.oracle_id,''), dc.card_id)
                  AND dc.is_commander=0
                 GROUP BY cohort.deck_id
               )
               SELECT s.deck_id, s.overlap, s.library_count, s.commander_count,
                      r.player_name, r.standing, r.tournament_date
               FROM scored s
               JOIN research_results r
                 ON r.deck_id=s.deck_id AND r.commander_id=?
                AND r.tournament_date>=? AND r.tournament_date<?
               WHERE r.id=(
                 SELECT r2.id FROM research_results r2
                 WHERE r2.deck_id=s.deck_id AND r2.commander_id=?
                   AND r2.tournament_date>=? AND r2.tournament_date<?
                 ORDER BY r2.tournament_date DESC, r2.id
                 LIMIT 1
               )
               ORDER BY CASE
                          WHEN s.library_count + s.commander_count = 100
                           AND s.commander_count IN (1, 2) THEN 1
                          ELSE 0
                        END DESC,
                        s.overlap DESC, s.deck_id
               LIMIT 1""",
            (
                commander_id,
                start,
                end,
                commander_id,
                start,
                end,
                commander_id,
                start,
                end,
            ),
        ).fetchone()
        if chosen is None:
            return None
        deck_id = str(chosen["deck_id"])
        rows = conn.execute(
            """SELECT c.id,c.oracle_id,c.name,c.type_line,c.image_uri,
                      dc.quantity,dc.is_commander
               FROM deck_cards dc JOIN cards c ON c.id=dc.card_id
               WHERE dc.deck_id=?
               ORDER BY dc.is_commander DESC, c.name COLLATE NOCASE, c.id""",
            (deck_id,),
        ).fetchall()
        commanders, groups = ResearchRepo._merge_recorded_printings(list(rows))
        grouped = [
            {"type": kind, "cards": groups[kind]}
            for kind in (*PRIMARY_TYPES, "Other")
            if kind in groups
        ]
        library_count = int(chosen["library_count"] or 0)
        commander_count = int(chosen["commander_count"] or 0)
        complete = library_count + commander_count == 100 and commander_count in (1, 2)
        return {
            "deck_id": deck_id,
            "player_name": chosen["player_name"],
            "standing": chosen["standing"],
            "tournament_date": chosen["tournament_date"],
            "library_count": library_count,
            "commander_count": commander_count,
            "complete": complete,
            "overlap": int(chosen["overlap"] or 0),
            "provenance": (
                "Recorded example from the local corpus. A representative sample "
                "for this commander and window, not a guaranteed optimal list."
            ),
            "commanders": commanders,
            "groups": grouped,
        }

    def card_detail(self, card_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id,oracle_id,name,type_line,mana_cost,cmc,oracle_text,"
                "color_identity,image_uri,rarity,power,toughness "
                "FROM cards WHERE id=? AND is_legal_in_99=1",
                (card_id,),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["rulings"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT ruling_date,ruling_text FROM card_rulings WHERE card_oracle_id=? ORDER BY ruling_date DESC",
                    (row["oracle_id"],),
                ).fetchall()
            ]
        result["color_identity"] = _colors(result.get("color_identity"))
        return result
