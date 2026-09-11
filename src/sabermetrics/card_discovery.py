"""Research card-discovery filters over imported card attributes only."""

from __future__ import annotations

import math
from typing import Any

# Printed super/types used by Research IS/NOT controls. Subtype is free text
# matched against the type line after the dash; nothing is invented.
SUPERTYPES = (
    "Basic",
    "Legendary",
    "Snow",
    "World",
    "Ongoing",
    "Host",
    "Elite",
    "Token",
)
CARD_TYPES = (
    "Artifact",
    "Battle",
    "Conspiracy",
    "Creature",
    "Dungeon",
    "Enchantment",
    "Instant",
    "Kindred",
    "Land",
    "Phenomenon",
    "Plane",
    "Planeswalker",
    "Scheme",
    "Sorcery",
    "Tribal",
    "Vanguard",
)
PRIMARY_TYPES = (
    "Creature",
    "Planeswalker",
    "Battle",
    "Instant",
    "Sorcery",
    "Artifact",
    "Enchantment",
    "Land",
)
RARITIES = ("common", "uncommon", "rare", "mythic")
COLOR_MODES = ("include", "exclude", "exactly", "all", "any", "exact")
TYPE_OPS = ("is", "not")
RANGE_MAX = 10  # labeled 10+; an open upper bucket, not exactly ten
RANGE_LABELS = tuple([str(n) for n in range(10)] + ["10+"])


def normalize_bound(raw: Any) -> int | None:
    """Parse a 0–10 range bound. ``10`` means the open 10+ bucket."""
    if raw in (None, ""):
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    try:
        number = int(value)
    except (OverflowError, ValueError):
        return None
    return max(0, min(number, RANGE_MAX))


def full_range(minimum: int | None, maximum: int | None) -> bool:
    """True when the control is at its default unfiltered span."""
    lo = 0 if minimum is None else minimum
    hi = RANGE_MAX if maximum is None else maximum
    return lo <= 0 and hi >= RANGE_MAX


def numeric_stat_sql(column: str) -> str:
    """SQL that yields a REAL only for numeric printed power/toughness.

    Nonnumeric values such as ``*`` or ``1+*`` stay NULL and never become
    zero. Values above 99 remain numeric so they belong in the 10+ bucket.
    """
    return (
        f"CASE WHEN {column} GLOB '[0-9]*' "
        f"AND {column} NOT GLOB '*[^0-9.]*' "
        f"AND {column} NOT GLOB '*.*.*' "
        f"AND {column} NOT GLOB '.' AND {column} NOT GLOB '*.' "
        f"AND {column} NOT GLOB '.*' "
        f"THEN CAST({column} AS REAL) "
        f"WHEN {column} GLOB '-[0-9]*' "
        f"AND {column} NOT GLOB '-*[^0-9.]*' "
        f"AND {column} NOT GLOB '*.*.*' "
        f"THEN CAST({column} AS REAL) END"
    )


def apply_discrete_range(
    where: list[str],
    params: list[Any],
    expr: str,
    minimum: int | None,
    maximum: int | None,
) -> None:
    """Restrict ``expr`` to the labeled 0–9 / 10+ span. Full span is a no-op."""
    if full_range(minimum, maximum):
        return
    lo = 0 if minimum is None else minimum
    hi = RANGE_MAX if maximum is None else maximum
    where.append(f"({expr}) >= ?")
    params.append(float(lo))
    if hi < RANGE_MAX:
        where.append(f"({expr}) <= ?")
        params.append(float(hi))


def _normalized_type_line(column: str = "c.type_line") -> str:
    return (
        f"REPLACE(REPLACE(REPLACE(COALESCE({column}, ''), "
        "'—', ' - '), '–', ' - '), '//', ' // ')"
    )


def _face_sql(normalized: str, index: int) -> str:
    delimiter = "' // '"
    if index == 0:
        return (
            f"CASE WHEN INSTR({normalized}, {delimiter})>0 "
            f"THEN TRIM(SUBSTR({normalized}, 1, INSTR({normalized}, {delimiter})-1)) "
            f"ELSE TRIM({normalized}) END"
        )
    return (
        f"CASE WHEN INSTR({normalized}, {delimiter})>0 "
        f"THEN TRIM(SUBSTR({normalized}, INSTR({normalized}, {delimiter})+4)) "
        f"ELSE '' END"
    )


def _left_of_dash(face: str) -> str:
    return (
        f"CASE WHEN INSTR({face}, ' - ')>0 "
        f"THEN TRIM(SUBSTR({face}, 1, INSTR({face}, ' - ')-1)) "
        f"ELSE TRIM({face}) END"
    )


def _right_of_dash(face: str) -> str:
    return (
        f"CASE WHEN INSTR({face}, ' - ')>0 "
        f"THEN TRIM(SUBSTR({face}, INSTR({face}, ' - ')+3)) "
        f"ELSE '' END"
    )


def _type_haystack_sql(column: str = "c.type_line", *, subtype: bool = False) -> str:
    """Match super/type tokens only left of the dash; subtypes only right of it."""
    normalized = _normalized_type_line(column)
    face_one = _face_sql(normalized, 0)
    face_two = _face_sql(normalized, 1)
    if subtype:
        combined = (
            f"TRIM({_right_of_dash(face_one)} || ' ' || {_right_of_dash(face_two)})"
        )
    else:
        combined = (
            f"TRIM({_left_of_dash(face_one)} || ' ' || {_left_of_dash(face_two)})"
        )
    return f"(' ' || {combined} || ' ')"


def apply_type_token(
    where: list[str],
    params: list[Any],
    value: str,
    operator: str,
    *,
    allowed: tuple[str, ...] | None = None,
    subtype: bool = False,
) -> None:
    """Match one imported type-line token with IS/NOT. Unknown tokens are ignored."""
    token = " ".join(str(value or "").split())
    if not token:
        return
    if allowed is not None:
        match = next((item for item in allowed if item.lower() == token.lower()), None)
        if match is None:
            return
        token = match
    elif subtype:
        token = token[:40]
    else:
        return
    op = operator if operator in TYPE_OPS else "is"
    haystack = _type_haystack_sql(subtype=subtype)
    clause = f"{haystack} LIKE ? ESCAPE '\\'"
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"% {escaped} %"
    if op == "not":
        where.append(f"NOT ({clause})")
    else:
        where.append(clause)
    params.append(pattern)


def apply_colors(
    where: list[str],
    params: list[Any],
    colors: list[str],
    color_mode: str,
    *,
    alias: str = "c",
) -> None:
    """Apply Include / Exclude / Exactly against stored color identity JSON."""
    ident = f"{alias}.color_identity"
    selected: list[str] = []
    for color in colors:
        if color in set("WUBRGC") and color not in selected:
            selected.append(color)
    mode = {
        "all": "include",
        "exact": "exactly",
        "include": "include",
        "exclude": "exclude",
        "exactly": "exactly",
        "any": "any",
    }.get(color_mode, "include")
    if not selected and mode != "exactly":
        return
    colored = [color for color in selected if color != "C"]
    wants_colorless = "C" in selected
    if mode == "exclude":
        for color in colored:
            where.append(f"{ident} NOT LIKE ?")
            params.append(f'%"{color}"%')
        if wants_colorless:
            where.append(f"json_array_length({ident})>0")
        return
    if mode == "any":
        tests = [f"{ident} LIKE ?" for _ in colored]
        values = [f'%"{color}"%' for color in colored]
        if wants_colorless:
            tests.append(f"json_array_length({ident})=0")
        if tests:
            where.append(f"({' OR '.join(tests)})")
            params.extend(values)
        return
    if wants_colorless and not colored:
        where.append(f"json_array_length({ident})=0")
        return
    for color in colored:
        where.append(f"{ident} LIKE ?")
        params.append(f'%"{color}"%')
    if mode == "exactly":
        where.append(f"json_array_length({ident})=?")
        params.append(len(colored))
        if wants_colorless and colored:
            # Include+colorless together is not a real identity; exact empty only.
            where.append("1=0")


def commander_eligible_sql(alias: str = "c") -> str:
    """Format-legal and commander-eligible. The two flags stay distinct."""
    return f"{alias}.is_legal_in_99=1 AND {alias}.is_legal_commander=1"


def imported_commander_eligible(
    type_line: str, oracle_text: str, power: Any = None, toughness: Any = None
) -> bool:
    """Eligibility from front-face characteristics (CR 903.3, 903.3a).

    Backgrounds can participate in a legal pair; pair validation is separate.
    Format legality must also be affirmative before setting the stored flag.
    """
    front = type_line.split("//", 1)[0].strip()
    text = oracle_text.split("//", 1)[0].casefold()
    if "can be your commander" in text:
        return True
    if "Legendary" not in front.split():
        return False
    tokens = front.replace("—", " ").split()
    return (
        "Creature" in tokens
        or "Vehicle" in tokens
        or ("Spacecraft" in tokens and power is not None and toughness is not None)
        or ("Enchantment" in tokens and "Background" in tokens)
        or ("isn't on the battlefield" in text and "creature in addition" in text)
    )


def format_legal_sql(alias: str = "c") -> str:
    return f"{alias}.is_legal_in_99=1"


def primary_type(type_line: str | None) -> str:
    """Group a card by its first imported card type; never invent a type."""
    head = (type_line or "").split("//")[0]
    left = head.split("—")[0].split("-")[0]
    folded = left.casefold()
    for kind in PRIMARY_TYPES:
        if kind.casefold() in folded:
            return kind
    return "Other"
