"""Card-discovery filters over imported attributes only."""

import math
import sqlite3

from sabermetrics.card_discovery import (
    CARD_TYPES,
    SUPERTYPES,
    apply_colors,
    apply_discrete_range,
    apply_type_token,
    normalize_bound,
    numeric_stat_sql,
)


def test_normalize_bound_rejects_nonfinite_and_overflow():
    assert normalize_bound(None) is None
    assert normalize_bound("") is None
    assert normalize_bound("3") == 3
    assert normalize_bound("10") == 10
    assert normalize_bound("99") == 10
    assert normalize_bound("inf") is None
    assert normalize_bound("-inf") is None
    assert normalize_bound("nan") is None
    assert normalize_bound(math.inf) is None
    assert normalize_bound("1e309") is None


def _query(sql: str, params: list) -> list[str]:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE cards(id TEXT, type_line TEXT, color_identity TEXT, "
        "power TEXT, toughness TEXT)"
    )
    conn.executemany(
        "INSERT INTO cards VALUES(?,?,?,?,?)",
        [
            ("elf", "Legendary Creature — Elf Druid", '["G"]', "1", "2"),
            ("aura", "Enchantment — Aura", "[]", None, None),
            ("tarmo", "Creature — Lhurgoyf", '["G"]', "*", "1+*"),
            ("hydra", "Creature — Hydra", '["G"]', "100", "100"),
            ("wu", "Legendary Creature — Human Advisor", '["W","U"]', "2", "4"),
        ],
    )
    rows = conn.execute(f"SELECT id FROM cards c WHERE {sql}", params).fetchall()
    conn.close()
    return [row[0] for row in rows]


def test_subtype_matches_only_imported_subtype_segment():
    where: list[str] = []
    params: list = []
    apply_type_token(where, params, "Elf", "is", subtype=True)
    assert _query(" AND ".join(where), params) == ["elf"]
    where, params = [], []
    apply_type_token(where, params, "Creature", "is", subtype=True)
    assert _query(" AND ".join(where), params) == []
    where, params = [], []
    apply_type_token(where, params, "Legendary", "is", subtype=True)
    assert _query(" AND ".join(where), params) == []


def test_super_and_type_tokens_do_not_match_subtype_words():
    where: list[str] = []
    params: list = []
    apply_type_token(where, params, "Creature", "is", allowed=CARD_TYPES)
    assert set(_query(" AND ".join(where), params)) == {"elf", "tarmo", "hydra", "wu"}
    where, params = [], []
    apply_type_token(where, params, "Enchantment", "is", allowed=CARD_TYPES)
    assert _query(" AND ".join(where), params) == ["aura"]
    where, params = [], []
    apply_type_token(where, params, "Aura", "is", allowed=CARD_TYPES)
    assert where == []
    where, params = [], []
    apply_type_token(where, params, "Legendary", "is", allowed=SUPERTYPES)
    assert set(_query(" AND ".join(where), params)) == {"elf", "wu"}


def test_numeric_parser_keeps_star_null_and_includes_values_above_99():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE cards(id TEXT, power TEXT, toughness TEXT)")
    conn.executemany(
        "INSERT INTO cards VALUES(?,?,?)",
        [
            ("star", "*", "1+*"),
            ("big", "100", "13"),
            ("zero", "0", "0"),
        ],
    )
    power = numeric_stat_sql("power")
    rows = {
        row[0]: row[1]
        for row in conn.execute(f"SELECT id, {power} FROM cards").fetchall()
    }
    assert rows["star"] is None
    assert rows["big"] == 100
    assert rows["zero"] == 0
    where: list[str] = []
    params: list = []
    apply_discrete_range(where, params, numeric_stat_sql("c.power"), 10, 10)
    conn.execute(
        "CREATE TABLE typed(id TEXT, type_line TEXT, color_identity TEXT, "
        "power TEXT, toughness TEXT)"
    )
    conn.execute(
        "INSERT INTO typed SELECT id,'Creature — Test','[]',power,toughness FROM cards"
    )
    matched = [
        row[0]
        for row in conn.execute(
            f"SELECT id FROM typed c WHERE {' AND '.join(where)}", params
        ).fetchall()
    ]
    assert matched == ["big"]


def test_exact_colors_deduplicate_repeated_query_colors():
    where: list[str] = []
    params: list = []
    apply_colors(where, params, ["W", "W", "U"], "exactly")
    assert params.count(2) == 1
    assert _query(" AND ".join(where), params) == ["wu"]
