"""Effective mana cost calculator for alternative casting costs.

Parses oracle text for alternative casting methods (morph, evoke, dash,
madness, unearth, disguise, megamorph) and returns the minimum effective
CMC a card can be deployed for.

Pure-function module (no DB access), following oracle_keywords.py conventions:
module-level compiled regexes, deterministic outputs.
"""

import re

# ---------------------------------------------------------------------------
# Mana cost string parser
# ---------------------------------------------------------------------------

# Matches mana symbols like {2}, {B}, {W/U}, {X}, {C}
_MANA_SYMBOL_RE = re.compile(r"\{([^}]+)\}")


def _parse_mana_cost_cmc(cost_str: str) -> float:
    """Convert a mana cost string to CMC.

    Args:
        cost_str: Mana cost like "{2}{B}{B}" or "{1}{R}" or "{X}{G}".

    Returns:
        Converted mana value as float. X counts as 0.
    """
    total = 0.0
    for match in _MANA_SYMBOL_RE.finditer(cost_str):
        symbol = match.group(1).upper()
        if symbol == "X":
            continue  # X = 0 for CMC purposes
        try:
            total += float(symbol)
        except ValueError:
            # Single color symbol (W, U, B, R, G, C) or hybrid (W/U)
            if "/" in symbol:
                # Hybrid: each half is 1 mana; CMC counts the higher
                # Per rules: hybrid {W/U} contributes 1 to CMC
                total += 1.0
            else:
                total += 1.0
    return total


# ---------------------------------------------------------------------------
# Alternative cost extraction
# ---------------------------------------------------------------------------

# Morph/Megamorph/Disguise — always costs {3} face-down (game rule)
_FACE_DOWN_RE = re.compile(
    r"\b(?:morph|megamorph|disguise)\b",
    re.IGNORECASE,
)

# Evoke cost: "Evoke {cost}" or "Evoke—{cost}"
_EVOKE_RE = re.compile(
    r"\bevoke\s*[—\-]?\s*(\{[^}]+\}(?:\{[^}]+\})*)",
    re.IGNORECASE,
)

# Dash cost: "Dash {cost}"
_DASH_RE = re.compile(
    r"\bdash\s+(\{[^}]+\}(?:\{[^}]+\})*)",
    re.IGNORECASE,
)

# Madness cost: "Madness {cost}"
_MADNESS_RE = re.compile(
    r"\bmadness\s+(\{[^}]+\}(?:\{[^}]+\})*)",
    re.IGNORECASE,
)

# Unearth cost: "Unearth {cost}"
_UNEARTH_RE = re.compile(
    r"\bunearth\s+(\{[^}]+\}(?:\{[^}]+\})*)",
    re.IGNORECASE,
)


def parse_alternative_costs(oracle_text: str | None) -> list[dict]:
    """Extract alternative casting costs from oracle text.

    Args:
        oracle_text: Card's oracle text (may be None).

    Returns:
        List of dicts with keys "method" and "cmc".
    """
    if not oracle_text:
        return []

    results: list[dict] = []

    # Face-down mechanics (morph/megamorph/disguise) always cost {3}
    if _FACE_DOWN_RE.search(oracle_text):
        results.append({"method": "face_down", "cmc": 3.0})

    # Evoke
    match = _EVOKE_RE.search(oracle_text)
    if match:
        results.append({
            "method": "evoke",
            "cmc": _parse_mana_cost_cmc(match.group(1)),
        })

    # Dash
    match = _DASH_RE.search(oracle_text)
    if match:
        results.append({
            "method": "dash",
            "cmc": _parse_mana_cost_cmc(match.group(1)),
        })

    # Madness
    match = _MADNESS_RE.search(oracle_text)
    if match:
        results.append({
            "method": "madness",
            "cmc": _parse_mana_cost_cmc(match.group(1)),
        })

    # Unearth
    match = _UNEARTH_RE.search(oracle_text)
    if match:
        results.append({
            "method": "unearth",
            "cmc": _parse_mana_cost_cmc(match.group(1)),
        })

    return results


def compute_effective_cmc(card: dict) -> float:
    """Compute the minimum effective CMC considering alternative costs.

    Returns the minimum of printed CMC and any alternative casting costs.
    If no alternative costs exist, returns the printed CMC.

    Args:
        card: Card dict with "cmc" and "oracle_text" fields.

    Returns:
        Effective CMC as float (always >= 0).
    """
    printed_cmc = float(card.get("cmc", 0))
    oracle_text = card.get("oracle_text")

    alternatives = parse_alternative_costs(oracle_text)
    if not alternatives:
        return printed_cmc

    min_alt = min(alt["cmc"] for alt in alternatives)
    return min(printed_cmc, min_alt)
