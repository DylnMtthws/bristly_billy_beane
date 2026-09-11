"""Indexed, legality-scoped card search used by builder autocomplete and Research.

Name matching is punctuation-tolerant via a generated ``name_search`` column.
Unique faces are the preferred *legal* printing of each name, never a banned
or unknown reprint chosen by a correlated subquery.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections import OrderedDict
from typing import Any

from sabermetrics.card_discovery import format_legal_sql

# Card-name search ignores this punctuation on both the query and stored names.
_APOS_CHARS = "'\u2019\u2018"
_SPACE_PUNCT = ',.-–—.:;!?"()[]/\\&+'
_NEEDLE_TABLE = str.maketrans(
    {char: "" for char in _APOS_CHARS} | {char: " " for char in _SPACE_PUNCT}
)

_MAX_CATALOGS = 8
_CATALOG_LOCK = threading.Lock()
_CatalogStamp = tuple[tuple[int, int], str, int]
_CATALOGS: OrderedDict[str, tuple[_CatalogStamp, list[dict[str, Any]]]] = OrderedDict()

_SEARCH_COLUMNS = (
    "c.id, c.oracle_id, c.name, c.type_line, c.mana_cost, c.cmc, "
    "c.oracle_text, c.color_identity, c.image_uri, c.rarity, "
    "c.is_legal_commander"
)
_RESEARCH_COLUMNS = _SEARCH_COLUMNS + ", c.power, c.toughness"


def search_needle(query: str) -> str:
    return " ".join(str(query).translate(_NEEDLE_TABLE).split())


def strip_punct_sql(expr: str) -> str:
    sql = expr
    for char in _APOS_CHARS:
        sql = f"REPLACE({sql}, char({ord(char)}), '')"
    for char in _SPACE_PUNCT:
        sql = f"REPLACE({sql}, char({ord(char)}), ' ')"
    for _ in range(3):
        sql = f"REPLACE({sql}, '  ', ' ')"
    return f"TRIM({sql})"


def name_search_expr(column: str = "name") -> str:
    return strip_punct_sql(column)


def ensure_card_search_schema(conn: sqlite3.Connection) -> None:
    """Idempotent additive name_search column, indexes, and catalog revision."""
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "cards" not in tables:
        return
    # table_info hides generated columns; table_xinfo does not.
    columns = {row[1] for row in conn.execute("PRAGMA table_xinfo(cards)")}
    if "name_search" not in columns:
        # VIRTUAL can be added to a populated table; STORED cannot.
        conn.execute(
            "ALTER TABLE cards ADD COLUMN name_search TEXT "
            f"GENERATED ALWAYS AS ({name_search_expr('name')}) VIRTUAL"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cards_name_search ON cards(name_search)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cards_legal_name_search "
        "ON cards(name_search) WHERE is_legal_in_99=1"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS card_catalog_revision (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            epoch TEXT NOT NULL,
            revision INTEGER NOT NULL
        )""")
    conn.execute(
        "INSERT OR IGNORE INTO card_catalog_revision(id, epoch, revision) "
        "VALUES (1, ?, 1)",
        (uuid.uuid4().hex,),
    )
    for event in ("INSERT", "UPDATE", "DELETE"):
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS card_catalog_rev_{event.lower()} "
            f"AFTER {event} ON cards BEGIN "
            "UPDATE card_catalog_revision SET revision = revision + 1 "
            "WHERE id = 1; END"
        )
    conn.execute("""CREATE TABLE IF NOT EXISTS _schema_version (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT
        )""")
    conn.execute(
        "INSERT OR IGNORE INTO _schema_version(version, description) "
        "VALUES ('card-search-v1', "
        "'Indexed normalized card names for commander-legal search')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO _schema_version(version, description) "
        "VALUES ('card-search-v2', "
        "'Durable card catalog revision for search cache invalidation')"
    )


def unique_legal_faces_sql(
    where_sql: str,
    *,
    columns: str = _RESEARCH_COLUMNS,
    extra_columns: str = "",
) -> str:
    """Windowed preferred legal printing per name. ``where_sql`` must include legality."""
    extra = f", {extra_columns}" if extra_columns else ""
    return (
        f"SELECT {columns}{extra} FROM ("
        f"SELECT {columns}{extra}, "
        "ROW_NUMBER() OVER (PARTITION BY c.name "
        "ORDER BY c.image_uri IS NULL, c.id) AS _print_rn "
        f"FROM cards c WHERE {where_sql}"
        ") AS c WHERE c._print_rn=1"
    )


def catalog_load_sql() -> str:
    return unique_legal_faces_sql(
        format_legal_sql("c"),
        columns=_SEARCH_COLUMNS,
        extra_columns="c.name_search",
    )


def catalog_plan(conn: sqlite3.Connection) -> str:
    ensure_card_search_schema(conn)
    rows = conn.execute("EXPLAIN QUERY PLAN " + catalog_load_sql()).fetchall()
    return " | ".join(str(row[-1]) for row in rows)


def reset_search_catalog_cache(db_key: str | None = None) -> None:
    with _CATALOG_LOCK:
        if db_key is None:
            _CATALOGS.clear()
        else:
            _CATALOGS.pop(str(db_key), None)


def _file_id(db_key: str) -> tuple[int, int]:
    try:
        stat = os.stat(db_key)
    except OSError:
        return (0, 0)
    return (int(stat.st_dev), int(stat.st_ino))


def _catalog_stamp(conn: sqlite3.Connection, db_key: str) -> _CatalogStamp:
    try:
        row = conn.execute(
            "SELECT epoch, revision FROM card_catalog_revision WHERE id=1"
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None:
        ensure_card_search_schema(conn)
        row = conn.execute(
            "SELECT epoch, revision FROM card_catalog_revision WHERE id=1"
        ).fetchone()
    if row is None:
        return (_file_id(db_key), "", 0)
    epoch = str(row[0])
    revision = int(row[1] or 0)
    return (_file_id(db_key), epoch, revision)


def _parse_identity(value: Any) -> tuple[list[str], bool]:
    """Return (colors, known). Unknown/malformed data is not treated as colorless."""
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return [str(item) for item in value], True
        return [], False
    if not isinstance(value, str) or not value:
        return [], False
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return [], False
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) for item in parsed
    ):
        return [], False
    return [str(item) for item in parsed], True


def _load_catalog(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cursor = conn.execute(catalog_load_sql())
    names = [column[0] for column in cursor.description]
    faces: list[dict[str, Any]] = []
    for row in cursor:
        card = dict(zip(names, row))
        card["mana_value"] = card.pop("cmc", None)
        colors, known = _parse_identity(card.get("color_identity"))
        card["color_identity"] = colors
        card["_colors_known"] = known
        needle = str(card.get("name_search") or "")
        if not needle:
            needle = search_needle(str(card.get("name") or ""))
        card["name_search"] = needle
        card["_folded"] = needle.casefold()
        faces.append(card)
    return faces


def _catalog_faces(conn: sqlite3.Connection, db_key: str) -> list[dict[str, Any]]:
    key = str(db_key)
    stamp = _catalog_stamp(conn, key)
    with _CATALOG_LOCK:
        cached = _CATALOGS.get(key)
        if cached is not None and cached[0] == stamp:
            _CATALOGS.move_to_end(key)
            return cached[1]
    faces = _load_catalog(conn)
    with _CATALOG_LOCK:
        _CATALOGS[key] = (stamp, faces)
        _CATALOGS.move_to_end(key)
        while len(_CATALOGS) > _MAX_CATALOGS:
            _CATALOGS.popitem(last=False)
    return faces


def _identity_ok(card: dict[str, Any], allowed: set[str] | None) -> bool:
    if allowed is None:
        return True
    if not card.get("_colors_known"):
        return False
    colors = set(card["color_identity"])
    return colors.issubset(set("WUBRG")) and colors.issubset(allowed)


def search_cards(
    conn: sqlite3.Connection,
    *,
    db_key: str,
    query: str = "",
    commander_only: bool = False,
    oracle_text: str = "",
    type_line: str = "",
    mana_max: float | None = None,
    rarity: str = "",
    allowed_colors: set[str] | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Unique commander-legal faces, ranked exact/prefix then name."""
    needle = search_needle(query)
    if str(query).strip() and not needle:
        return []
    folded = needle.casefold()
    raw_prefix = str(query).casefold()
    oracle_fold = oracle_text.casefold()
    type_fold = type_line.casefold()
    rarity_fold = rarity.casefold()
    cap = max(1, min(int(limit or 40), 100))
    matches: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
    for card in _catalog_faces(conn, db_key):
        if commander_only and not card.get("is_legal_commander"):
            continue
        name_fold = card["_folded"]
        if folded and folded not in name_fold:
            continue
        if (
            oracle_fold
            and oracle_fold not in str(card.get("oracle_text") or "").casefold()
        ):
            continue
        if type_fold and type_fold not in str(card.get("type_line") or "").casefold():
            continue
        if mana_max is not None:
            try:
                if float(card.get("mana_value") or 0) > float(mana_max):
                    continue
            except (TypeError, ValueError):
                continue
        if rarity_fold and str(card.get("rarity") or "").casefold() != rarity_fold:
            continue
        if not _identity_ok(card, allowed_colors):
            continue
        prefix = 0 if folded and name_fold.startswith(folded) else 1
        raw = (
            0
            if raw_prefix and str(card["name"]).casefold().startswith(raw_prefix)
            else 1
        )
        matches.append(((prefix, raw, str(card["name"]).casefold()), card))
    matches.sort(key=lambda item: item[0])
    results: list[dict[str, Any]] = []
    for _, card in matches[:cap]:
        item = {
            key: card[key]
            for key in (
                "id",
                "oracle_id",
                "name",
                "type_line",
                "mana_cost",
                "mana_value",
                "oracle_text",
                "color_identity",
                "image_uri",
                "rarity",
            )
        }
        results.append(item)
    return results
