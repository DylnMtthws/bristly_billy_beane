"""A small predicate algebra over a card's printed text.

The plan calls for "regex or small AST over oracle text, mana cost and type
line". This is the AST, and it exists rather than a bare regex for three
reasons that a regex cannot supply:

1. **Negative gating.** Most useful mechanic tags are "matches A and not B".
   Expressing that in one regex is possible and unreadable; expressing it as
   ``AllOf(Text(a), Not(Text(b)))`` is neither.
2. **Fields.** A cost lives in the mana cost, a type constraint in the type
   line, and a mechanic in the oracle text — including the oracle text of a
   *face*, because a modal double-faced card publishes no card-level text.
3. **Spans.** Every match carries where it matched, which is what lets an
   interface show *why* a card carries a tag. A tag whose predicate can be
   satisfied without producing a span is rejected at import time by
   :func:`always_yields_span`, so ``matched_span`` is structurally non-optional
   rather than optional-with-a-convention.

Nothing here scores, weights, or ranks. A predicate answers "does this card's
text say this", and what that is worth is a different question answered
elsewhere against a stated objective.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sabermetrics.mechanics.text import mask_reminder_text

#: Field labels a span can name. ``faces[i].<field>`` is generated per face.
CARD_FIELDS = ("oracle_text", "type_line", "mana_cost", "keywords")


@dataclass(frozen=True)
class FaceView:
    """One face of a multi-faced card, mirroring ``mtg_v1.card_face``."""

    name: str = ""
    mana_cost: str | None = None
    type_line: str | None = None
    oracle_text: str | None = None


@dataclass(frozen=True)
class CardView:
    """Everything a predicate may read about a card.

    Deliberately a subset of :class:`sabermetrics.cedh.repositories.CardFacts`
    with the same field names and the same semantics, so a predicate written
    against the checked-in fixtures behaves identically against
    ``mtg_v1.card_any_medium``. Fields ``mtg_v1`` does not publish are absent on
    purpose: Scryfall's ``produced_mana`` would be convenient and is not
    available in production, and a predicate that used it would pass every
    offline test and tag nothing.

    There is no price field here for the same reason there is none on
    ``CardFacts`` (ADR-025).
    """

    oracle_id: str = ""
    name: str = ""
    layout: str = ""
    mana_cost: str | None = None
    mana_value: float = 0.0
    type_line: str = ""
    oracle_text: str | None = None
    colors: tuple[str, ...] = ()
    color_identity: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    all_types: tuple[str, ...] = ()
    castable_cmcs: tuple[float, ...] = ()
    faces: tuple[FaceView, ...] = ()

    def texts(self, attribute: str) -> tuple[tuple[str, str], ...]:
        """Every readable value of ``attribute``, labelled by where it came from.

        Args:
            attribute: One of ``oracle_text``, ``type_line``, ``mana_cost``.

        Returns:
            ``(field_label, value)`` pairs: the card-level value first when it
            is non-empty, then one per face. Split cards, adventures and modal
            double-faced cards publish no card-level oracle text, so a predicate
            that only read the card level would silently see nothing.
        """
        out: list[tuple[str, str]] = []
        top = getattr(self, attribute)
        if top:
            out.append((attribute, str(top)))
        for index, face in enumerate(self.faces):
            value = getattr(face, attribute, None)
            if value:
                out.append((f"faces[{index}].{attribute}", str(value)))
        return tuple(out)


@dataclass(frozen=True)
class MatchedSpan:
    """Where a predicate matched, in the card's own printed text.

    ``text`` is sliced from the unmasked source, so a span taken from a match
    against reminder-masked text still reads back as what is printed on the
    card.
    """

    field: str
    start: int
    end: int
    text: str

    def as_dict(self) -> dict[str, object]:
        """JSON-ready form, for the ``matched_span`` column."""
        return {
            "field": self.field,
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }


@dataclass(frozen=True)
class Evidence:
    """A successful evaluation: zero or more spans, in evaluation order."""

    spans: tuple[MatchedSpan, ...] = ()


# --- nodes -----------------------------------------------------------------


@dataclass(frozen=True)
class _TextLike:
    """Shared implementation for the three regex-over-a-field nodes."""

    pattern: str
    attribute: str = field(default="oracle_text", init=False)
    #: Reminder text is masked before matching unless a node opts out. See
    #: :func:`sabermetrics.mechanics.text.mask_reminder_text` for why.
    mask_reminders: bool = True

    def signature(self) -> str:
        return f"{type(self).__name__}({self.pattern!r},mask={self.mask_reminders})"

    def evaluate(self, card: CardView) -> Evidence | None:
        regex = _compiled(self.pattern)
        for label, value in card.texts(self.attribute):
            haystack = _masked(value) if self.mask_reminders else value
            match = regex.search(haystack)
            if match:
                return Evidence(
                    (
                        MatchedSpan(
                            field=label,
                            start=match.start(),
                            end=match.end(),
                            text=value[match.start() : match.end()],
                        ),
                    )
                )
        return None


@dataclass(frozen=True)
class Text(_TextLike):
    """Regex over oracle text, card level and every face."""

    attribute: str = field(default="oracle_text", init=False)


@dataclass(frozen=True)
class TypeLine(_TextLike):
    """Regex over the type line, card level and every face."""

    attribute: str = field(default="type_line", init=False)
    mask_reminders: bool = False


@dataclass(frozen=True)
class Cost(_TextLike):
    """Regex over the printed mana cost, card level and every face.

    Costs contain braces, so patterns here escape them: ``r"\\{[WUBRG]/P\\}"``.
    """

    attribute: str = field(default="mana_cost", init=False)
    mask_reminders: bool = False


@dataclass(frozen=True)
class Keyword:
    """Exact membership in the published ``keywords`` array.

    ``mtg_v1`` publishes the same keyword list Scryfall does. Matching it is
    more precise than matching the word in oracle text, which also appears in
    reminder text, in other cards' names and in ability text that grants the
    keyword to something else.
    """

    name: str

    def signature(self) -> str:
        return f"Keyword({self.name!r})"

    def evaluate(self, card: CardView) -> Evidence | None:
        wanted = self.name.casefold()
        offset = 0
        for keyword in card.keywords:
            if keyword.casefold() == wanted:
                return Evidence(
                    (
                        MatchedSpan(
                            field="keywords",
                            start=offset,
                            end=offset + len(keyword),
                            text=keyword,
                        ),
                    )
                )
            offset += len(keyword) + 2
        return None


@dataclass(frozen=True)
class ManaValue:
    """Bound on the card-level mana value. Produces no span, so it cannot stand
    alone in a tag — see :func:`always_yields_span`.

    Reads the card-level value, which for a modal double-faced card is the front
    face's. A tag that leans on this should say so in its ``limitations``.
    """

    minimum: float | None = None
    maximum: float | None = None

    def signature(self) -> str:
        return f"ManaValue(min={self.minimum},max={self.maximum})"

    def evaluate(self, card: CardView) -> Evidence | None:
        if self.minimum is not None and card.mana_value < self.minimum:
            return None
        if self.maximum is not None and card.mana_value > self.maximum:
            return None
        return Evidence(())


@dataclass(frozen=True)
class AllOf:
    """Every child must match. Spans concatenate in declaration order."""

    children: tuple[Predicate, ...]

    def __init__(self, *children: Predicate) -> None:
        object.__setattr__(self, "children", tuple(children))

    def signature(self) -> str:
        return "AllOf(" + ",".join(c.signature() for c in self.children) + ")"

    def evaluate(self, card: CardView) -> Evidence | None:
        spans: list[MatchedSpan] = []
        for child in self.children:
            evidence = child.evaluate(card)
            if evidence is None:
                return None
            spans.extend(evidence.spans)
        return Evidence(tuple(spans))


@dataclass(frozen=True)
class AnyOf:
    """The first matching child wins, in declaration order.

    Order is part of the definition, not an optimisation: it decides which span
    a card's tag row carries, and a rebuild that reordered the children would
    change the content hash. That is the intended behaviour — the explanation
    changed.
    """

    children: tuple[Predicate, ...]

    def __init__(self, *children: Predicate) -> None:
        object.__setattr__(self, "children", tuple(children))

    def signature(self) -> str:
        return "AnyOf(" + ",".join(c.signature() for c in self.children) + ")"

    def evaluate(self, card: CardView) -> Evidence | None:
        for child in self.children:
            evidence = child.evaluate(card)
            if evidence is not None:
                return evidence
        return None


@dataclass(frozen=True)
class Not:
    """Matches when its child does not. Contributes no span, by construction."""

    child: Predicate

    def signature(self) -> str:
        return f"Not({self.child.signature()})"

    def evaluate(self, card: CardView) -> Evidence | None:
        return None if self.child.evaluate(card) is not None else Evidence(())


Predicate = Text | TypeLine | Cost | Keyword | ManaValue | AllOf | AnyOf | Not


def always_yields_span(node: Predicate) -> bool:
    """Whether *every* way of satisfying ``node`` produces at least one span.

    This is what makes ``matched_span`` non-optional. A tag whose predicate can
    be satisfied by a bare :class:`ManaValue` bound, or by a :class:`Not`, would
    produce rows asserting a mechanic with nothing to point at, and an
    unattributable claim is the thing a tag exists not to be.

    Args:
        node: Any predicate.

    Returns:
        True when the node cannot match without a span.
    """
    if isinstance(node, (Text, TypeLine, Cost, Keyword)):
        return True
    if isinstance(node, (ManaValue, Not)):
        return False
    if isinstance(node, AllOf):
        return any(always_yields_span(child) for child in node.children)
    if isinstance(node, AnyOf):
        return bool(node.children) and all(
            always_yields_span(child) for child in node.children
        )
    raise TypeError(f"unknown predicate node: {node!r}")


# --- compiled-pattern cache ------------------------------------------------

#: Patterns are module-level constants in practice; caching keeps a full-corpus
#: rebuild from recompiling the same expression 34,000 times.
_CACHE: dict[str, re.Pattern[str]] = {}
_MASK_CACHE: dict[str, str] = {}


def _compiled(pattern: str) -> re.Pattern[str]:
    compiled = _CACHE.get(pattern)
    if compiled is None:
        compiled = re.compile(pattern, re.IGNORECASE)
        _CACHE[pattern] = compiled
    return compiled


def _masked(value: str) -> str:
    cached = _MASK_CACHE.get(value)
    if cached is None:
        cached = mask_reminder_text(value)
        if len(_MASK_CACHE) < 200_000:
            _MASK_CACHE[value] = cached
    return cached
