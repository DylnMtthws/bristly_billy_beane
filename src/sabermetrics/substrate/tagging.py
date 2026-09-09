"""Build the mechanic tag corpus, and report what it did not cover.

Three things happen here and they are deliberately separate:

**The build** applies every tag definition to every card and emits one row per
(card, tag). It is a pure function of the corpus and the tag library, so it is
content-hashed: two builds of the same library against the same snapshot
produce byte-identical output and the same ``content_sha256``. A rebuild that
changes the hash changed something, and the hash says so before anyone reads a
diff.

**Coverage** counts what carries no tag at all, grouped by type line. This is
the direct analogue of the simulator's inert table, and it exists for the same
reason: a tag library is narrow in some direction, and the choice is between
stating which direction and letting a reader assume it is narrow in none. It is
printed by the CLI on every build, not stored and forgotten.

**Fixture measurement** scores each definition against its own hand-labelled
cards. This is where the ``0.95`` precision floor is actually enforced against
real text; ``TagDefinition.__post_init__`` can only check that the number a
definition *claims* clears the floor, and :func:`measure` is what stops that
number being fiction.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from sabermetrics.mechanics.tags.definitions import TagDefinition, library_sha256
from sabermetrics.mechanics.tags.predicates import CardView
from sabermetrics.substrate.corpus import CorpusSource, SnapshotIdentity

#: Card types, in the order a type line prints them. Everything else on the
#: line is a supertype or a subtype and is dropped for grouping.
CARD_TYPES = (
    "Artifact",
    "Battle",
    "Creature",
    "Enchantment",
    "Instant",
    "Kindred",
    "Land",
    "Planeswalker",
    "Sorcery",
    "Tribal",
)


@dataclass(frozen=True)
class TagRow:
    """One row of ``card_mechanic_tag``.

    ``matched_span`` is a JSON object, not a bare string, because "why" needs a
    field and an offset to be checkable: a span that says only *what* text
    matched cannot be verified against the card it claims to describe.
    """

    oracle_id: str
    tag_id: str
    tag_version: str
    confidence: float
    matched_span: str
    snapshot_hash: str

    def key(self) -> tuple[str, str]:
        return (self.oracle_id, self.tag_id)

    def digest_line(self) -> str:
        return "|".join(
            (
                self.oracle_id,
                self.tag_id,
                self.tag_version,
                f"{self.confidence:.6f}",
                self.matched_span,
            )
        )


@dataclass(frozen=True)
class CoverageReport:
    """What the tag library did and did not reach."""

    total_cards: int
    tagged_cards: int
    untagged_by_type: tuple[tuple[str, int], ...]
    tag_counts: tuple[tuple[str, int], ...]
    family_counts: tuple[tuple[str, int], ...]

    @property
    def untagged_cards(self) -> int:
        return self.total_cards - self.tagged_cards

    @property
    def tagged_fraction(self) -> float:
        return self.tagged_cards / self.total_cards if self.total_cards else 0.0


@dataclass(frozen=True)
class TagBuild:
    """The output of one rebuild, and everything needed to reproduce it."""

    rows: tuple[TagRow, ...]
    coverage: CoverageReport
    snapshot: SnapshotIdentity
    library_sha256: str
    content_sha256: str
    tag_ids: tuple[str, ...] = field(default=())


def type_group(card: CardView) -> str:
    """The card's types, as a stable grouping key for the coverage report.

    Args:
        card: Any card.

    Returns:
        Card types in printed order, e.g. ``"Artifact Creature"``, or
        ``"(no recognised card type)"``. Supertypes (``Legendary``, ``Snow``)
        and subtypes are dropped: grouping by the raw line would produce
        thousands of groups of one and hide the shape the report exists to show.

        The catch-all group is named for what it is rather than for an empty
        line, because it is not empty — it holds Stickers, Conspiracies,
        Dungeons and pre-modern ``Summon`` templating, which have type lines the
        modern type list does not contain. Calling that "(no type line)" would
        misreport 100 real cards as malformed data.
    """
    lines = [card.type_line] + [f.type_line or "" for f in card.faces]
    found: list[str] = []
    for line in lines:
        head = line.split("—")[0]
        for card_type in CARD_TYPES:
            if card_type in head.split() and card_type not in found:
                found.append(card_type)
    if not found:
        return "(no recognised card type)"
    return " ".join(sorted(found, key=CARD_TYPES.index))


def build(
    source: CorpusSource,
    definitions: Sequence[TagDefinition],
) -> TagBuild:
    """Apply every definition to every card.

    Args:
        source: Where the cards come from, and what to call that.
        definitions: The tag library. Order does not matter; output is sorted.

    Returns:
        A :class:`TagBuild`. ``rows`` is sorted by ``(oracle_id, tag_id)``, so
        the content hash is a property of the tags found, not of the scan order.
    """
    snapshot = source.identity()
    snapshot_hash = snapshot.sha256()
    ordered = sorted(definitions, key=lambda d: d.id)

    rows: list[TagRow] = []
    tag_counts: Counter[str] = Counter()
    untagged: Counter[str] = Counter()
    total = 0
    tagged = 0

    for card in source.iter_cards():
        total += 1
        hits = 0
        for definition in ordered:
            match = definition.evaluate(card)
            if match is None:
                continue
            hits += 1
            tag_counts[definition.id] += 1
            rows.append(
                TagRow(
                    oracle_id=card.oracle_id,
                    tag_id=match.tag_id,
                    tag_version=match.tag_version,
                    confidence=match.confidence,
                    matched_span=json.dumps(
                        match.matched_span.as_dict(),
                        sort_keys=True,
                        ensure_ascii=False,
                    ),
                    snapshot_hash=snapshot_hash,
                )
            )
        if hits:
            tagged += 1
        else:
            untagged[type_group(card)] += 1

    rows.sort(key=TagRow.key)
    families: Counter[str] = Counter()
    for definition in ordered:
        families[definition.family] += tag_counts.get(definition.id, 0)

    coverage = CoverageReport(
        total_cards=total,
        tagged_cards=tagged,
        untagged_by_type=tuple(
            sorted(untagged.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        tag_counts=tuple((d.id, tag_counts.get(d.id, 0)) for d in ordered),
        family_counts=tuple(sorted(families.items())),
    )
    return TagBuild(
        rows=tuple(rows),
        coverage=coverage,
        snapshot=snapshot,
        library_sha256=library_sha256(tuple(ordered)),
        content_sha256=content_sha256(rows),
        tag_ids=tuple(d.id for d in ordered),
    )


def content_sha256(rows: Iterable[TagRow]) -> str:
    """Hash the build's output.

    Args:
        rows: The tag rows, in any order; the hash sorts them.

    Returns:
        Hex sha256 over one canonical line per row. Excludes ``snapshot_hash``,
        which is carried per row but is a property of the corpus rather than of
        what the library found in it — including it would make every nightly
        look like a library change.
    """
    payload = "\n".join(sorted(row.digest_line() for row in rows))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_coverage(report: CoverageReport, *, top: int = 15) -> str:
    """Format the coverage report for a terminal.

    Args:
        report: The report from :func:`build`.
        top: How many untagged type groups to list.

    Returns:
        Plain text. The untagged section is the point of the report, so it is
        printed even when it is long, and a truncated tail says how much it hid.
    """
    lines = [
        f"cards scanned      {report.total_cards}",
        f"carrying >=1 tag   {report.tagged_cards} "
        f"({report.tagged_fraction * 100:.1f}%)",
        f"carrying no tag    {report.untagged_cards}",
        "",
        "rows per family",
    ]
    for family, count in report.family_counts:
        lines.append(f"  {family + ':':<12} {count} rows")
    lines.append("")
    lines.append("rows per tag")
    for tag_id, count in report.tag_counts:
        flag = "   <- matched nothing" if count == 0 else ""
        lines.append(f"  {tag_id:<34} {count}{flag}")
    lines.append("")
    lines.append("untagged cards by type line")
    shown = report.untagged_by_type[:top]
    for group, count in shown:
        lines.append(f"  {group:<34} {count}")
    hidden = len(report.untagged_by_type) - len(shown)
    if hidden > 0:
        remainder = sum(count for _, count in report.untagged_by_type[top:])
        lines.append(f"  ... {hidden} further groups, {remainder} cards")
    return "\n".join(lines)


# --- fixture measurement ---------------------------------------------------


@dataclass(frozen=True)
class FixtureScore:
    """One tag's measured behaviour on its own hand-labelled cards."""

    tag_id: str
    declared_confidence: float
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    unresolved: tuple[str, ...]

    @property
    def precision(self) -> float:
        """Positives that were labelled positive. ``1.0`` when nothing matched.

        A tag that matches none of its own positive fixtures has perfect
        precision and is useless, which is why :attr:`recall` is reported beside
        it and why the CLI fails on a zero-recall tag rather than passing it.
        """
        denominator = self.true_positives + self.false_positives
        return 1.0 if denominator == 0 else self.true_positives / denominator

    @property
    def recall(self) -> float:
        denominator = self.true_positives + self.false_negatives
        return 1.0 if denominator == 0 else self.true_positives / denominator

    @property
    def ok(self) -> bool:
        return not self.unresolved and self.false_positives == 0 and self.recall == 1.0


def measure(definition: TagDefinition, index: Mapping[str, CardView]) -> FixtureScore:
    """Score one definition against its fixtures.

    Args:
        definition: The tag.
        index: Card name -> card, as produced by
            :meth:`~sabermetrics.substrate.corpus.JsonCorpusSource.by_name`.

    Returns:
        A :class:`FixtureScore`. Fixture names the index cannot resolve are
        collected in ``unresolved`` rather than skipped, because a precision
        computed over an unstated subset is a different number than the one the
        tag claims.
    """
    unresolved: list[str] = []
    tp = fp = fn = tn = 0
    for name in definition.positive_fixtures:
        card = index.get(name)
        if card is None:
            unresolved.append(name)
            continue
        if definition.evaluate(card) is not None:
            tp += 1
        else:
            fn += 1
    for name in definition.negative_fixtures:
        card = index.get(name)
        if card is None:
            unresolved.append(name)
            continue
        if definition.evaluate(card) is not None:
            fp += 1
        else:
            tn += 1
    return FixtureScore(
        tag_id=definition.id,
        declared_confidence=definition.confidence,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        true_negatives=tn,
        unresolved=tuple(sorted(unresolved)),
    )


def measure_all(
    definitions: Sequence[TagDefinition], index: Mapping[str, CardView]
) -> tuple[FixtureScore, ...]:
    """Score every definition, in id order."""
    return tuple(measure(d, index) for d in sorted(definitions, key=lambda d: d.id))


def render_scores(scores: Sequence[FixtureScore]) -> str:
    """Format fixture scores as a table, failures last and marked."""
    header = (
        f"{'tag':<34} {'prec':>6} {'rec':>6} {'tp':>4} {'fp':>4} "
        f"{'fn':>4} {'tn':>4}  status"
    )
    lines = [header, "-" * len(header)]
    for score in scores:
        status = "ok"
        if score.unresolved:
            status = f"UNRESOLVED FIXTURES: {', '.join(score.unresolved)}"
        elif score.false_positives:
            status = f"{score.false_positives} FALSE POSITIVE(S)"
        elif score.recall < 1.0:
            status = f"{score.false_negatives} MISSED POSITIVE(S)"
        lines.append(
            f"{score.tag_id:<34} {score.precision:>6.3f} {score.recall:>6.3f} "
            f"{score.true_positives:>4} {score.false_positives:>4} "
            f"{score.false_negatives:>4} {score.true_negatives:>4}  {status}"
        )
    return "\n".join(lines)


def matches_by_tag(
    source: CorpusSource, definitions: Sequence[TagDefinition]
) -> dict[str, list[CardView]]:
    """Every card each definition matches. For authoring and review, not builds.

    Holds the matched cards in memory, so it is for a development corpus and the
    fixture set rather than for a production rebuild, which streams.
    """
    out: dict[str, list[CardView]] = defaultdict(list)
    for card in source.iter_cards():
        for definition in definitions:
            if definition.evaluate(card) is not None:
                out[definition.id].append(card)
    return dict(out)
