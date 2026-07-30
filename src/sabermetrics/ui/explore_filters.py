"""Build the Explore-page SQL from request args (P3).

A pure function that turns query parameters into a parameterized WHERE clause,
ORDER BY, and LIMIT/OFFSET over the ``commander_candidates`` view. Kept separate
from the route so the filter logic is unit-testable without a database.

Columns referenced are guaranteed to exist on any database: base ``cards``
columns (``name``, ``color_identity``, ``keywords``, ``cmc``) plus ``price_usd``
from the view. ``role_tags`` is deliberately NOT referenced — it is added at
runtime by the role tagger and is absent on freshly created databases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

WUBRG = ["W", "U", "B", "R", "G"]

# Curated filterable abilities, matched against the Scryfall ``keywords`` JSON.
# Values are spelled exactly as Scryfall stores them.
ABILITY_OPTIONS = [
    "Flying",
    "First strike",
    "Double strike",
    "Deathtouch",
    "Lifelink",
    "Trample",
    "Menace",
    "Vigilance",
    "Haste",
    "Reach",
    "Ward",
    "Hexproof",
    "Indestructible",
    "Flash",
    "Prowess",
    "Cascade",
    "Convoke",
    "Defender",
]
_ABILITY_SET = set(ABILITY_OPTIONS)

COLOR_MODES = ("atmost", "exactly")
DEFAULT_COLOR_MODE = "atmost"

# sort key -> ORDER BY fragment
SORTS = {
    "name": "name COLLATE NOCASE ASC",
    "price_asc": "price_usd IS NULL, price_usd ASC, name COLLATE NOCASE ASC",
    "price_desc": "price_usd IS NULL, price_usd DESC, name COLLATE NOCASE ASC",
    "cmc_asc": "cmc ASC, name COLLATE NOCASE ASC",
    "cmc_desc": "cmc DESC, name COLLATE NOCASE ASC",
}
DEFAULT_SORT = "name"

DEFAULT_PER_PAGE = 24


class _Args(Protocol):
    """Minimal interface satisfied by werkzeug's MultiDict."""

    def get(self, key: str, default: str | None = ...) -> str | None: ...
    def getlist(self, key: str) -> list[str]: ...


@dataclass
class ExploreQuery:
    """A built Explore query plus the echoed filter state for the template."""

    where_sql: str
    params: list
    order_sql: str
    limit: int
    offset: int
    page: int
    per_page: int
    # echoed, normalized filter state
    q: str = ""
    colors: list[str] = field(default_factory=list)
    color_mode: str = DEFAULT_COLOR_MODE
    abilities: list[str] = field(default_factory=list)
    price_min: float | None = None
    price_max: float | None = None
    cmc_min: float | None = None
    cmc_max: float | None = None
    sort: str = DEFAULT_SORT


def _num(raw: str | None) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _int(raw: str | None, default: int) -> int:
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _like(value: str) -> str:
    """LIKE pattern matching a JSON-array element, e.g. ``["R","G"]`` for R."""
    return f'%"{value}"%'


def build_explore_query(args: _Args, *, per_page: int = DEFAULT_PER_PAGE) -> ExploreQuery:
    """Translate request args into a parameterized Explore query.

    Args:
        args: A werkzeug ``MultiDict`` (``request.args``).
        per_page: Page size for pagination.

    Returns:
        An :class:`ExploreQuery` with SQL fragments, params, and echoed state.
    """
    clauses: list[str] = []
    params: list = []

    # Text search on name
    q = (args.get("q", "") or "").strip()
    if q:
        clauses.append("name LIKE ?")
        params.append(f"%{q}%")

    # Colors
    colors = [c for c in args.getlist("color") if c in WUBRG]
    color_mode = args.get("color_mode", DEFAULT_COLOR_MODE)
    if color_mode not in COLOR_MODES:
        color_mode = DEFAULT_COLOR_MODE
    absent = [c for c in WUBRG if c not in colors]
    if color_mode == "exactly":
        # identity must equal the selected set (empty selection => colorless)
        for c in colors:
            clauses.append("color_identity LIKE ?")
            params.append(_like(c))
        for c in absent:
            clauses.append("color_identity NOT LIKE ?")
            params.append(_like(c))
    else:  # atmost: castable within the selected colors (subset)
        if colors:
            for c in absent:
                clauses.append("color_identity NOT LIKE ?")
                params.append(_like(c))

    # Abilities (matched against Scryfall keywords JSON; all must be present)
    abilities = [a for a in args.getlist("ability") if a in _ABILITY_SET]
    for a in abilities:
        clauses.append("keywords LIKE ?")
        params.append(_like(a))

    # Price range (NULL prices are excluded by the comparison)
    price_min = _num(args.get("price_min"))
    price_max = _num(args.get("price_max"))
    if price_min is not None:
        clauses.append("price_usd >= ?")
        params.append(price_min)
    if price_max is not None:
        clauses.append("price_usd <= ?")
        params.append(price_max)

    # CMC range
    cmc_min = _num(args.get("cmc_min"))
    cmc_max = _num(args.get("cmc_max"))
    if cmc_min is not None:
        clauses.append("cmc >= ?")
        params.append(cmc_min)
    if cmc_max is not None:
        clauses.append("cmc <= ?")
        params.append(cmc_max)

    # Sort + pagination
    sort = args.get("sort", DEFAULT_SORT)
    if sort not in SORTS:
        sort = DEFAULT_SORT
    order_sql = SORTS[sort]

    page = max(1, _int(args.get("page"), 1))
    offset = (page - 1) * per_page

    where_sql = " AND ".join(clauses) if clauses else "1=1"

    return ExploreQuery(
        where_sql=where_sql,
        params=params,
        order_sql=order_sql,
        limit=per_page,
        offset=offset,
        page=page,
        per_page=per_page,
        q=q,
        colors=colors,
        color_mode=color_mode,
        abilities=abilities,
        price_min=price_min,
        price_max=price_max,
        cmc_min=cmc_min,
        cmc_max=cmc_max,
        sort=sort,
    )
