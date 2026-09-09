"""Materialise a card-text snapshot for offline tag authoring and testing.

    python scripts/fetch_tag_corpus.py --out .research-dev/corpus.jsonl
    python scripts/fetch_tag_corpus.py --names-from-tags \
        --out fixtures/mechanics/tag_cards.json

**This is not the production corpus and it does not pretend to be.** Production
tag builds read ``mtg_v1.card_any_medium`` as ``mtg_consumer`` (ADR-020); this
script exists so a precision claim can be measured without a database, and so
tag predicates can be authored against real oracle text rather than remembered
oracle text.

Provenance is recorded in the output and is deliberately honest: the snapshot's
``source_view`` says ``scryfall:oracle_cards``, never ``mtg_v1.card_any_medium``,
so a tag row built from it can never be mistaken for one built from the
production view.

The two modes differ only in what they keep:

* default — every card, for the development corpus (gitignored, ~34k rows)
* ``--names-from-tags`` — only the cards named as fixtures by the shipped tag
  library, for the checked-in fixture file the test suite measures against

The network call is to Scryfall's public bulk endpoint and happens **here**, at
materialisation time. Nothing in ``mechanics/``, ``substrate/`` or
``assistant/`` makes a network call to build or answer anything.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

BULK_INDEX = "https://api.scryfall.com/bulk-data"

#: Layouts that are not cards a deck can contain. Dropping them keeps the
#: coverage report about cards rather than about tokens and art series.
SKIP_LAYOUTS = frozenset(
    {
        "art_series",
        "token",
        "double_faced_token",
        "emblem",
        "vanguard",
        "scheme",
        "planar",
        "augment",
        "host",
    }
)

#: Every card type, so ``all_types`` mirrors what ``mtg_v1`` publishes rather
#: than being derived differently offline and online.
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


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "sabermetrics-research/0.1", "Accept": "*/*"}
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def _bulk_entry(kind: str = "oracle_cards") -> dict:
    index = json.loads(_fetch(BULK_INDEX))
    for entry in index["data"]:
        if entry["type"] == kind:
            return entry
    raise SystemExit(f"scryfall bulk index has no {kind!r} entry")


def _all_types(type_line: str) -> list[str]:
    head = type_line.split("—")[0]
    words = head.replace("//", " ").split()
    return [t for t in CARD_TYPES if t in words]


def _record(card: dict) -> dict:
    faces = [
        {
            "name": face.get("name"),
            "mana_cost": face.get("mana_cost"),
            "type_line": face.get("type_line"),
            "oracle_text": face.get("oracle_text"),
        }
        for face in card.get("card_faces") or []
    ]
    type_line = card.get("type_line") or ""
    return {
        "oracle_id": card.get("oracle_id"),
        "name": card.get("name"),
        "layout": card.get("layout") or "",
        "mana_cost": card.get("mana_cost"),
        "mana_value": card.get("cmc") or 0.0,
        "type_line": type_line,
        "oracle_text": card.get("oracle_text"),
        "colors": card.get("colors") or [],
        "color_identity": card.get("color_identity") or [],
        "keywords": card.get("keywords") or [],
        "all_types": _all_types(type_line),
        "castable_cmcs": (
            [] if "Land" in _all_types(type_line) else [card.get("cmc", 0.0)]
        ),
        "faces": faces,
        "commander_legal": (card.get("legalities") or {}).get("commander"),
    }


def _wanted_names() -> set[str]:
    from sabermetrics.mechanics.tags.registry import ALL_TAGS

    names: set[str] = set()
    for definition in ALL_TAGS:
        names.update(definition.positive_fixtures)
        names.update(definition.negative_fixtures)
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--names-from-tags",
        action="store_true",
        help="keep only cards named as fixtures by the shipped tag library",
    )
    parser.add_argument(
        "--cached",
        type=Path,
        default=None,
        help="reuse a previously downloaded oracle-cards .jsonl(.gz)",
    )
    args = parser.parse_args()

    if args.cached:
        raw = args.cached.read_bytes()
        if args.cached.suffix == ".gz":
            raw = gzip.decompress(raw)
        updated_at = datetime.fromtimestamp(
            args.cached.stat().st_mtime, tz=UTC
        ).isoformat()
        origin = str(args.cached)
    else:
        entry = _bulk_entry()
        raw = gzip.decompress(_fetch(entry["jsonl_download_uri"]))
        updated_at = entry["updated_at"]
        origin = entry["jsonl_download_uri"]

    wanted = _wanted_names() if args.names_from_tags else None
    records = []
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        card = json.loads(line)
        if card.get("layout") in SKIP_LAYOUTS or card.get("set_type") == "memorabilia":
            continue
        record = _record(card)
        if wanted is not None:
            front = (record["name"] or "").split(" // ", 1)[0]
            if record["name"] not in wanted and front not in wanted:
                continue
        records.append(record)
    records.sort(key=lambda r: (r["name"] or "", r["oracle_id"] or ""))

    snapshot = {
        "source_view": "scryfall:oracle_cards",
        "row_count": len(records),
        "max_content_updated_at": updated_at,
        "captured_at": datetime.now(UTC).isoformat(),
        "origin": origin,
        "note": (
            "Materialised for offline tag authoring and fixture measurement. "
            "Production tag builds read mtg_v1.card_any_medium; this snapshot "
            "is labelled scryfall:oracle_cards so a row built from it is never "
            "mistaken for one built from the production view."
        ),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.suffix == ".jsonl":
        with args.out.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        args.out.with_suffix(".meta.json").write_text(
            json.dumps(
                {"schema_version": "mechanic-corpus-snapshot.v1", "snapshot": snapshot},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    else:
        args.out.write_text(
            json.dumps(
                {
                    "schema_version": "mechanic-corpus-snapshot.v1",
                    "snapshot": snapshot,
                    "cards": records,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    missing = ""
    if wanted is not None:
        found = {r["name"] for r in records} | {
            (r["name"] or "").split(" // ", 1)[0] for r in records
        }
        unresolved = sorted(wanted - found)
        if unresolved:
            missing = "\nUNRESOLVED FIXTURE NAMES: " + ", ".join(unresolved)
    print(f"wrote {len(records)} cards to {args.out}{missing}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
