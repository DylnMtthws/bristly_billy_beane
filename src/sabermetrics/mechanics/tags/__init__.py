"""Mechanic tags: auditable predicates over what a card's text says it does.

A tag is a claim ("this card can be cast for an alternative cost"), a predicate
that decides it, the hand-labelled cards it was measured on, and a required
statement of what it misses. It is not a score. Whether a mechanic is *worth*
anything is a separate question, answered elsewhere against a stated objective —
keeping the two apart is what lets the same tag corpus serve a question about
the field and a question about a specific list without smuggling one objective
into the other.

Layout follows the plan: one file per family, all sharing one predicate algebra.

* :mod:`~sabermetrics.mechanics.tags.predicates` — the AST and ``CardView``
* :mod:`~sabermetrics.mechanics.tags.definitions` — ``TagDefinition`` and the
  rules a tag must satisfy to ship
* :mod:`~sabermetrics.mechanics.tags.cost` / ``.mana`` — the R1 families
* :mod:`~sabermetrics.mechanics.tags.registry` — the shipped library
"""

from sabermetrics.mechanics.tags.definitions import (
    MINIMUM_FIXTURES,
    PRECISION_FLOOR,
    TagDefinition,
    TagMatch,
    library_sha256,
)
from sabermetrics.mechanics.tags.predicates import (
    AllOf,
    AnyOf,
    CardView,
    Cost,
    Evidence,
    FaceView,
    Keyword,
    ManaValue,
    MatchedSpan,
    Not,
    Predicate,
    Text,
    TypeLine,
    always_yields_span,
)
from sabermetrics.mechanics.tags.registry import (
    ALL_TAGS,
    SHIPPED_FAMILIES,
    TAG_LIBRARY_SHA256,
    by_family,
    by_id,
)

__all__ = [
    "ALL_TAGS",
    "MINIMUM_FIXTURES",
    "PRECISION_FLOOR",
    "SHIPPED_FAMILIES",
    "TAG_LIBRARY_SHA256",
    "AllOf",
    "AnyOf",
    "CardView",
    "Cost",
    "Evidence",
    "FaceView",
    "Keyword",
    "ManaValue",
    "MatchedSpan",
    "Not",
    "Predicate",
    "TagDefinition",
    "TagMatch",
    "Text",
    "TypeLine",
    "always_yields_span",
    "by_family",
    "by_id",
    "library_sha256",
]
