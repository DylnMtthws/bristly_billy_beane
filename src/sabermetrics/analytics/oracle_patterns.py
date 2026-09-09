"""Deprecated re-export. Moved to :mod:`sabermetrics.mechanics.oracle_patterns`.

Pure oracle-text pattern matching. No config, no database, no weights.

New code must import from ``sabermetrics.mechanics.oracle_patterns`` directly. This file
exists only so the legacy casual generator keeps working unchanged; the names
below are re-exported and nothing else lives here.
"""

from sabermetrics.mechanics.oracle_patterns import (
    BOARD_WIPE,
    COMBAT_GATED,
    DRAW,
    FIXING,
    PROTECTION,
    RAMP,
    RECURSION,
    REMOVAL,
    ROLE_PATTERNS,
    THREAT,
    TUTOR,
    WINCON,
    _compile,
    is_combat_gated,
)

__all__ = [
    "BOARD_WIPE",
    "COMBAT_GATED",
    "DRAW",
    "FIXING",
    "PROTECTION",
    "RAMP",
    "RECURSION",
    "REMOVAL",
    "ROLE_PATTERNS",
    "THREAT",
    "TUTOR",
    "WINCON",
    "_compile",
    "is_combat_gated",
]
