"""Before/after harness for the oracle-pattern unification.

Classifies the shared corpus through both components.py (per-card predicates
mirroring the count_* functions) and role_tagger.tag_card_roles, emitting a
stable JSON blob so the behavior delta from unifying the patterns is visible.

Run:  python -m tests._oracle_harness
"""

from __future__ import annotations

import json
import re

from sabermetrics.analytics import components as comp
from sabermetrics.analytics.role_tagger import tag_card_roles
from tests.oracle_corpus import CORPUS

_BASIC_LAND_SEARCH = re.compile(
    r"search your library for a (?:basic )?land", re.IGNORECASE
)


def _components_flags(card: dict) -> dict[str, bool]:
    """Per-card booleans mirroring the count_* predicates in components.py."""
    oracle = (card.get("oracle_text") or "").lower()
    is_land = comp._is_land(card)
    tutor = (
        not is_land
        and comp._matches_any(oracle, comp._TUTOR_RE)
        and not _BASIC_LAND_SEARCH.search(oracle)
    )
    return {
        "ramp": (not is_land) and comp._is_ramp(card),
        "draw": (not is_land) and comp._matches_any(oracle, comp._DRAW_RE),
        "removal": (not is_land) and comp._matches_any(oracle, comp._REMOVAL_RE),
        "board_wipe": comp._matches_any(oracle, comp._WIPE_RE),
        "tutor": tutor,
    }


def snapshot() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key, card in CORPUS:
        out[key] = {
            "components": _components_flags(card),
            "role_tags": sorted(tag_card_roles(card).role_tags),
        }
    return out


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2, sort_keys=True))
