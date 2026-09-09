"""Deprecated re-export. Moved to :mod:`sabermetrics.mechanics.theme_patterns`.

Pure theme pattern matching. No config, no database, no weights.

New code must import from ``sabermetrics.mechanics.theme_patterns`` directly. This file
exists only so the legacy casual generator keeps working unchanged; the names
below are re-exported and nothing else lives here.
"""

from sabermetrics.mechanics.theme_patterns import (
    COMPILED_THEME_PATTERNS,
    THEME_PATTERNS,
    classify_dominant_theme,
    compute_deck_theme_vector,
    count_theme_cards,
)

__all__ = [
    "COMPILED_THEME_PATTERNS",
    "THEME_PATTERNS",
    "classify_dominant_theme",
    "compute_deck_theme_vector",
    "count_theme_cards",
]
