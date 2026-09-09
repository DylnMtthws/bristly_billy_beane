"""Deprecated re-export. This module was SPLIT, not moved.

The two halves answer different questions and now live apart:

- :mod:`sabermetrics.mechanics.oracle_keywords` — extraction. "Does this text
  reference this keyword?" An auditable predicate over oracle text, stdlib-only,
  and the half the Research Assistant builds on.
- :mod:`sabermetrics.analytics.keyword_scoring` — graded matching. "How much is
  that worth?" Carries hand-tuned 1.0/0.5/0.35 weights and a ``cmc >= 5``
  threshold built for :mod:`sabermetrics.analytics.cvar`'s casual/budget
  objective, which is not the cEDH objective.

That boundary is the whole point of the split: a weight in a pure package
becomes an unexamined judgement that every later caller inherits.

New code must import from one of those two modules directly, and must choose
which one it means. This file exists only so the legacy casual generator keeps
working unchanged.
"""

from sabermetrics.analytics.keyword_scoring import (
    _grants_durably,
    card_matches_referenced_keywords,
    referenced_match_strength,
)
from sabermetrics.mechanics.oracle_keywords import (
    _GRANT_PATTERNS,
    _MECHANIC_REFERENCE_PATTERNS,
    _REFERENCE_PATTERNS,
    MTG_KEYWORD_ABILITIES,
    _extract_keywords,
    extract_granted_keywords,
    extract_referenced_keywords,
    extract_referenced_mechanics,
)

__all__ = [
    "MTG_KEYWORD_ABILITIES",
    "_GRANT_PATTERNS",
    "_MECHANIC_REFERENCE_PATTERNS",
    "_REFERENCE_PATTERNS",
    "_extract_keywords",
    "_grants_durably",
    "card_matches_referenced_keywords",
    "extract_granted_keywords",
    "extract_referenced_keywords",
    "extract_referenced_mechanics",
    "referenced_match_strength",
]
