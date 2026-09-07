"""Read-only local research queries with explicit metric denominators."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def _colors(value: Any) -> list[str]:
    try:
        return (
            json.loads(value or "[]") if isinstance(value, str) else list(value or [])
        )
    except (json.JSONDecodeError, TypeError):
        return []


class ResearchRepo:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def commander_choices(self, *, limit: int = 250) -> list[dict[str, str]]:
        """Return a bounded alphabetical list for comparison controls."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,name FROM commander_candidates "
                "ORDER BY name COLLATE NOCASE LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [{"id": str(row["id"]), "name": str(row["name"])} for row in rows]

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
                "EXISTS (SELECT 1 FROM tournament_results trp "
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
                    "SELECT COUNT(DISTINCT COALESCE(source_entry_id,id)) FROM tournament_results WHERE tournament_date>=? AND tournament_date<?",
                    (start, end),
                ).fetchone()[0]
            )
            prior_entries = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT COALESCE(source_entry_id,id)) FROM tournament_results WHERE tournament_date>=? AND tournament_date<?",
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
            having_sql = f" HAVING {' AND '.join(having)}" if having else ""
            count = int(
                conn.execute(
                    f"""SELECT COUNT(*) FROM (
                        SELECT cc.id,
                          COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? THEN 1 END) * 1.0 / NULLIF(?,0) AS meta_share
                        FROM commander_candidates cc
                        LEFT JOIN tournament_results tr ON tr.commander_id=cc.id
                        WHERE {' AND '.join(where)} GROUP BY cc.id{having_sql}
                    )""",
                    [start, end, total_entries, *params, *having_params],
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""SELECT cc.id, cc.oracle_id, cc.name, cc.type_line,
                    cc.color_identity, cc.mana_cost, cc.cmc, cc.image_uri,
                    COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? THEN 1 END) AS entries,
                    COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? AND tr.standing IS NOT NULL THEN 1 END) AS finish_coverage,
                    COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? AND tr.standing BETWEEN 1 AND 16 THEN 1 END) AS top16,
                    COUNT(DISTINCT CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? THEN tr.tournament_id END) AS events,
                    COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? THEN 1 END) * 1.0 / NULLIF(?,0) AS meta_share,
                    COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? AND tr.standing BETWEEN 1 AND 16 THEN 1 END) * 1.0 /
                        NULLIF(COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? AND tr.standing IS NOT NULL THEN 1 END),0) AS top16_rate,
                    (COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? THEN 1 END) * 1.0 / NULLIF(?,0)) -
                    (COUNT(CASE WHEN tr.tournament_date>=? AND tr.tournament_date<? THEN 1 END) * 1.0 / NULLIF(?,0)) AS trend
                    FROM commander_candidates cc
                    LEFT JOIN tournament_results tr ON tr.commander_id=cc.id
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
        colors: list[str] | None = None,
        color_mode: str = "all",
        mana_operator: str = "lte",
        mana_value: float | None = None,
        rarity: str = "",
    ) -> dict[str, Any]:
        page = max(page, 1)
        where = ["c.name LIKE ?"]
        values: list[Any] = [f"%{query}%"]
        if oracle_text:
            where.append("c.oracle_text LIKE ?")
            values.append(f"%{oracle_text}%")
        if type_line:
            where.append("c.type_line LIKE ?")
            values.append(f"%{type_line}%")
        selected_colors = [color for color in colors or [] if color in set("WUBRGC")]
        colored = [color for color in selected_colors if color != "C"]
        color_tests = ["c.color_identity LIKE ?" for _ in colored]
        color_values = [f'%"{color}"%' for color in colored]
        if "C" in selected_colors:
            color_tests.append("json_array_length(c.color_identity)=0")
        if color_tests:
            if color_mode == "any":
                where.append(f"({' OR '.join(color_tests)})")
                values.extend(color_values)
            else:
                where.extend(color_tests)
                values.extend(color_values)
                if color_mode == "exact":
                    where.append("json_array_length(c.color_identity)=?")
                    values.append(len(colored))
        if mana_value is not None:
            operator = {"eq": "=", "gte": ">=", "lte": "<="}.get(mana_operator, "<=")
            where.append(f"c.cmc {operator} ?")
            values.append(mana_value)
        if rarity in {"common", "uncommon", "rare", "mythic"}:
            where.append("c.rarity=?")
            values.append(rarity)
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT c.id,c.oracle_id,c.name,c.type_line,c.mana_cost,c.cmc,
                          c.oracle_text,c.color_identity,c.image_uri,c.rarity
                   FROM cards c WHERE {' AND '.join(where)}
                     AND c.id=(SELECT c2.id FROM cards c2 WHERE c2.name=c.name
                               ORDER BY c2.image_uri IS NULL,c2.id LIMIT 1)
                   ORDER BY c.name COLLATE NOCASE LIMIT ? OFFSET ?""",
                [*values, per_page + 1, (page - 1) * per_page],
            ).fetchall()
        has_next = len(rows) > per_page
        results = [dict(row) for row in rows[:per_page]]
        for row in results:
            row["color_identity"] = _colors(row.get("color_identity"))
        return {"results": results, "page": page, "has_next": has_next}

    def commander_detail(
        self, card_id: str, *, window_days: int = 90
    ) -> dict[str, Any] | None:
        window_days = 0 if window_days == 0 else max(7, min(window_days, 365))
        start, end, prior = self._scope(window_days)
        with self._connect() as conn:
            card = conn.execute(
                "SELECT id,oracle_id,name,type_line,mana_cost,cmc,oracle_text,color_identity,image_uri "
                "FROM commander_candidates WHERE id=?",
                (card_id,),
            ).fetchone()
            if card is None:
                return None
            current_total = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT COALESCE(source_entry_id,id)) FROM tournament_results WHERE tournament_date>=? AND tournament_date<?",
                    (start, end),
                ).fetchone()[0]
            )
            prior_total = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT COALESCE(source_entry_id,id)) FROM tournament_results WHERE tournament_date>=? AND tournament_date<?",
                    (prior, start),
                ).fetchone()[0]
            )
            metrics = conn.execute(
                """SELECT COUNT(*) AS entries,
                    COUNT(CASE WHEN standing IS NOT NULL THEN 1 END) AS finish_coverage,
                    COUNT(CASE WHEN standing BETWEEN 1 AND 16 THEN 1 END) AS top16,
                    COUNT(DISTINCT tournament_id) AS events
                   FROM tournament_results WHERE commander_id=?
                   AND tournament_date>=? AND tournament_date<?""",
                (card_id, start, end),
            ).fetchone()
            previous = int(
                conn.execute(
                    "SELECT COUNT(*) FROM tournament_results WHERE commander_id=? "
                    "AND tournament_date>=? AND tournament_date<?",
                    (card_id, prior, start),
                ).fetchone()[0]
            )
            denominator = int(
                conn.execute(
                    """SELECT COUNT(DISTINCT tr.deck_id)
                       FROM tournament_results tr
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
                     FROM tournament_results tr
                     JOIN deck_cards dc ON dc.deck_id=tr.deck_id AND dc.is_commander=0
                     JOIN cards c ON c.id=dc.card_id
                     WHERE tr.commander_id=? AND tr.tournament_date>=? AND tr.tournament_date<?
                       AND c.type_line NOT LIKE '%Land%'
                     GROUP BY tr.deck_id
                   )""",
                (card_id, start, end),
            ).fetchone()
            inclusions = conn.execute(
                """SELECT c.id,c.oracle_id,c.name,c.type_line,c.mana_cost,c.cmc,
                          c.image_uri,COUNT(DISTINCT tr.deck_id) AS decks_including
                   FROM tournament_results tr
                   JOIN deck_cards dc ON dc.deck_id=tr.deck_id
                   JOIN cards c ON c.id=dc.card_id
                   WHERE tr.commander_id=? AND tr.tournament_date>=? AND tr.tournament_date<?
                     AND dc.is_commander=0
                   GROUP BY c.oracle_id ORDER BY decks_including DESC,c.name LIMIT 40""",
                (card_id, start, end),
            ).fetchall()
            rulings = conn.execute(
                "SELECT ruling_date,ruling_text FROM card_rulings WHERE card_oracle_id=? "
                "ORDER BY ruling_date DESC LIMIT 20",
                (card["oracle_id"],),
            ).fetchall()
            recent_lists = conn.execute(
                """SELECT deck_id,player_name,standing,tournament_date
                   FROM tournament_results WHERE commander_id=? AND deck_id IS NOT NULL
                     AND tournament_date>=? AND tournament_date<?
                   ORDER BY tournament_date DESC,standing IS NULL,standing LIMIT 12""",
                (card_id, start, end),
            ).fetchall()
        result = dict(card)
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
        result["inclusions"] = [dict(row) for row in inclusions]
        result["rulings"] = [dict(row) for row in rulings]
        result["recent_lists"] = [dict(row) for row in recent_lists]
        return result

    def card_detail(self, card_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id,oracle_id,name,type_line,mana_cost,cmc,oracle_text,color_identity,image_uri,rarity "
                "FROM cards WHERE id=?",
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
