"""Parse and resolve pasted plain-text Commander decklists against the local corpus."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sabermetrics.commander_pairs import compatible_pair

MAX_IMPORT_CHARS = 100_000
MAX_IMPORT_LINES = 500
MAX_IMPORT_QUANTITY = 99
MAX_IMPORT_TOTAL = 400

ZONE_LIBRARY = "Unsorted"
ZONE_SIDEBOARD = "Sideboard"
ZONE_MAYBEBOARD = "Maybeboard"

_SECTION_ALIASES: dict[str, tuple[str, bool]] = {
    "commander": (ZONE_LIBRARY, True),
    "commanders": (ZONE_LIBRARY, True),
    "deck": (ZONE_LIBRARY, False),
    "mainboard": (ZONE_LIBRARY, False),
    "main deck": (ZONE_LIBRARY, False),
    "maindeck": (ZONE_LIBRARY, False),
    "main": (ZONE_LIBRARY, False),
    "sideboard": (ZONE_SIDEBOARD, False),
    "side": (ZONE_SIDEBOARD, False),
    "maybeboard": (ZONE_MAYBEBOARD, False),
    "maybe": (ZONE_MAYBEBOARD, False),
    "considering": (ZONE_MAYBEBOARD, False),
}

_QTY_ATTACHED = re.compile(r"^(-?\d+)\s*[xX]\s*(.+)$")
_QTY_SPACED = re.compile(r"^(-?\d+)\s+(.+)$")
_SB_PREFIX = re.compile(r"^SB:\s*(.+)$", re.IGNORECASE)
_TRAILING_COMMENT = re.compile(r"\s+#.*$")
_FOIL_SUFFIX = re.compile(r"(?:\s+\*F(?:oil)?\*|\s+\*F\*)$", re.IGNORECASE)
_SET_SUFFIX = re.compile(r"\s+\([A-Za-z0-9]{2,8}\)(?:\s+[0-9]+[A-Za-z]?)?\s*$")
_TAG_SUFFIX = re.compile(r"\s*\[([^\]]+)\]\s*$")
_SECTION_COUNT = re.compile(r"\s*\(\d+\)\s*$")
_COMMANDER_TAG = re.compile(r"^commanders?$")
_SIDEBOARD_TAG = re.compile(r"^(?:sideboard|side|sb)$")
_MAYBE_TAG = re.compile(r"^(?:maybeboard|maybe|considering)$")


class DeckTextImportError(Exception):
    """Hard failure while parsing or resolving a pasted decklist."""

    def __init__(
        self, errors: list[dict[str, Any]], *, message: str | None = None
    ) -> None:
        self.errors = errors
        detail = message
        if detail is None:
            detail = (
                errors[0]["message"]
                if errors
                else "This decklist could not be imported."
            )
        super().__init__(detail)


@dataclass(frozen=True)
class ParsedCardLine:
    line_no: int
    quantity: int
    name: str
    zone: str
    commander: bool


@dataclass
class ParsedDeck:
    lines: list[ParsedCardLine] = field(default_factory=list)


@dataclass
class ResolvedEntry:
    card: dict[str, Any]
    quantity: int
    zone: str
    is_commander: bool


@dataclass
class ResolvedImport:
    entries: list[ResolvedEntry]
    eligible_commanders: list[dict[str, Any]]
    warnings: list[str]
    needs_commander_selection: bool


def normalize_card_name(name: str) -> str:
    value = unicodedata.normalize("NFKC", str(name or ""))
    value = value.replace("\u2019", "'").replace("`", "'").replace("\u00b4", "'")
    return " ".join(value.split()).casefold()


def parse_deck_text(text: str) -> ParsedDeck:
    if text is None:
        raise DeckTextImportError(
            [{"line": None, "message": "Paste a decklist with at least one card."}]
        )
    raw = str(text).lstrip("\ufeff")
    if len(raw) > MAX_IMPORT_CHARS:
        raise DeckTextImportError(
            [
                {
                    "line": None,
                    "message": f"Decklist must be at most {MAX_IMPORT_CHARS:,} characters.",
                }
            ]
        )
    rows = raw.splitlines()
    if len(rows) > MAX_IMPORT_LINES:
        raise DeckTextImportError(
            [
                {
                    "line": None,
                    "message": f"Decklist must be at most {MAX_IMPORT_LINES} lines.",
                }
            ]
        )

    current_zone = ZONE_LIBRARY
    commander_section = False
    parsed: list[ParsedCardLine] = []
    errors: list[dict[str, Any]] = []
    total = 0

    for line_no, original in enumerate(rows, 1):
        line = original.strip()
        if not line:
            continue
        if line.startswith(("//", "#")):
            continue
        line = _TRAILING_COMMENT.sub("", line).strip()
        if not line:
            continue
        section = _match_section(line)
        if section is not None:
            current_zone, commander_section = section
            continue

        zone = current_zone
        commander = commander_section
        sideboard_prefix = _SB_PREFIX.match(line)
        if sideboard_prefix:
            zone = ZONE_SIDEBOARD
            commander = False
            line = sideboard_prefix.group(1).strip()

        try:
            quantity, name, tags = _parse_card_line(line)
        except DeckTextImportError as exc:
            errors.extend(
                {**item, "line": item.get("line") or line_no} for item in exc.errors
            )
            continue

        tag_zone, tag_commander = _zone_from_tags(tags)
        if tag_zone is not None:
            zone = tag_zone
        if tag_commander:
            commander = True
        if name.lower().startswith(("http://", "https://")):
            errors.append(
                {
                    "line": line_no,
                    "message": "Paste a plain-text decklist, not a URL.",
                }
            )
            continue
        total += quantity
        if total > MAX_IMPORT_TOTAL:
            errors.append(
                {
                    "line": line_no,
                    "message": f"Imported decks can include at most {MAX_IMPORT_TOTAL} cards.",
                }
            )
            break
        parsed.append(
            ParsedCardLine(
                line_no=line_no,
                quantity=quantity,
                name=name,
                zone=zone,
                commander=commander,
            )
        )

    if errors:
        raise DeckTextImportError(errors)
    if not parsed:
        raise DeckTextImportError(
            [{"line": None, "message": "Paste a decklist with at least one card."}]
        )
    return ParsedDeck(lines=parsed)


def build_card_lookup(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    lookup: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        card = _card_dict(row)
        keys = {normalize_card_name(card["name"])}
        for face in str(card.get("name") or "").split(" // "):
            face_key = normalize_card_name(face)
            if face_key:
                keys.add(face_key)
        for key in keys:
            _add_lookup(lookup, key, card)
    return lookup


def resolve_import(
    parsed: ParsedDeck,
    lookup: Mapping[str, list[dict[str, Any]]],
    commander_card_ids: list[str] | None = None,
) -> ResolvedImport:
    errors: list[dict[str, Any]] = []
    matched: list[tuple[ParsedCardLine, dict[str, Any]]] = []
    for line in parsed.lines:
        key = normalize_card_name(line.name)
        hits = list(lookup.get(key) or [])
        if not hits:
            errors.append(
                {
                    "line": line.line_no,
                    "message": f"Unknown card: {line.name}.",
                }
            )
            continue
        if len(hits) > 1:
            names = ", ".join(sorted({str(card["name"]) for card in hits}))
            errors.append(
                {
                    "line": line.line_no,
                    "message": (
                        f"“{line.name}” matches more than one card ({names}). "
                        "Use the exact printed name."
                    ),
                }
            )
            continue
        if not hits[0].get("is_legal_in_99"):
            errors.append(
                {
                    "line": line.line_no,
                    "message": f"{line.name} is not Commander-legal. Remove or replace this card.",
                }
            )
            continue
        matched.append((line, hits[0]))

    if errors:
        raise DeckTextImportError(errors)

    grouped: dict[tuple[str, str, bool], ResolvedEntry] = {}
    for line, card in matched:
        oracle = str(card.get("oracle_id") or card["id"])
        bucket = grouped.get((oracle, line.zone, line.commander))
        if bucket is None:
            grouped[(oracle, line.zone, line.commander)] = ResolvedEntry(
                card=card,
                quantity=line.quantity,
                zone=line.zone,
                is_commander=line.commander,
            )
        else:
            bucket.quantity += line.quantity

    overflow = [
        item for item in grouped.values() if item.quantity > MAX_IMPORT_QUANTITY
    ]
    if overflow:
        raise DeckTextImportError(
            [
                {
                    "line": None,
                    "message": (
                        f"{item.card['name']} totals {item.quantity} copies; "
                        f"quantity must be at most {MAX_IMPORT_QUANTITY}."
                    ),
                }
                for item in overflow
            ]
        )

    by_id = {str(item.card["id"]): item.card for item in grouped.values()}
    by_oracle = {
        str(item.card.get("oracle_id") or item.card["id"]): item.card
        for item in grouped.values()
    }
    eligible: dict[str, dict[str, Any]] = {}
    for item in grouped.values():
        card = item.card
        if card.get("is_legal_commander") and card.get("is_legal_in_99"):
            eligible[str(card.get("oracle_id") or card["id"])] = card

    nominated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in grouped.values():
        if not item.is_commander:
            continue
        oracle = str(item.card.get("oracle_id") or item.card["id"])
        if oracle in seen or item.quantity != 1:
            raise DeckTextImportError(
                [
                    {
                        "line": None,
                        "message": f"Commander {item.card['name']} must appear exactly once in the commander section.",
                    }
                ]
            )
        seen.add(oracle)
        nominated.append(item.card)

    selected_ids = [
        str(value) for value in (commander_card_ids or []) if str(value).strip()
    ]
    needs_selection = not nominated
    if nominated and selected_ids:
        selected_ids = []
    if not nominated and selected_ids:
        nominated = _commanders_from_ids(selected_ids, by_id, by_oracle, eligible)

    if len(nominated) > 2:
        raise DeckTextImportError(
            [{"line": None, "message": "Choose up to two commanders."}]
        )
    for card in nominated:
        if not card.get("is_legal_commander") or not card.get("is_legal_in_99"):
            raise DeckTextImportError(
                [
                    {
                        "line": None,
                        "message": f"{card.get('name') or 'That card'} cannot be a commander.",
                    }
                ]
            )
    if len(nominated) == 2 and not compatible_pair(*nominated):
        raise DeckTextImportError(
            [
                {
                    "line": None,
                    "message": "The two commanders do not form a recognized legal pair.",
                }
            ]
        )

    commander_oracles = (
        {str(card.get("oracle_id") or card["id"]) for card in nominated}
        if needs_selection
        else set()
    )
    entries: list[ResolvedEntry] = []
    for item in grouped.values():
        if item.is_commander:
            continue
        oracle = str(item.card.get("oracle_id") or item.card["id"])
        quantity = item.quantity
        if oracle in commander_oracles:
            quantity -= 1
            commander_oracles.remove(oracle)
        if quantity <= 0:
            continue
        entries.append(
            ResolvedEntry(
                card=item.card,
                quantity=quantity,
                zone=item.zone,
                is_commander=False,
            )
        )
    for card in nominated:
        entries.append(
            ResolvedEntry(card=card, quantity=1, zone=ZONE_LIBRARY, is_commander=True)
        )

    warnings: list[str] = []

    eligible_list = sorted(
        eligible.values(), key=lambda card: str(card["name"]).casefold()
    )
    return ResolvedImport(
        entries=entries,
        eligible_commanders=eligible_list,
        warnings=warnings,
        needs_commander_selection=needs_selection and not nominated,
    )


def _commanders_from_ids(
    selected_ids: list[str],
    by_id: Mapping[str, dict[str, Any]],
    by_oracle: Mapping[str, dict[str, Any]],
    eligible: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in selected_ids:
        card = by_id.get(key) or by_oracle.get(key)
        if card is None:
            raise DeckTextImportError(
                [
                    {
                        "line": None,
                        "message": "Choose a commander from the imported cards.",
                    }
                ]
            )
        oracle = str(card.get("oracle_id") or card["id"])
        if oracle not in eligible:
            raise DeckTextImportError(
                [
                    {
                        "line": None,
                        "message": f"{card.get('name') or 'That card'} cannot be a commander.",
                    }
                ]
            )
        if oracle in seen:
            raise DeckTextImportError(
                [
                    {
                        "line": None,
                        "message": "Each commander in a pair must be a different card.",
                    }
                ]
            )
        seen.add(oracle)
        chosen.append(eligible[oracle])
    return chosen


def _match_section(line: str) -> tuple[str, bool] | None:
    cleaned = line.strip().rstrip(":").strip()
    cleaned = _SECTION_COUNT.sub("", cleaned).strip()
    key = " ".join(cleaned.split()).casefold()
    return _SECTION_ALIASES.get(key)


def _parse_card_line(line: str) -> tuple[int, str, list[str]]:
    attached = _QTY_ATTACHED.match(line)
    spaced = _QTY_SPACED.match(line)
    match = attached or spaced
    if match:
        qty_text = match.group(1)
        rest = match.group(2).strip()
        if len(qty_text) > 4:
            raise DeckTextImportError(
                [{"line": None, "message": f"Quantity {qty_text} is too large."}]
            )
        quantity = int(qty_text)
    else:
        quantity = 1
        rest = line.strip()
    if quantity <= 0:
        raise DeckTextImportError(
            [{"line": None, "message": "Quantity must be at least 1."}]
        )
    if quantity > MAX_IMPORT_QUANTITY:
        raise DeckTextImportError(
            [
                {
                    "line": None,
                    "message": f"Quantity must be at most {MAX_IMPORT_QUANTITY}.",
                }
            ]
        )
    tags: list[str] = []
    changed = True
    while rest and changed:
        changed = False
        foil = _FOIL_SUFFIX.search(rest)
        if foil:
            rest = rest[: foil.start()].rstrip()
            changed = True
        tag = _TAG_SUFFIX.search(rest)
        if tag:
            tags.append(tag.group(1))
            rest = rest[: tag.start()].rstrip()
            changed = True
        set_code = _SET_SUFFIX.search(rest)
        if set_code:
            rest = rest[: set_code.start()].rstrip()
            changed = True
    name = " ".join(rest.split())
    if not name:
        raise DeckTextImportError(
            [{"line": None, "message": "A card line is missing a name."}]
        )
    return quantity, name, tags


def _zone_from_tags(tags: list[str]) -> tuple[str | None, bool]:
    zone: str | None = None
    commander = False
    for raw in tags:
        label = raw.split("{", 1)[0].strip().casefold()
        if _COMMANDER_TAG.match(label):
            commander = True
        elif _SIDEBOARD_TAG.match(label):
            zone = ZONE_SIDEBOARD
        elif _MAYBE_TAG.match(label):
            zone = ZONE_MAYBEBOARD
    return zone, commander


def _card_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    card = dict(row)
    raw = card.get("color_identity")
    if isinstance(raw, str):
        try:
            card["color_identity"] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            card["color_identity"] = []
    elif not isinstance(raw, list):
        card["color_identity"] = []
    return card


def _printing_rank(card: Mapping[str, Any]) -> tuple[int, str]:
    missing_image = 1 if not card.get("image_uri") else 0
    return missing_image, str(card.get("id") or "")


def _add_lookup(
    lookup: dict[str, list[dict[str, Any]]], key: str, card: dict[str, Any]
) -> None:
    if not key:
        return
    bucket = lookup.setdefault(key, [])
    oracle = str(card.get("oracle_id") or card.get("id") or "")
    for index, existing in enumerate(bucket):
        if str(existing.get("oracle_id") or existing.get("id") or "") == oracle:
            if _printing_rank(card) < _printing_rank(existing):
                bucket[index] = card
            return
    bucket.append(card)


def oversized_request_error() -> DeckTextImportError:
    return DeckTextImportError(
        [
            {
                "line": None,
                "message": f"Decklist must be at most {MAX_IMPORT_CHARS:,} characters.",
            }
        ]
    )
