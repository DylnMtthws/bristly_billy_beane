"""Deprecated re-export. Moved to :mod:`sabermetrics.mechanics.effective_cost`.

Pure text-to-cost parsing with no config and no database, so it belongs
in the stdlib-only mechanics package the Research Assistant builds on.

New code must import from ``sabermetrics.mechanics.effective_cost`` directly. This file
exists only so the legacy casual generator keeps working unchanged; the names
below are re-exported and nothing else lives here.
"""

from sabermetrics.mechanics.effective_cost import (
    _parse_mana_cost_cmc,
    compute_effective_cmc,
    parse_alternative_costs,
)

__all__ = [
    "_parse_mana_cost_cmc",
    "compute_effective_cmc",
    "parse_alternative_costs",
]
