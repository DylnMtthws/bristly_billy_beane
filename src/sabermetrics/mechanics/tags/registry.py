"""The shipped tag library: every family, checked for the rules that bind all of
them.

Uniqueness, span-producibility and family membership are checked here at import
time rather than in a test, so a definition that breaks a rule cannot be
imported at all. A test can be skipped; an import cannot.

Families ship in the plan's build order. ``cost`` and ``mana`` land at R1;
``draw``, ``interact`` and ``win`` continue in the background and land through
R2-R5. :data:`SHIPPED_FAMILIES` is what the R1 gate checks against, so adding a
family is a deliberate edit here rather than a side effect of adding a file.
"""

from __future__ import annotations

from sabermetrics.mechanics.tags.cost import COST_TAGS
from sabermetrics.mechanics.tags.definitions import TagDefinition, library_sha256
from sabermetrics.mechanics.tags.mana import MANA_TAGS

#: Families with complete coverage as of R1.
SHIPPED_FAMILIES: tuple[str, ...] = ("cost", "mana")

ALL_TAGS: tuple[TagDefinition, ...] = tuple(
    sorted(COST_TAGS + MANA_TAGS, key=lambda d: d.id)
)


def _check() -> None:
    seen: set[str] = set()
    for definition in ALL_TAGS:
        if definition.id in seen:
            raise ValueError(f"duplicate tag id: {definition.id}")
        seen.add(definition.id)
        if definition.family not in SHIPPED_FAMILIES:
            raise ValueError(
                f"{definition.id}: family {definition.family!r} is not shipped; "
                f"add it to SHIPPED_FAMILIES on purpose"
            )


_check()

#: Content hash of the whole library. Changes when any predicate, version or
#: declared confidence changes, and is carried alongside a build so a stored
#: result can be told apart from one produced by a later library.
TAG_LIBRARY_SHA256 = library_sha256(ALL_TAGS)


def by_family(family: str) -> tuple[TagDefinition, ...]:
    """Every shipped tag in one family, in id order.

    Args:
        family: ``cost``, ``mana``, ...

    Returns:
        The tags. Empty for a family that has not shipped yet — which is a
        stated absence, not an error.
    """
    return tuple(d for d in ALL_TAGS if d.family == family)


def by_id(tag_id: str) -> TagDefinition:
    """One tag by id.

    Args:
        tag_id: ``family:name``.

    Returns:
        The definition.

    Raises:
        KeyError: If no such tag ships.
    """
    for definition in ALL_TAGS:
        if definition.id == tag_id:
            return definition
    raise KeyError(f"no such tag: {tag_id}")
