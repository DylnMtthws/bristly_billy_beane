"""What a mechanic tag *is*, and what it must carry before it may ship.

A tag is not a regex with a name. It is a claim about card text, and this module
makes the claim accountable: it names what it misses, it records the precision
it measured on hand-labelled cards, and it cannot be constructed without either.

Two of these rules are borrowed deliberately from the simulator's card model:

* **A tag below the precision floor does not ship.** ``0.95`` is the plan's
  number and it is enforced in ``__post_init__``, not in review.
* **A tag with an empty ``limitations`` field does not ship**, for the same
  reason an explanation with no weaknesses is marketing copy.

``confidence`` is the third, and the one most likely to be misread, so it is
named for what it is: the tag's measured precision on its own fixtures. It is
**not** a per-card probability. A card carrying ``cost:phyrexian_mana`` with
confidence ``1.0`` is not certainly right in general; it is right on every
fixture the tag was measured against, and ``limitations`` says where that
measurement stops. ``tests/test_mechanic_tags.py`` recomputes the number from
the fixtures and fails if a definition's declared value has drifted, so it
cannot become a claim nobody checks.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sabermetrics.mechanics.tags.predicates import (
    CardView,
    MatchedSpan,
    Predicate,
    always_yields_span,
)

#: The plan's threshold. A family may set a higher bar; none may set a lower one.
PRECISION_FLOOR = 0.95

#: Minimum hand-labelled fixtures per tag, positives and negatives together.
MINIMUM_FIXTURES = 20

#: ``family:name``. The five families are fixed by the plan; ``cost`` and
#: ``mana`` ship at R1 and the rest land through R2-R5.
TAG_ID = re.compile(r"^(cost|mana|draw|interact|win):[a-z][a-z0-9_]*$")

#: ``major.minor``. Bumped when a predicate changes what it matches.
TAG_VERSION = re.compile(r"^\d+\.\d+$")


@dataclass(frozen=True)
class TagMatch:
    """A card carries a tag, and this is where and why."""

    tag_id: str
    tag_version: str
    confidence: float
    spans: tuple[MatchedSpan, ...]

    @property
    def matched_span(self) -> MatchedSpan:
        """The primary span. Never absent — see ``always_yields_span``."""
        return self.spans[0]


@dataclass(frozen=True)
class TagDefinition:
    """One versioned, fixture-measured claim about card text.

    Args:
        id: ``family:name``, e.g. ``cost:phyrexian_mana``.
        version: ``major.minor``; bump when the predicate's meaning changes.
        description: What the tag asserts, in a sentence a player would accept.
        limitations: What it misses. Required, and checked for length, because
            an empty one is the failure this field exists to prevent.
        predicate: The AST. Must produce a span on every satisfying path.
        positive_fixtures: Card names that must carry the tag.
        negative_fixtures: Card names that must not. Choose near misses — a
            negative fixture that no plausible predicate would match measures
            nothing.
        confidence: Measured precision on those fixtures. See the module
            docstring; this is not a per-card probability.
    """

    id: str
    version: str
    description: str
    limitations: str
    predicate: Predicate
    positive_fixtures: tuple[str, ...]
    negative_fixtures: tuple[str, ...]
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not TAG_ID.match(self.id):
            raise ValueError(f"tag id must be family:name, got {self.id!r}")
        if not TAG_VERSION.match(self.version):
            raise ValueError(f"tag version must be major.minor, got {self.version!r}")
        if len(self.description.strip()) < 20:
            raise ValueError(f"{self.id}: description is too short to be a claim")
        if len(self.limitations.strip()) < 20:
            raise ValueError(
                f"{self.id}: limitations is required and must state what the tag "
                "misses; a tag that claims no weakness does not ship"
            )
        if not always_yields_span(self.predicate):
            raise ValueError(
                f"{self.id}: predicate can be satisfied without a matched span; "
                "every tag row must be able to point at the text it came from"
            )
        total = len(self.positive_fixtures) + len(self.negative_fixtures)
        if total < MINIMUM_FIXTURES:
            raise ValueError(
                f"{self.id}: {total} fixtures, need at least {MINIMUM_FIXTURES}"
            )
        if not self.positive_fixtures or not self.negative_fixtures:
            raise ValueError(f"{self.id}: needs both positive and negative fixtures")
        overlap = sorted(set(self.positive_fixtures) & set(self.negative_fixtures))
        if overlap:
            raise ValueError(
                f"{self.id}: fixtures both positive and negative: {overlap}"
            )
        for label, names in (
            ("positive", self.positive_fixtures),
            ("negative", self.negative_fixtures),
        ):
            duplicates = sorted({n for n in names if list(names).count(n) > 1})
            if duplicates:
                raise ValueError(f"{self.id}: duplicate {label} fixtures: {duplicates}")
        if not PRECISION_FLOOR <= self.confidence <= 1.0:
            raise ValueError(
                f"{self.id}: confidence {self.confidence} is below the "
                f"{PRECISION_FLOOR} precision floor; the tag does not ship"
            )

    @property
    def family(self) -> str:
        """The part before the colon: ``cost``, ``mana``, ..."""
        return self.id.split(":", 1)[0]

    def evaluate(self, card: CardView) -> TagMatch | None:
        """Apply the predicate to one card.

        Args:
            card: The card's published text.

        Returns:
            A :class:`TagMatch` carrying at least one span, or ``None``.
        """
        evidence = self.predicate.evaluate(card)
        if evidence is None:
            return None
        if not evidence.spans:  # pragma: no cover - blocked by __post_init__
            raise AssertionError(f"{self.id}: matched with no span")
        return TagMatch(
            tag_id=self.id,
            tag_version=self.version,
            confidence=self.confidence,
            spans=evidence.spans,
        )

    def signature(self) -> str:
        """Canonical, hashable form. Changing any of this changes the build."""
        return "|".join(
            (
                self.id,
                self.version,
                f"{self.confidence:.6f}",
                self.predicate.signature(),
            )
        )


def library_sha256(definitions: tuple[TagDefinition, ...]) -> str:
    """Content hash of a tag library.

    Args:
        definitions: The tags, in any order; the hash sorts them.

    Returns:
        Hex sha256 over each tag's signature. Two builds agree iff the tag
        definitions agree, which is what makes a rebuild's output comparable
        rather than merely re-derived.
    """
    payload = "\n".join(sorted(d.signature() for d in definitions))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
