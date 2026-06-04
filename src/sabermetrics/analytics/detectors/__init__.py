"""Shared engine for oracle-text candidate detectors.

The ramp, removal, and protection detectors all share one shape: strip
parenthetical reminder text, reject on negative patterns, require a positive
pattern, extract a metadata dict, and populate a pre-scored ``*_candidates``
SQLite table. :mod:`sabermetrics.analytics.detectors.base` factors that shape
into a single parameterized engine; each detector module supplies only its
patterns, field extraction, and table layout.
"""

from sabermetrics.analytics.detectors.base import (
    Detector,
    populate_candidates,
    run_detect,
    strip_reminder_text,
)

__all__ = [
    "Detector",
    "populate_candidates",
    "run_detect",
    "strip_reminder_text",
]
