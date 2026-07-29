"""Tests for the Explore filter builder (P3).

Unit tests cover the pure builder; an integration test executes the built SQL
against the real ``commander_candidates`` view when a database is present.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from sabermetrics.ui.explore_filters import build_explore_query

DB_PATH = Path("data/sabermetrics.db")
HAS_DB = DB_PATH.exists()


def _args(**kwargs) -> MultiDict:
    md = MultiDict()
    for key, value in kwargs.items():
        if isinstance(value, (list, tuple)):
            for v in value:
                md.add(key, v)
        else:
            md.add(key, value)
    return md


# --- Unit tests ---


def test_default_query_matches_all() -> None:
    eq = build_explore_query(_args())
    assert eq.where_sql == "1=1"
    assert eq.params == []
    assert eq.sort == "name"
    assert eq.page == 1
    assert eq.offset == 0


def test_name_search() -> None:
    eq = build_explore_query(_args(q="korvold"))
    assert "name LIKE ?" in eq.where_sql
    assert "%korvold%" in eq.params


def test_colors_exactly_mono_red() -> None:
    eq = build_explore_query(_args(color="R", color_mode="exactly"))
    # R present, the other four absent
    assert eq.where_sql.count("color_identity LIKE ?") == 1
    assert eq.where_sql.count("color_identity NOT LIKE ?") == 4
    assert '%"R"%' in eq.params
    assert '%"G"%' in eq.params  # as a NOT LIKE param


def test_colors_atmost_only_excludes_absent() -> None:
    eq = build_explore_query(_args(color=["R", "G"], color_mode="atmost"))
    # no positive color clause; W/U/B excluded
    assert "color_identity LIKE ?" not in eq.where_sql
    assert eq.where_sql.count("color_identity NOT LIKE ?") == 3
    assert eq.colors == ["R", "G"]


def test_colors_exactly_empty_is_colorless() -> None:
    eq = build_explore_query(_args(color_mode="exactly"))
    # all five colors excluded => colorless identity
    assert eq.where_sql.count("color_identity NOT LIKE ?") == 5
    assert "color_identity LIKE ?" not in eq.where_sql


def test_ability_filter() -> None:
    eq = build_explore_query(_args(ability="Flying"))
    assert "keywords LIKE ?" in eq.where_sql
    assert '%"Flying"%' in eq.params
    assert eq.abilities == ["Flying"]


def test_invalid_ability_and_color_ignored() -> None:
    eq = build_explore_query(_args(ability="Bogus", color="X"))
    assert eq.where_sql == "1=1"
    assert eq.abilities == []
    assert eq.colors == []


def test_price_and_cmc_ranges() -> None:
    eq = build_explore_query(
        _args(price_min="1", price_max="10", cmc_min="2", cmc_max="5")
    )
    assert "price_usd >= ?" in eq.where_sql
    assert "price_usd <= ?" in eq.where_sql
    assert "cmc >= ?" in eq.where_sql
    assert "cmc <= ?" in eq.where_sql
    assert eq.params == [1.0, 10.0, 2.0, 5.0]


def test_sort_and_pagination() -> None:
    eq = build_explore_query(_args(sort="price_desc", page="3"), per_page=24)
    assert "price_usd" in eq.order_sql and "DESC" in eq.order_sql
    assert eq.page == 3
    assert eq.offset == 48
    # invalid sort falls back to name
    assert build_explore_query(_args(sort="nonsense")).sort == "name"


def test_garbage_numbers_ignored() -> None:
    eq = build_explore_query(_args(price_min="abc", cmc_max=""))
    assert eq.price_min is None
    assert eq.cmc_max is None
    assert eq.where_sql == "1=1"


# --- Integration test (executes the SQL) ---


@pytest.mark.skipif(not HAS_DB, reason="No database available")
def test_built_sql_executes_and_filters() -> None:
    eq = build_explore_query(_args(color="R", color_mode="exactly", price_max="5"))
    sql = (
        "SELECT name, color_identity, price_usd FROM commander_candidates "
        f"WHERE {eq.where_sql} ORDER BY {eq.order_sql} LIMIT ? OFFSET ?"
    )
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, [*eq.params, eq.limit, eq.offset]).fetchall()
    finally:
        conn.close()

    # Every result is mono-red and within budget.
    for r in rows:
        assert json.loads(r["color_identity"]) == ["R"]
        if r["price_usd"] is not None:
            assert r["price_usd"] <= 5
