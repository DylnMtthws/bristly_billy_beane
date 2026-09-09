"""Author and audit mechanic tag predicates against a real corpus.

    python scripts/tagdev.py grep 'rather than pay .* mana cost'
    python scripts/tagdev.py card 'Force of Will'
    python scripts/tagdev.py tag cost:phyrexian_mana --sample 40
    python scripts/tagdev.py fixtures            # score every tag on its fixtures
    python scripts/tagdev.py coverage            # full build + coverage report

This is a development aid, not part of any request path. It exists because a
tag's precision claim is only worth the corpus it was checked against: a regex
that looks right on six remembered cards routinely matches two hundred, and the
only way to find that out is to look.

The corpus defaults to ``$SABER_TAG_CORPUS`` and then to
``.research-dev/corpus.jsonl`` — a materialised snapshot, gitignored, built by
``scripts/fetch_tag_corpus.py``. Production tag builds read
``mtg_v1.card_any_medium`` instead; see ``sabermetrics tags build``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sabermetrics.mechanics.tags.predicates import CardView
from sabermetrics.substrate import tagging
from sabermetrics.substrate.corpus import JsonCorpusSource

DEFAULT_CORPUS = os.environ.get("SABER_TAG_CORPUS") or str(
    ROOT / ".research-dev" / "corpus.jsonl"
)


def _source(path: str) -> JsonCorpusSource:
    try:
        return JsonCorpusSource(path)
    except FileNotFoundError:
        raise SystemExit(
            f"no corpus at {path}.\n"
            "Build one with:  python scripts/fetch_tag_corpus.py --out "
            ".research-dev/corpus.jsonl\n"
            "or point SABER_TAG_CORPUS at an existing snapshot."
        ) from None


def _show(card: CardView, *, full: bool = False) -> str:
    head = f"{card.name}  [{card.mana_cost or '-'}]  {card.type_line}"
    if not full:
        return head
    body = [head]
    if card.oracle_text:
        body.append("    " + card.oracle_text.replace("\n", "\n    "))
    for index, face in enumerate(card.faces):
        body.append(
            f"  face[{index}] {face.name}  [{face.mana_cost or '-'}]  "
            f"{face.type_line or ''}"
        )
        if face.oracle_text:
            body.append("    " + face.oracle_text.replace("\n", "\n    "))
    if card.keywords:
        body.append(f"  keywords: {', '.join(card.keywords)}")
    return "\n".join(body)


def cmd_grep(args: argparse.Namespace) -> int:
    """Count and sample cards whose oracle text matches a raw regex."""
    regex = re.compile(args.pattern, re.IGNORECASE)
    source = _source(args.corpus)
    hits: list[CardView] = []
    for card in source.iter_cards():
        for _, text in card.texts("oracle_text"):
            haystack = text
            if not args.keep_reminders:
                from sabermetrics.mechanics.text import mask_reminder_text

                haystack = mask_reminder_text(text)
            if regex.search(haystack):
                hits.append(card)
                break
    print(f"{len(hits)} cards match {args.pattern!r}")
    step = max(1, len(hits) // args.sample) if args.sample else 1
    for card in hits[::step][: args.sample]:
        print("  " + _show(card, full=args.full))
    return 0


def cmd_card(args: argparse.Namespace) -> int:
    """Print one card, or every card whose name contains the argument."""
    source = _source(args.corpus)
    needle = args.name.casefold()
    for card in source.iter_cards():
        if card.name.casefold() == needle or (
            args.contains and needle in card.name.casefold()
        ):
            print(_show(card, full=True))
            print()
    return 0


def cmd_tag(args: argparse.Namespace) -> int:
    """Show what one shipped tag matches across the whole corpus."""
    from sabermetrics.mechanics.tags.registry import ALL_TAGS, by_id

    definitions = [by_id(args.tag_id)] if args.tag_id else list(ALL_TAGS)
    source = _source(args.corpus)
    matches = tagging.matches_by_tag(source, definitions)
    for definition in definitions:
        cards = matches.get(definition.id, [])
        print(f"\n{definition.id}  v{definition.version}  {len(cards)} cards")
        print(f"  {definition.description}")
        print(f"  limitations: {definition.limitations}")
        step = max(1, len(cards) // args.sample) if args.sample else 1
        for card in cards[::step][: args.sample]:
            match = definition.evaluate(card)
            span = match.matched_span if match else None
            where = (
                f"{span.field}[{span.start}:{span.end}] {span.text!r}" if span else ""
            )
            print(f"    {card.name:<40} {where}")
    return 0


def cmd_fixtures(args: argparse.Namespace) -> int:
    """Score every shipped tag against its hand-labelled fixtures."""
    from sabermetrics.mechanics.tags.registry import ALL_TAGS

    source = _source(args.corpus)
    scores = tagging.measure_all(ALL_TAGS, source.by_name())
    print(tagging.render_scores(scores))
    bad = [s for s in scores if not s.ok]
    print(f"\n{len(ALL_TAGS) - len(bad)}/{len(ALL_TAGS)} tags clean")
    return 1 if bad else 0


def cmd_coverage(args: argparse.Namespace) -> int:
    """Full build against the corpus, with the coverage report."""
    from sabermetrics.mechanics.tags.registry import ALL_TAGS

    source = _source(args.corpus)
    result = tagging.build(source, ALL_TAGS)
    print(f"snapshot   {result.snapshot.source_view}  {result.snapshot.sha256()[:12]}")
    print(f"library    {result.library_sha256[:12]}")
    print(f"content    {result.content_sha256[:12]}")
    print(f"rows       {len(result.rows)}")
    print()
    print(tagging.render_coverage(result.coverage, top=args.top))
    return 0


def cmd_try(args: argparse.Namespace) -> int:
    """Evaluate an ad-hoc predicate expression against the whole corpus.

    The expression is ordinary Python over the predicate algebra, so it is the
    same object a definition would carry::

        python scripts/tagdev.py try \
            'AllOf(TypeLine(r"Artifact"), Text(r"\\{T\\}: Add"))' \
            --positives 'Sol Ring,Mana Crypt' --negatives 'Lightning Bolt'

    Prints the corpus-wide match count, a spread-out sample with the span each
    card matched on, and — when fixtures are supplied — which of them the
    predicate gets wrong. That last line is the one worth reading: a predicate
    that matches 4,000 cards is usually matching a rules gloss.
    """
    from sabermetrics.mechanics.tags import predicates as P

    namespace = {
        name: getattr(P, name)
        for name in (
            "AllOf",
            "AnyOf",
            "Not",
            "Text",
            "TypeLine",
            "Cost",
            "Keyword",
            "ManaValue",
        )
    }
    predicate = eval(args.expression, {"__builtins__": {}}, namespace)
    if not P.always_yields_span(predicate):
        print("REJECTED: this predicate can match without producing a span.")
        print("A tag row must be able to point at the text it came from; add a")
        print("Text/TypeLine/Cost/Keyword leaf that every satisfying path hits.")
        return 1

    source = _source(args.corpus)
    hits: list[tuple[CardView, P.MatchedSpan]] = []
    index: dict[str, CardView] = {}
    for card in source.iter_cards():
        index.setdefault(card.name.split(" // ", 1)[0], card)
        index[card.name] = card
        evidence = predicate.evaluate(card)
        if evidence is not None:
            hits.append((card, evidence.spans[0]))

    print(f"{len(hits)} cards match")
    step = max(1, len(hits) // args.sample) if args.sample else 1
    for card, span in hits[::step][: args.sample]:
        print(f"  {card.name:<38} {span.field}[{span.start}:{span.end}] {span.text!r}")

    matched = {card.name for card, _ in hits} | {
        card.name.split(" // ", 1)[0] for card, _ in hits
    }
    problems = []
    for name in [n.strip() for n in (args.positives or "").split(",") if n.strip()]:
        if name not in index:
            problems.append(f"UNKNOWN CARD (positive): {name}")
        elif name not in matched:
            problems.append(f"MISSED positive: {name}")
    for name in [n.strip() for n in (args.negatives or "").split(",") if n.strip()]:
        if name not in index:
            problems.append(f"UNKNOWN CARD (negative): {name}")
        elif name in matched:
            problems.append(f"FALSE POSITIVE: {name}")
    if problems:
        print("\n" + "\n".join("  " + p for p in problems))
        return 1
    if args.positives or args.negatives:
        print("\n  all supplied fixtures agree")
    return 0


def cmd_json(args: argparse.Namespace) -> int:
    """Emit matched cards for one tag as JSON, for scripted review."""
    from sabermetrics.mechanics.tags.registry import by_id

    definition = by_id(args.tag_id)
    source = _source(args.corpus)
    out = []
    for card in source.iter_cards():
        match = definition.evaluate(card)
        if match:
            out.append(
                {
                    "name": card.name,
                    "type_line": card.type_line,
                    "mana_cost": card.mana_cost,
                    "span": match.matched_span.as_dict(),
                }
            )
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=DEFAULT_CORPUS)
    sub = parser.add_subparsers(dest="command", required=True)

    grep = sub.add_parser("grep", help="regex over oracle text")
    grep.add_argument("pattern")
    grep.add_argument("--sample", type=int, default=25)
    grep.add_argument("--full", action="store_true")
    grep.add_argument("--keep-reminders", action="store_true")
    grep.set_defaults(func=cmd_grep)

    card = sub.add_parser("card", help="print a card")
    card.add_argument("name")
    card.add_argument("--contains", action="store_true")
    card.set_defaults(func=cmd_card)

    tag = sub.add_parser("tag", help="what a shipped tag matches")
    tag.add_argument("tag_id", nargs="?")
    tag.add_argument("--sample", type=int, default=25)
    tag.set_defaults(func=cmd_tag)

    fixtures = sub.add_parser("fixtures", help="score tags on their fixtures")
    fixtures.set_defaults(func=cmd_fixtures)

    coverage = sub.add_parser("coverage", help="full build and coverage report")
    coverage.add_argument("--top", type=int, default=20)
    coverage.set_defaults(func=cmd_coverage)

    attempt = sub.add_parser("try", help="evaluate an ad-hoc predicate expression")
    attempt.add_argument("expression")
    attempt.add_argument("--sample", type=int, default=25)
    attempt.add_argument("--positives", default="", help="comma-separated names")
    attempt.add_argument("--negatives", default="", help="comma-separated names")
    attempt.set_defaults(func=cmd_try)

    as_json = sub.add_parser("json", help="matched cards as JSON")
    as_json.add_argument("tag_id")
    as_json.set_defaults(func=cmd_json)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
