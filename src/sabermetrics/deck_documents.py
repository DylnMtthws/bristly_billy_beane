"""Persistent, owner-scoped editable deck documents for Deck Lab."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import unicodedata
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from sabermetrics import db
from sabermetrics.account_playmats import (
    BUILTIN_SURFACES,
    CUSTOM_SURFACE,
    LIBRARY_SURFACE,
    AccountPlaymatRepo,
    owner_presentation,
    public_presentation,
)
from sabermetrics.card_discovery import (
    format_legal_sql,
    primary_type,
)
from sabermetrics.commander_pairs import (
    can_participate_in_pair,
    compatible_pair,
    pair_id,
)
from sabermetrics.deck_text_import import (
    ZONE_LIBRARY,
    ResolvedImport,
    build_card_lookup,
    parse_deck_text,
    resolve_import,
)


class DeckDocumentError(Exception):
    """Base class for document command failures."""


class DeckNotFound(DeckDocumentError):
    pass


class RevisionConflict(DeckDocumentError):
    def __init__(self, current_revision: int) -> None:
        super().__init__("This deck changed in another tab.")
        self.current_revision = current_revision


class InvalidCommand(DeckDocumentError):
    pass


# Public discovery shows commanders plus the main library. Zone names in this
# set stay owner-private and are omitted from the public projection.
_PRIVATE_PUBLIC_ZONES = frozenset(
    {"sideboard", "notes", "note", "maybeboard", "maybe", "draft", "considering"}
)


def _is_public_library_zone(name: str | None) -> bool:
    return str(name or "Unsorted").strip().casefold() not in _PRIVATE_PUBLIC_ZONES


def _clean_tag_name(value: object) -> tuple[str, str]:
    name = " ".join(unicodedata.normalize("NFKC", str(value)).split())
    if not 2 <= len(name) <= 32:
        raise InvalidCommand("Tags must be between 2 and 32 characters.")
    if any(not (char.isalnum() or char in " &+'-/") for char in name):
        raise InvalidCommand(
            "Tags may use letters, numbers, spaces, &, +, -, /, and '."
        )
    return name, name.casefold()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback


class DeckDocumentRepo:
    """Transactions and read models for editable deck documents."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        conn.execute("PRAGMA journal_size_limit=67108864")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _event(
        conn: sqlite3.Connection,
        actor_id: str,
        action: str,
        subject_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO activity_events "
            "(id, actor_id, action, subject_kind, subject_id, metadata_json) "
            "VALUES (?, ?, ?, 'deck', ?, ?)",
            (db.new_id(), actor_id, action, subject_id, json.dumps(metadata or {})),
        )

    @staticmethod
    def _card_row(conn: sqlite3.Connection, card_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT id, oracle_id, name, type_line, mana_cost, cmc, oracle_text, "
            "color_identity, image_uri, is_legal_commander, is_legal_in_99 "
            "FROM cards WHERE id = ?",
            (card_id,),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _oracle_row(conn: sqlite3.Connection, oracle_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT id, oracle_id, name, type_line, mana_cost, cmc, oracle_text, "
            "color_identity, image_uri, is_legal_commander, is_legal_in_99 "
            "FROM cards WHERE oracle_id = ? "
            "ORDER BY image_uri IS NULL, last_updated DESC, id LIMIT 1",
            (oracle_id,),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _insert_card(
        conn: sqlite3.Connection,
        *,
        deck_id: str,
        zone_id: str | None,
        card: dict[str, Any],
        quantity: int = 1,
        is_commander: bool = False,
        role: str | None = None,
        order: int = 0,
    ) -> str:
        entry_id = db.new_id()
        conn.execute(
            """INSERT INTO deck_entries
            (id, deck_id, zone_id, card_id, oracle_id, name, quantity,
             is_commander, sort_order, role, type_line, mana_cost, mana_value,
             oracle_text, color_identity, image_uri)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id,
                deck_id,
                None if is_commander else zone_id,
                card.get("id") or card.get("card_id"),
                card.get("oracle_id"),
                card.get("name") or "Unknown card",
                quantity,
                1 if is_commander else 0,
                order,
                role,
                card.get("type_line"),
                card.get("mana_cost"),
                card.get("cmc", card.get("mana_value")),
                card.get("oracle_text"),
                (
                    card.get("color_identity")
                    if isinstance(card.get("color_identity"), str)
                    else json.dumps(card.get("color_identity") or [])
                ),
                card.get("image_uri"),
            ),
        )
        return entry_id

    def _commander_cards(
        self, conn: sqlite3.Connection, ids: list[str]
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(ids, list)
            or len(ids) > 2
            or any(not isinstance(key, str) for key in ids)
        ):
            raise InvalidCommand("Choose up to two commanders.")
        cards = []
        for key in ids:
            card = self._card_row(conn, key)
            if (
                not card
                or not card.get("is_legal_commander")
                or not card.get("is_legal_in_99")
            ):
                raise InvalidCommand("Choose a legal commander.")
            cards.append(card)
        if len(cards) == 2 and not compatible_pair(*cards):
            raise InvalidCommand(
                "The two commanders do not form a recognized legal pair."
            )
        return cards

    def create(
        self,
        owner_id: str,
        *,
        title: str = "Untitled deck",
        commander_card_id: str | None = None,
        commander_card_ids: list[str] | None = None,
        source_kind: str | None = None,
        source_id: str | None = None,
    ) -> str:
        deck_id = db.new_id()
        with self._connect() as conn:
            if source_kind and source_id:
                existing = conn.execute(
                    "SELECT id FROM deck_documents WHERE owner_id=? "
                    "AND source_kind=? AND source_id=?",
                    (owner_id, source_kind, source_id),
                ).fetchone()
                if existing:
                    return str(existing["id"])
            conn.execute(
                "INSERT INTO deck_documents "
                "(id, owner_id, title, source_kind, source_id) VALUES (?, ?, ?, ?, ?)",
                (
                    deck_id,
                    owner_id,
                    title.strip()[:160] or "Untitled deck",
                    source_kind,
                    source_id,
                ),
            )
            unsorted_id = db.new_id()
            conn.execute(
                "INSERT INTO deck_zones(id, deck_id, name, sort_order, x, y) "
                "VALUES (?, ?, 'Unsorted', 0, 80, 120)",
                (unsorted_id, deck_id),
            )
            conn.execute(
                "INSERT INTO deck_presentations(deck_id,canvas_width,canvas_height) "
                "VALUES (?,1600,900)",
                (deck_id,),
            )
            ids = (
                commander_card_ids
                if commander_card_ids is not None
                else ([commander_card_id] if commander_card_id else [])
            )
            for card in self._commander_cards(conn, ids):
                self._insert_card(
                    conn, deck_id=deck_id, zone_id=None, card=card, is_commander=True
                )
            self._event(conn, owner_id, "deck.created", deck_id)
        return deck_id

    def _card_lookup(self, conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
        rows = conn.execute(
            "SELECT id, oracle_id, name, type_line, mana_cost, cmc, oracle_text, "
            "color_identity, image_uri, is_legal_commander, is_legal_in_99 "
            "FROM cards"
        ).fetchall()
        return build_card_lookup(rows)

    def preview_text_import(
        self,
        text: str,
        *,
        title: str = "",
        commander_card_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Parse and match a pasted list without writing a deck."""
        parsed = parse_deck_text(text)
        with self._connect() as conn:
            resolved = resolve_import(
                parsed, self._card_lookup(conn), commander_card_ids
            )
        return self._text_import_preview(title, resolved)

    def import_text(
        self,
        owner_id: str,
        text: str,
        *,
        title: str = "Untitled deck",
        commander_card_ids: list[str] | None = None,
    ) -> str:
        """Atomically create a private editable deck from a pasted list."""
        parsed = parse_deck_text(text)
        deck_id = db.new_id()
        clean_title = str(title or "").strip()[:160] or "Untitled deck"
        with self._connect() as conn:
            resolved = resolve_import(
                parsed, self._card_lookup(conn), commander_card_ids
            )
            conn.execute(
                "INSERT INTO deck_documents "
                "(id, owner_id, title, source_kind) VALUES (?, ?, ?, ?)",
                (deck_id, owner_id, clean_title, "text_import"),
            )
            zone_ids = {ZONE_LIBRARY: db.new_id()}
            conn.execute(
                "INSERT INTO deck_zones(id, deck_id, name, sort_order, x, y) "
                "VALUES (?, ?, 'Unsorted', 0, 80, 120)",
                (zone_ids[ZONE_LIBRARY], deck_id),
            )
            extra = 1
            needed_zones = {
                item.zone
                for item in resolved.entries
                if not item.is_commander and item.zone != ZONE_LIBRARY
            }
            for zone_name in ("Sideboard", "Maybeboard"):
                if zone_name not in needed_zones:
                    continue
                zone_ids[zone_name] = db.new_id()
                conn.execute(
                    "INSERT INTO deck_zones(id, deck_id, name, sort_order, x, y) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        zone_ids[zone_name],
                        deck_id,
                        zone_name,
                        extra,
                        80 + extra * 40,
                        120 + extra * 40,
                    ),
                )
                extra += 1
            conn.execute(
                "INSERT INTO deck_presentations(deck_id,canvas_width,canvas_height) "
                "VALUES (?,1600,900)",
                (deck_id,),
            )
            for order, item in enumerate(resolved.entries):
                self._insert_card(
                    conn,
                    deck_id=deck_id,
                    zone_id=None if item.is_commander else zone_ids[item.zone],
                    card=item.card,
                    quantity=item.quantity,
                    is_commander=item.is_commander,
                    order=order,
                )
            self._event(
                conn,
                owner_id,
                "deck.imported",
                deck_id,
                {"source_kind": "text_import"},
            )
        return deck_id

    @staticmethod
    def _text_import_preview(title: str, resolved: ResolvedImport) -> dict[str, Any]:
        commanders = []
        zones: dict[str, list[dict[str, Any]]] = {}
        validate_entries: list[dict[str, Any]] = []
        for item in resolved.entries:
            card = item.card
            format_legal = bool(card.get("is_legal_in_99"))
            preview_card = {
                "card_id": card.get("id"),
                "oracle_id": card.get("oracle_id"),
                "name": card.get("name") or "Unknown card",
                "quantity": item.quantity,
                "type_line": card.get("type_line") or "",
                "format_legal": format_legal,
                "is_commander": item.is_commander,
            }
            entry = {
                "name": preview_card["name"],
                "zone_name": item.zone,
                "quantity": item.quantity,
                "is_commander": item.is_commander,
                "oracle_id": card.get("oracle_id"),
                "oracle_text": card.get("oracle_text"),
                "type_line": card.get("type_line"),
                "color_identity": card.get("color_identity") or [],
                "format_legal": format_legal,
                "commander_legal": bool(
                    card.get("is_legal_commander") and format_legal
                ),
            }
            validate_entries.append(entry)
            if item.is_commander:
                commanders.append(preview_card)
            else:
                zones.setdefault(item.zone, []).append(preview_card)
        zone_list = [
            {"name": name, "cards": zones[name]}
            for name in ("Unsorted", "Sideboard", "Maybeboard")
            if name in zones
        ]
        return {
            "title": str(title or "").strip()[:160] or "Untitled deck",
            "commanders": commanders,
            "zones": zone_list,
            "validation": DeckDocumentRepo.validate(validate_entries),
            "eligible_commanders": [
                {"id": card["id"], "name": card["name"]}
                for card in resolved.eligible_commanders
            ],
            "needs_commander_selection": resolved.needs_commander_selection,
            "warnings": list(resolved.warnings),
        }

    def import_generated(self, owner_id: str, generated_id: str) -> str:
        with self._connect() as conn:
            source = conn.execute(
                """SELECT gd.*, c.id AS commander_card_id, c.oracle_id,
                          c.name AS commander_name, c.type_line, c.mana_cost,
                          c.cmc, c.oracle_text, c.color_identity, c.image_uri
                   FROM generated_decks gd JOIN cards c ON c.id=gd.commander_id
                   WHERE gd.id=? AND gd.owner_id=?""",
                (generated_id, owner_id),
            ).fetchone()
            if source is None:
                raise DeckNotFound()
        deck_id = self.create(
            owner_id,
            title=source["deck_name"] or source["commander_name"],
            source_kind="generated",
            source_id=generated_id,
        )
        with self._connect() as conn:
            if conn.execute(
                "SELECT COUNT(*) FROM deck_entries WHERE deck_id=?", (deck_id,)
            ).fetchone()[0]:
                return deck_id
            unsorted_id = conn.execute(
                "SELECT id FROM deck_zones WHERE deck_id=? AND name='Unsorted'",
                (deck_id,),
            ).fetchone()["id"]
            commander_row = self._card_row(conn, str(source["commander_card_id"]))
            if (
                commander_row
                and commander_row.get("is_legal_commander")
                and commander_row.get("is_legal_in_99")
            ):
                self._insert_card(
                    conn,
                    deck_id=deck_id,
                    zone_id=None,
                    card=commander_row,
                    is_commander=True,
                )
            cards = _json(source["cards_json"], [])
            grouped: dict[tuple[str, str], list[Any]] = {}
            for item in cards:
                key = (
                    str(item.get("card_id") or item.get("name")),
                    str(item.get("slot_role") or ""),
                )
                if key not in grouped:
                    grouped[key] = [item, 0]
                grouped[key][1] += int(item.get("quantity") or 1)
            for order, (item, quantity) in enumerate(grouped.values()):
                card = self._card_row(conn, str(item.get("card_id") or ""))
                if not card or not card.get("is_legal_in_99"):
                    continue
                self._insert_card(
                    conn,
                    deck_id=deck_id,
                    zone_id=unsorted_id,
                    card=card,
                    quantity=min(99, quantity),
                    role=item.get("slot_role"),
                    order=order,
                )
            self._event(
                conn,
                owner_id,
                "deck.imported",
                deck_id,
                {"source_kind": "generated", "source_id": generated_id},
            )
            if source["generated_at"]:
                conn.execute(
                    "UPDATE deck_documents SET created_at=?,updated_at=? WHERE id=?",
                    (source["generated_at"], source["generated_at"], deck_id),
                )
        return deck_id

    def import_candidate(self, owner_id: str, candidate_id: str) -> str:
        with self._connect() as conn:
            source = conn.execute(
                "SELECT * FROM cedh_candidates WHERE candidate_id=? AND owner_id=?",
                (candidate_id, owner_id),
            ).fetchone()
            if source is None:
                raise DeckNotFound()
        deck_id = self.create(
            owner_id,
            title=source["commander_name"],
            source_kind="candidate",
            source_id=candidate_id,
        )
        with self._connect() as conn:
            if conn.execute(
                "SELECT COUNT(*) FROM deck_entries WHERE deck_id=?", (deck_id,)
            ).fetchone()[0]:
                return deck_id
            unsorted_id = conn.execute(
                "SELECT id FROM deck_zones WHERE deck_id=? AND name='Unsorted'",
                (deck_id,),
            ).fetchone()["id"]
            candidate = _json(source["candidate_json"], {})
            for order, oracle_id in enumerate(
                candidate.get("commander_oracle_ids", [])
            ):
                card = self._oracle_row(conn, oracle_id)
                if (
                    not card
                    or not card.get("is_legal_commander")
                    or not card.get("is_legal_in_99")
                ):
                    continue
                self._insert_card(
                    conn,
                    deck_id=deck_id,
                    zone_id=None,
                    card=card,
                    is_commander=True,
                    order=order,
                )
            for order, item in enumerate(candidate.get("library", [])):
                oracle_id = str(item.get("oracle_id") or "")
                card = self._oracle_row(conn, oracle_id)
                if not card or not card.get("is_legal_in_99"):
                    continue
                self._insert_card(
                    conn,
                    deck_id=deck_id,
                    zone_id=unsorted_id,
                    card=card,
                    quantity=int(item.get("quantity") or 1),
                    order=order,
                )
            self._event(
                conn,
                owner_id,
                "deck.imported",
                deck_id,
                {"source_kind": "candidate", "source_id": candidate_id},
            )
            if source["created_at"]:
                conn.execute(
                    "UPDATE deck_documents SET created_at=?,updated_at=? WHERE id=?",
                    (source["created_at"], source["created_at"], deck_id),
                )
        return deck_id

    def list_for_owner(
        self,
        owner_id: str,
        *,
        query: str = "",
        favorite: bool = False,
        recent_days: int | None = None,
        sort: str = "edited",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where = ["d.owner_id=?"]
        params: list[Any] = [owner_id]
        if query:
            where.append(
                "(d.title LIKE ? OR EXISTS (SELECT 1 FROM deck_entries ec WHERE ec.deck_id=d.id AND ec.is_commander=1 AND ec.name LIKE ?))"
            )
            term = f"%{query}%"
            params.extend([term, term])
        if favorite:
            where.append("d.favorite=1")
        if recent_days is not None:
            where.append("d.updated_at>=datetime('now', ?)")
            params.append(f"-{max(1, min(recent_days, 365))} days")
        params.append(limit)
        order = (
            "d.title COLLATE NOCASE, d.updated_at DESC"
            if sort == "name"
            else "d.updated_at DESC"
        )
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT d.*,
                    COALESCE(SUM(CASE WHEN e.is_commander=0 THEN e.quantity ELSE 0 END),0) AS library_count,
                    COALESCE(SUM(CASE WHEN e.is_commander=1 THEN e.quantity ELSE 0 END),0) AS commander_count,
                    GROUP_CONCAT(CASE WHEN e.is_commander=1 THEN e.name END, ' / ') AS commander_names
                    FROM deck_documents d LEFT JOIN deck_entries e ON e.deck_id=d.id
                    WHERE {' AND '.join(where)} GROUP BY d.id
                    ORDER BY {order} LIMIT ?""",
                params,
            ).fetchall()
            out = [dict(row) for row in rows]
            for item in out:
                if item.get("visibility") not in {"private", "public"}:
                    item["visibility"] = "private"
            commander_colors_by_deck: dict[str, set[str]] = {}
            commander_decks: set[str] = set()
            commander_oracles: dict[str, list[str]] = {}
            commander_card_by_deck: dict[str, str] = {}
            commander_image_by_deck: dict[str, str] = {}
            tags_by_deck: dict[str, list[dict[str, Any]]] = {}
            if out:
                deck_ids = [str(item["id"]) for item in out]
                placeholders = ",".join("?" for _ in deck_ids)
                commander_rows = conn.execute(
                    "SELECT e.deck_id,e.card_id,e.oracle_id,e.color_identity,"
                    "COALESCE(e.image_uri,c.image_uri) AS image_uri "
                    "FROM deck_entries e LEFT JOIN cards c ON c.id=e.card_id "
                    f"WHERE e.is_commander=1 AND e.deck_id IN ({placeholders}) "
                    "ORDER BY e.deck_id,e.sort_order",
                    deck_ids,
                ).fetchall()
                for commander in commander_rows:
                    deck_id = str(commander["deck_id"])
                    commander_decks.add(deck_id)
                    commander_oracles.setdefault(deck_id, []).append(
                        str(commander["oracle_id"])
                    )
                    if commander["card_id"] and deck_id not in commander_card_by_deck:
                        commander_card_by_deck[deck_id] = str(commander["card_id"])
                    if (
                        commander["image_uri"]
                        and deck_id not in commander_image_by_deck
                    ):
                        commander_image_by_deck[deck_id] = str(commander["image_uri"])
                    commander_colors_by_deck.setdefault(deck_id, set()).update(
                        _json(commander["color_identity"], [])
                    )
                tag_rows = conn.execute(
                    "SELECT a.deck_id,t.id,t.name FROM deck_tag_assignments a "
                    "JOIN deck_tags t ON t.id=a.tag_id "
                    f"WHERE a.deck_id IN ({placeholders}) "
                    "ORDER BY a.deck_id,a.created_at,t.name COLLATE NOCASE",
                    deck_ids,
                ).fetchall()
                for tag in tag_rows:
                    tags_by_deck.setdefault(str(tag["deck_id"]), []).append(
                        {"id": tag["id"], "name": tag["name"]}
                    )
            for item in out:
                deck_id = str(item["id"])
                commander_colors = commander_colors_by_deck.get(deck_id, set())
                item["color_identity"] = [
                    color for color in "WUBRG" if color in commander_colors
                ]
                item["is_colorless"] = (
                    deck_id in commander_decks and not commander_colors
                )
                item["commander_card_id"] = commander_card_by_deck.get(deck_id)
                identity = (
                    pair_id(commander_oracles[deck_id])
                    if len(commander_oracles.get(deck_id, [])) == 2
                    else item["commander_card_id"]
                )
                item["commander_identity_id"] = (
                    identity
                    if conn.execute(
                        "SELECT 1 FROM research_commanders WHERE id=?", (identity,)
                    ).fetchone()
                    else None
                )
                item["commander_image_uri"] = commander_image_by_deck.get(deck_id)
                tags = tags_by_deck.get(deck_id, [])
                item["tags"] = tags[:4]
                item["hidden_tag_count"] = max(0, len(tags) - 4)
            return out

    def search_tags(self, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Find canonical tags, ranked by use across all Deck Lab users."""
        normalized_query = unicodedata.normalize("NFKC", query).strip().casefold()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT t.id,t.name,COUNT(a.deck_id) AS usage_count
                   FROM deck_tags t
                   LEFT JOIN deck_tag_assignments a ON a.tag_id=t.id
                   WHERE t.normalized_name LIKE ?
                   GROUP BY t.id
                   ORDER BY usage_count DESC,t.name COLLATE NOCASE
                   LIMIT ?""",
                (f"%{normalized_query}%", max(1, min(limit, 50))),
            ).fetchall()
        return [dict(row) for row in rows]

    def library_stats(self, owner_id: str) -> dict[str, int]:
        """Return stable counts for the deck-library header and filters."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT
                       COUNT(*) AS total_count,
                       COALESCE(SUM(CASE WHEN favorite=1 THEN 1 ELSE 0 END),0)
                           AS favorite_count,
                       COALESCE(SUM(CASE
                           WHEN datetime(updated_at)>=datetime('now','-30 days')
                           THEN 1 ELSE 0 END),0) AS recent_count,
                       COALESCE(SUM(CASE
                           WHEN datetime(updated_at)>=datetime('now','-7 days')
                           THEN 1 ELSE 0 END),0) AS edited_week_count
                   FROM deck_documents WHERE owner_id=?""",
                (owner_id,),
            ).fetchone()
        keys = (
            "total_count",
            "favorite_count",
            "recent_count",
            "edited_week_count",
        )
        return {key: int(row[key]) for key in keys}

    def get(self, owner_id: str, deck_id: str) -> dict[str, Any]:
        return self._get(deck_id, owner_id=owner_id)

    def get_shared(self, token: str) -> dict[str, Any]:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as conn:
            grant = conn.execute(
                "SELECT deck_id FROM deck_share_grants "
                "WHERE token_digest=? AND revoked_at IS NULL",
                (digest,),
            ).fetchone()
        if not grant:
            raise DeckNotFound()
        document = self._get(str(grant["deck_id"]), owner_id=None)
        for key in ("owner_id", "source_kind", "source_id"):
            document.pop(key, None)
        return document

    def _get(self, deck_id: str, owner_id: str | None) -> dict[str, Any]:
        with self._connect() as conn:
            if owner_id is None:
                row = conn.execute(
                    "SELECT * FROM deck_documents WHERE id=?", (deck_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM deck_documents WHERE id=? AND owner_id=?",
                    (deck_id, owner_id),
                ).fetchone()
            if row is None:
                raise DeckNotFound()
            document = dict(row)
            zones = [
                dict(z)
                for z in conn.execute(
                    "SELECT * FROM deck_zones WHERE deck_id=? ORDER BY sort_order",
                    (deck_id,),
                ).fetchall()
            ]
            entries = [
                dict(e)
                for e in conn.execute(
                    """SELECT e.id,e.deck_id,e.zone_id,e.card_id,e.oracle_id,
                              e.name,e.quantity,e.is_commander,e.sort_order,e.role,
                              e.type_line,e.mana_cost,e.mana_value,e.oracle_text,
                              e.color_identity,COALESCE(e.image_uri,c.image_uri) AS image_uri,
                              c.is_legal_in_99, c.is_legal_commander
                       FROM deck_entries e LEFT JOIN cards c ON c.id=e.card_id
                       WHERE e.deck_id=? """
                    "ORDER BY e.is_commander DESC, e.sort_order, e.name",
                    (deck_id,),
                ).fetchall()
            ]
            presentation_row = conn.execute(
                "SELECT * FROM deck_presentations WHERE deck_id=?", (deck_id,)
            ).fetchone()
            playmats: list[dict[str, Any]] = []
            if owner_id:
                playmats = AccountPlaymatRepo.list_for_owner_conn(conn, owner_id)
            pref_row = (
                conn.execute(
                    "SELECT * FROM deck_view_preferences WHERE deck_id=? AND owner_id=?",
                    (deck_id, owner_id),
                ).fetchone()
                if owner_id
                else None
            )
            tags = [
                dict(tag)
                for tag in conn.execute(
                    """SELECT t.id,t.name FROM deck_tag_assignments a
                       JOIN deck_tags t ON t.id=a.tag_id WHERE a.deck_id=?
                       ORDER BY a.created_at,t.name COLLATE NOCASE""",
                    (deck_id,),
                ).fetchall()
            ]
        zone_names = {z["id"]: z["name"] for z in zones}
        for entry in entries:
            entry["zone_name"] = zone_names.get(entry["zone_id"], "Unsorted")
            entry["color_identity"] = _json(entry.get("color_identity"), [])
            format_legal = entry.pop("is_legal_in_99", None)
            commander_flag = entry.pop("is_legal_commander", None)
            entry["format_legal"] = format_legal in (1, True)
            entry["commander_legal"] = (
                commander_flag in (1, True) and entry["format_legal"]
            )
        if document.get("visibility") not in {"private", "public"}:
            document["visibility"] = "private"
        document["zones"] = zones
        document["entries"] = entries
        raw_presentation = dict(presentation_row) if presentation_row else {}
        if owner_id:
            owned_ids = {str(item["id"]) for item in playmats}
            document["presentation"] = owner_presentation(
                raw_presentation, owned_ids=owned_ids
            )
            document["playmats"] = [
                {"id": item["id"], "title": item["title"]} for item in playmats
            ]
        else:
            document["presentation"] = public_presentation(raw_presentation)
        document["preferences"] = (
            dict(pref_row)
            if pref_row
            else {
                "view_mode": "playmat",
                "display_mode": "text",
                "group_mode": "zone",
                "sort_mode": "manual",
                "density": "compact",
                "collapsed_json": "[]",
            }
        )
        document["validation"] = self.validate(entries)
        document["tags"] = tags
        document["tag_suggestions"] = self.search_tags(limit=40)
        return document

    @staticmethod
    def validate(entries: list[dict[str, Any]]) -> dict[str, Any]:
        entries = [
            e
            for e in entries
            if e["is_commander"] or _is_public_library_zone(e.get("zone_name"))
        ]
        commander_entries = [e for e in entries if e["is_commander"]]
        commanders = sum(int(e["quantity"]) for e in commander_entries)
        library = sum(int(e["quantity"]) for e in entries if not e["is_commander"])
        issues: list[str] = []
        if commanders not in (1, 2):
            issues.append("Choose one commander or a legal partner pair.")
        elif commanders == 2 and len(commander_entries) != 2:
            issues.append("Each commander in a pair must be a different card.")
        elif commanders == 2:
            if not compatible_pair(*commander_entries):
                issues.append("The two commanders do not form a recognized legal pair.")
        library_target = 100 - commanders if commanders in (1, 2) else 99
        if library != library_target:
            issues.append(f"The library has {library} of {library_target} cards.")
        library_entries = [e for e in entries if not e["is_commander"]]
        counts = Counter(
            e.get("oracle_id") or e["name"].casefold() for e in library_entries
        )
        if any(count > 1 for count in counts.values()):
            issues.append("The same card appears in more than one entry.")
        singleton_violations = []
        for entry in library_entries:
            text = (entry.get("oracle_text") or "").casefold()
            basic = (entry.get("type_line") or "").casefold().startswith("basic land")
            exception = (
                "any number of cards named" in text or "up to seven cards named" in text
            )
            if int(entry["quantity"]) > 1 and not (basic or exception):
                singleton_violations.append(entry["name"])
        if singleton_violations:
            issues.append("Commander singleton rule is exceeded.")
        allowed = {
            color
            for entry in entries
            if entry["is_commander"]
            for color in _json(entry.get("color_identity"), [])
        }
        if commander_entries and any(
            not set(_json(entry.get("color_identity"), [])).issubset(allowed)
            for entry in library_entries
        ):
            issues.append("A card is outside the commander's color identity.")
        if any(entry.get("format_legal") is False for entry in library_entries):
            issues.append(
                "This deck contains cards that are not Commander-legal. "
                "They were kept so you can inspect and remove them."
            )
        if any(entry.get("commander_legal") is False for entry in commander_entries):
            issues.append(
                "A commander in this deck is no longer Commander-legal. "
                "It was kept so you can inspect and replace it."
            )
        return {
            "commander_count": commanders,
            "library_count": library,
            "library_target": library_target,
            "total_count": commanders + library,
            "legal": not issues,
            "issues": issues,
        }

    @staticmethod
    def _resolve_unique_commander(
        conn: sqlite3.Connection, value: str
    ) -> dict[str, Any] | None:
        """Exact card id or unique case-insensitive name; never guess a prefix."""
        key = str(value or "").strip()
        if not key:
            return None
        row = conn.execute(
            "SELECT id, oracle_id, name, type_line, mana_cost, cmc, oracle_text, "
            "color_identity, image_uri, is_legal_commander, is_legal_in_99 "
            "FROM cards WHERE id=?",
            (key,),
        ).fetchone()
        if row and row["is_legal_commander"] and row["is_legal_in_99"]:
            return dict(row)
        rows = conn.execute(
            "SELECT id, oracle_id, name, type_line, mana_cost, cmc, oracle_text, "
            "color_identity, image_uri, is_legal_commander, is_legal_in_99 "
            "FROM cards WHERE is_legal_commander=1 AND is_legal_in_99=1 "
            "AND name=? COLLATE NOCASE "
            "ORDER BY image_uri IS NULL, id",
            (key,),
        ).fetchall()
        oracles = {str(item["oracle_id"] or item["id"]) for item in rows}
        if len(oracles) != 1:
            return None
        return dict(rows[0])

    def _card_has_compatible_partner(
        self, conn: sqlite3.Connection, card: dict[str, Any]
    ) -> bool:
        if not can_participate_in_pair(card):
            return False
        oracle_id = str(card.get("oracle_id") or card.get("id") or "")
        candidates = conn.execute(
            "SELECT id, oracle_id, name, type_line, oracle_text, color_identity, "
            "is_legal_commander, is_legal_in_99 "
            "FROM cards WHERE is_legal_in_99=1 AND is_legal_commander=1 "
            "AND COALESCE(oracle_id, id) != ? "
            "ORDER BY name, id",
            (oracle_id,),
        )
        seen: set[str] = set()
        for row in candidates:
            other = dict(row)
            other_oracle = str(other.get("oracle_id") or other.get("id") or "")
            if other_oracle in seen:
                continue
            seen.add(other_oracle)
            if compatible_pair(card, other):
                return True
        return False

    def public_deck_commander_state(
        self, commander: str = "", partner: str = ""
    ) -> dict[str, Any]:
        """UI flags for the Decks filter panel. Partner text is cleared when stale."""
        commander_text = str(commander or "").strip()[:120]
        partner_text = str(partner or "").strip()[:120]
        with self._connect() as conn:
            primary = self._resolve_unique_commander(conn, commander_text)
            show_partner = primary is not None and self._card_has_compatible_partner(
                conn, primary
            )
        if not show_partner:
            partner_text = ""
        return {
            "commander": commander_text,
            "partner": partner_text,
            "show_partner": show_partner,
            "commander_id": str(primary["id"]) if primary else "",
        }

    def suggest_deck_commanders(
        self, *, query: str = "", partner_of: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        """Bounded public commander metadata for Decks autocomplete. Cards table only."""
        limit = max(1, min(int(limit or 20), 40))
        query = str(query or "").strip()[:120]
        partner_of = str(partner_of or "").strip()
        if partner_of:
            return [
                {
                    "id": str(card["id"]),
                    "name": card["name"],
                    "can_pair": False,
                }
                for card in self.partner_choices(partner_of, query=query)[:limit]
            ]
        if not query:
            return []
        cards = self.search_cards(query=query, commander_only=True, limit=limit)
        if not cards:
            return []
        with self._connect() as conn:
            pool: list[dict[str, Any]] | None = None
            results: list[dict[str, Any]] = []
            for card in cards:
                can_pair = False
                if can_participate_in_pair(card):
                    if pool is None:
                        pool = [
                            dict(row)
                            for row in conn.execute(
                                "SELECT id, oracle_id, name, type_line, oracle_text, "
                                "color_identity, is_legal_commander, is_legal_in_99 "
                                "FROM cards WHERE is_legal_in_99=1 AND is_legal_commander=1"
                            )
                        ]
                    oracle_id = str(card.get("oracle_id") or card.get("id") or "")
                    for other in pool:
                        other_oracle = str(
                            other.get("oracle_id") or other.get("id") or ""
                        )
                        if other_oracle == oracle_id:
                            continue
                        if compatible_pair(card, other):
                            can_pair = True
                            break
                results.append(
                    {
                        "id": str(card["id"]),
                        "name": card["name"],
                        "can_pair": can_pair,
                    }
                )
        return results

    def _apply_public_commander_filter(
        self,
        conn: sqlite3.Connection,
        where: list[str],
        params: list[Any],
        commander: str,
        partner: str,
    ) -> bool:
        """Match commander entries only. False means the combination yields no rows."""
        commander_text = str(commander or "").strip()[:120]
        partner_text = str(partner or "").strip()[:120]
        if not commander_text:
            return not partner_text
        primary = self._resolve_unique_commander(conn, commander_text)
        if partner_text:
            if primary is None or not self._card_has_compatible_partner(conn, primary):
                # Invalid direct query must not broaden the requested pair.
                return False
            else:
                secondary = self._resolve_unique_commander(conn, partner_text)
                primary_key = str(primary.get("oracle_id") or primary["id"])
                secondary_key = (
                    str(secondary.get("oracle_id") or secondary["id"])
                    if secondary
                    else ""
                )
                if (
                    secondary is None
                    or primary_key == secondary_key
                    or not compatible_pair(primary, secondary)
                ):
                    return False
                where.append(
                    "EXISTS (SELECT 1 FROM deck_entries e WHERE e.deck_id=d.id "
                    "AND e.is_commander=1 AND (e.oracle_id=? OR e.card_id=?))"
                )
                where.append(
                    "EXISTS (SELECT 1 FROM deck_entries e WHERE e.deck_id=d.id "
                    "AND e.is_commander=1 AND (e.oracle_id=? OR e.card_id=?))"
                )
                params.extend(
                    [
                        primary.get("oracle_id") or primary["id"],
                        primary["id"],
                        secondary.get("oracle_id") or secondary["id"],
                        secondary["id"],
                    ]
                )
                return True
        if primary is not None:
            where.append(
                "EXISTS (SELECT 1 FROM deck_entries e WHERE e.deck_id=d.id "
                "AND e.is_commander=1 AND (e.oracle_id=? OR e.card_id=?))"
            )
            params.extend([primary.get("oracle_id") or primary["id"], primary["id"]])
            return True
        where.append(
            "EXISTS (SELECT 1 FROM deck_entries e WHERE e.deck_id=d.id "
            "AND e.is_commander=1 AND e.name LIKE ?)"
        )
        params.append(f"%{commander_text}%")
        return True

    @staticmethod
    def _apply_public_color_filter(
        where: list[str],
        params: list[Any],
        colors: list[str] | None,
        color_mode: str,
    ) -> None:
        """Union of commander-entry identities. Decks without commanders are skipped."""
        selected: list[str] = []
        for color in colors or []:
            if color in set("WUBRGC") and color not in selected:
                selected.append(color)
        mode = (
            color_mode if color_mode in {"include", "exclude", "exactly"} else "include"
        )
        if not selected:
            return
        where.append(
            "EXISTS (SELECT 1 FROM deck_entries e "
            "WHERE e.deck_id=d.id AND e.is_commander=1)"
        )
        where.append(
            "NOT EXISTS (SELECT 1 FROM deck_entries e WHERE e.deck_id=d.id "
            "AND e.is_commander=1 AND "
            "(CASE WHEN json_valid(e.color_identity) THEN json_type(e.color_identity) "
            "ELSE NULL END) IS NOT 'array')"
        )
        colored = [color for color in selected if color != "C"]
        wants_colorless = "C" in selected
        has_color = (
            "EXISTS (SELECT 1 FROM deck_entries e WHERE e.deck_id=d.id "
            "AND e.is_commander=1 AND e.color_identity LIKE ?)"
        )
        identity_empty = (
            "NOT EXISTS (SELECT 1 FROM deck_entries e WHERE e.deck_id=d.id "
            "AND e.is_commander=1 AND json_array_length(CASE WHEN json_valid(e.color_identity) THEN e.color_identity ELSE '[]' END)>0)"
        )
        union_size = (
            "(SELECT COUNT(DISTINCT value) FROM deck_entries e, json_each(e.color_identity) "
            "WHERE e.deck_id=d.id AND e.is_commander=1)"
        )
        if mode == "exclude":
            for color in colored:
                where.append(f"NOT ({has_color})")
                params.append(f'%"{color}"%')
            if wants_colorless:
                where.append(f"NOT ({identity_empty})")
            return
        if wants_colorless and not colored:
            where.append(identity_empty)
            return
        for color in colored:
            where.append(has_color)
            params.append(f'%"{color}"%')
        if mode == "exactly":
            where.append(f"{union_size}=?")
            params.append(len(colored))
            if wants_colorless and colored:
                where.append("1=0")

    def partner_choices(
        self, commander_id: str, *, query: str = ""
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            first = self._card_row(conn, commander_id)
            if (
                not first
                or not first.get("is_legal_commander")
                or not first.get("is_legal_in_99")
            ):
                return []
            candidates = conn.execute(
                "SELECT * FROM cards WHERE is_legal_in_99=1 AND is_legal_commander=1 "
                "AND name LIKE ? ORDER BY name,id",
                (f"%{query}%",),
            ).fetchall()
        matches: dict[str, dict[str, Any]] = {}
        for row in candidates:
            card = dict(row)
            if compatible_pair(first, card):
                card["color_identity"] = _json(card.get("color_identity"), [])
                matches.setdefault(str(card["oracle_id"]), card)
        return list(matches.values())[:40]

    def search_cards(
        self,
        *,
        query: str = "",
        commander_only: bool = False,
        oracle_text: str = "",
        type_line: str = "",
        mana_max: float | None = None,
        rarity: str = "",
        allowed_colors: set[str] | None = None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        where = ["c.name LIKE ?"]
        params: list[Any] = [f"%{query}%"]
        if commander_only:
            where.append("c.is_legal_commander=1")
            where.append("c.is_legal_in_99=1")
        else:
            where.append(format_legal_sql("c"))
        if oracle_text:
            where.append("c.oracle_text LIKE ?")
            params.append(f"%{oracle_text}%")
        if type_line:
            where.append("c.type_line LIKE ?")
            params.append(f"%{type_line}%")
        if mana_max is not None:
            where.append("c.cmc<=?")
            params.append(mana_max)
        if rarity:
            where.append("c.rarity=?")
            params.append(rarity)
        if allowed_colors is not None:
            if allowed_colors:
                placeholders = ",".join("?" for _ in allowed_colors)
                where.append(
                    "NOT EXISTS (SELECT 1 FROM json_each(c.color_identity) "
                    f"WHERE value NOT IN ({placeholders}))"
                )
                params.extend(sorted(allowed_colors))
            else:
                where.append("json_array_length(c.color_identity)=0")
        params.append(max(1, min(limit, 100)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT c.id, c.oracle_id, c.name, c.type_line, c.mana_cost,
                           c.cmc AS mana_value, c.oracle_text, c.color_identity,
                           c.image_uri, c.rarity
                    FROM cards c WHERE {' AND '.join(where)}
                      AND c.id=(SELECT c2.id FROM cards c2 WHERE c2.name=c.name
                                ORDER BY c2.image_uri IS NULL, c2.id LIMIT 1)
                    ORDER BY CASE WHEN c.name LIKE ? THEN 0 ELSE 1 END,
                             c.name COLLATE NOCASE LIMIT ?""",
                [*params[:-1], f"{query}%", params[-1]],
            ).fetchall()
        results = [dict(row) for row in rows]
        for card in results:
            card["color_identity"] = _json(card.get("color_identity"), [])
        return results

    @staticmethod
    def _owned_revision(
        conn: sqlite3.Connection, owner_id: str, deck_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM deck_documents WHERE id=? AND owner_id=?",
            (deck_id, owner_id),
        ).fetchone()
        if row is None:
            raise DeckNotFound()
        return cast(sqlite3.Row, row)

    def apply_commands(
        self,
        owner_id: str,
        deck_id: str,
        *,
        expected_revision: int,
        mutation_id: str,
        commands: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not mutation_id or len(mutation_id) > 100:
            raise InvalidCommand("A mutation id is required.")
        if not commands or len(commands) > 100:
            raise InvalidCommand("Send between 1 and 100 changes.")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            document = self._owned_revision(conn, owner_id, deck_id)
            seen = conn.execute(
                "SELECT response_revision FROM deck_mutations "
                "WHERE deck_id=? AND owner_id=? AND mutation_id=?",
                (deck_id, owner_id, mutation_id),
            ).fetchone()
            if seen:
                conn.rollback()
                return self.get(owner_id, deck_id)
            if int(document["revision"]) != expected_revision:
                current = int(document["revision"])
                conn.rollback()
                raise RevisionConflict(current)
            for command in commands:
                self._apply_command(conn, owner_id, deck_id, command)
            revision = expected_revision + 1
            now = _now()
            conn.execute(
                "UPDATE deck_documents SET revision=?, updated_at=? WHERE id=?",
                (revision, now, deck_id),
            )
            conn.execute(
                "INSERT INTO deck_mutations "
                "(deck_id, owner_id, mutation_id, response_revision) VALUES (?, ?, ?, ?)",
                (deck_id, owner_id, mutation_id, revision),
            )
            self._event(
                conn,
                owner_id,
                "deck.edited",
                deck_id,
                {
                    "commands": [str(c.get("type") or "") for c in commands],
                    "cards_added": sum(
                        int(c.get("quantity") or 1)
                        for c in commands
                        if c.get("type") == "add_card"
                    ),
                },
            )
            conn.commit()
        return self.get(owner_id, deck_id)

    def _zone_exists(
        self, conn: sqlite3.Connection, deck_id: str, zone_id: str
    ) -> bool:
        return bool(
            conn.execute(
                "SELECT 1 FROM deck_zones WHERE id=? AND deck_id=?",
                (zone_id, deck_id),
            ).fetchone()
        )

    def _entry_exists(
        self, conn: sqlite3.Connection, deck_id: str, entry_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM deck_entries WHERE id=? AND deck_id=?",
            (entry_id, deck_id),
        ).fetchone()
        if not row:
            raise InvalidCommand("That card is no longer in this deck.")
        return cast(sqlite3.Row, row)

    def _apply_command(
        self,
        conn: sqlite3.Connection,
        owner_id: str,
        deck_id: str,
        command: dict[str, Any],
    ) -> None:
        kind = str(command.get("type") or "")
        if kind == "rename_deck":
            title = str(command.get("title") or "").strip()[:160]
            if not title:
                raise InvalidCommand("Deck name cannot be empty.")
            conn.execute(
                "UPDATE deck_documents SET title=? WHERE id=?", (title, deck_id)
            )
        elif kind == "toggle_favorite":
            conn.execute(
                "UPDATE deck_documents SET favorite=CASE favorite WHEN 1 THEN 0 ELSE 1 END WHERE id=?",
                (deck_id,),
            )
        elif kind == "add_tag":
            name, normalized_name = _clean_tag_name(command.get("name") or "")
            tag = conn.execute(
                "SELECT id FROM deck_tags WHERE normalized_name=?",
                (normalized_name,),
            ).fetchone()
            if tag is None:
                tag_id = db.new_id()
                conn.execute(
                    "INSERT INTO deck_tags(id,name,normalized_name,created_by) "
                    "VALUES(?,?,?,?)",
                    (tag_id, name, normalized_name, owner_id),
                )
            else:
                tag_id = str(tag["id"])
            assigned = conn.execute(
                "SELECT 1 FROM deck_tag_assignments WHERE deck_id=? AND tag_id=?",
                (deck_id, tag_id),
            ).fetchone()
            if assigned is None:
                tag_count = conn.execute(
                    "SELECT COUNT(*) FROM deck_tag_assignments WHERE deck_id=?",
                    (deck_id,),
                ).fetchone()[0]
                if int(tag_count) >= 6:
                    raise InvalidCommand("A deck can have up to six tags.")
                conn.execute(
                    "INSERT INTO deck_tag_assignments(deck_id,tag_id,created_by) "
                    "VALUES(?,?,?)",
                    (deck_id, tag_id, owner_id),
                )
        elif kind == "remove_tag":
            tag_id = str(command.get("tag_id") or "")
            if not tag_id:
                raise InvalidCommand("Choose a tag to remove.")
            conn.execute(
                "DELETE FROM deck_tag_assignments WHERE deck_id=? AND tag_id=?",
                (deck_id, tag_id),
            )
        elif kind == "create_zone":
            name = str(command.get("name") or "New zone").strip()[:60]
            if not name:
                raise InvalidCommand("Zone name cannot be empty.")
            order = conn.execute(
                "SELECT COALESCE(MAX(sort_order),-1)+1 FROM deck_zones WHERE deck_id=?",
                (deck_id,),
            ).fetchone()[0]
            try:
                conn.execute(
                    "INSERT INTO deck_zones(id,deck_id,name,sort_order,x,y) VALUES(?,?,?,?,?,?)",
                    (
                        str(command.get("zone_id") or db.new_id()),
                        deck_id,
                        name,
                        order,
                        float(command.get("x") or 120 + order * 40),
                        float(command.get("y") or 160 + order * 30),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise InvalidCommand("Zone names must be unique.") from exc
        elif kind == "rename_zone":
            zone_id = str(command.get("zone_id") or "")
            name = str(command.get("name") or "").strip()[:60]
            if not name or not self._zone_exists(conn, deck_id, zone_id):
                raise InvalidCommand("Choose a valid zone and name.")
            current = conn.execute(
                "SELECT name FROM deck_zones WHERE id=?", (zone_id,)
            ).fetchone()[0]
            if str(current).casefold() == "unsorted":
                raise InvalidCommand("Unsorted is the permanent fallback zone.")
            try:
                conn.execute("UPDATE deck_zones SET name=? WHERE id=?", (name, zone_id))
            except sqlite3.IntegrityError as exc:
                raise InvalidCommand("Zone names must be unique.") from exc
        elif kind == "delete_zone":
            zone_id = str(command.get("zone_id") or "")
            zone = conn.execute(
                "SELECT name FROM deck_zones WHERE id=? AND deck_id=?",
                (zone_id, deck_id),
            ).fetchone()
            if not zone or str(zone["name"]).casefold() == "unsorted":
                raise InvalidCommand("Unsorted cannot be deleted.")
            unsorted = conn.execute(
                "SELECT id FROM deck_zones WHERE deck_id=? AND name='Unsorted'",
                (deck_id,),
            ).fetchone()
            conn.execute(
                "UPDATE deck_entries SET zone_id=? WHERE deck_id=? AND zone_id=?",
                (unsorted["id"], deck_id, zone_id),
            )
            conn.execute(
                "DELETE FROM deck_zones WHERE id=? AND deck_id=?", (zone_id, deck_id)
            )
        elif kind == "set_zone_layout":
            zone_id = str(command.get("zone_id") or "")
            layout = str(command.get("layout") or "")
            if layout not in {"spread", "fan"} or not self._zone_exists(
                conn, deck_id, zone_id
            ):
                raise InvalidCommand("Choose a valid zone layout.")
            conn.execute(
                "UPDATE deck_zones SET layout_mode=? WHERE id=?", (layout, zone_id)
            )
        elif kind == "set_commanders":
            cards = self._commander_cards(conn, command.get("card_ids", []))
            # Replacement is atomic and shares the revision/undo event path.
            conn.execute(
                "DELETE FROM deck_entries WHERE deck_id=? AND is_commander=1",
                (deck_id,),
            )
            for commander_card in cards:
                existing = conn.execute(
                    "SELECT 1 FROM deck_entries WHERE deck_id=? AND oracle_id=?",
                    (deck_id, commander_card["oracle_id"]),
                ).fetchone()
                if existing:
                    raise InvalidCommand(
                        "Remove that card from the library before making it a commander."
                    )
                self._insert_card(
                    conn,
                    deck_id=deck_id,
                    zone_id=None,
                    card=commander_card,
                    is_commander=True,
                )
        elif kind == "add_card":
            card_id = str(command.get("card_id") or "")
            card = self._card_row(conn, card_id)
            if not card:
                raise InvalidCommand("Card not found.")
            is_commander = bool(command.get("is_commander"))
            if is_commander and (
                not card.get("is_legal_commander") or not card.get("is_legal_in_99")
            ):
                raise InvalidCommand("That card is not a legal commander.")
            if is_commander:
                current = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM deck_entries WHERE deck_id=? AND is_commander=1",
                        (deck_id,),
                    )
                ]
                if len(current) >= 2 or (
                    current and not compatible_pair(current[0], card)
                ):
                    raise InvalidCommand(
                        "Choose a legal partner for the current commander."
                    )
            if not is_commander and not card.get("is_legal_in_99"):
                raise InvalidCommand("That card is not legal in a Commander library.")
            if not is_commander:
                commander_rows = conn.execute(
                    "SELECT color_identity FROM deck_entries "
                    "WHERE deck_id=? AND is_commander=1",
                    (deck_id,),
                ).fetchall()
                allowed = {
                    color
                    for row in commander_rows
                    for color in _json(row["color_identity"], [])
                }
                card_colors = set(_json(card.get("color_identity"), []))
                if commander_rows and not card_colors.issubset(allowed):
                    raise InvalidCommand(
                        "That card is outside the commander's color identity."
                    )
            destination_zone_id: str | None = (
                None if is_commander else str(command.get("zone_id") or "")
            )
            if (
                not is_commander
                and destination_zone_id is not None
                and not self._zone_exists(conn, deck_id, destination_zone_id)
            ):
                raise InvalidCommand("Choose a destination zone.")
            quantity = max(1, min(99, int(command.get("quantity") or 1)))
            existing = conn.execute(
                "SELECT id,quantity FROM deck_entries WHERE deck_id=? "
                "AND COALESCE(oracle_id,card_id)=COALESCE(?,?) AND is_commander=? "
                "AND COALESCE(zone_id,'')=COALESCE(?,'')",
                (
                    deck_id,
                    card.get("oracle_id"),
                    card_id,
                    1 if is_commander else 0,
                    destination_zone_id,
                ),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE deck_entries SET quantity=MIN(99,quantity+?) WHERE id=?",
                    (quantity, existing["id"]),
                )
            else:
                order = conn.execute(
                    "SELECT COALESCE(MAX(sort_order),-1)+1 FROM deck_entries WHERE deck_id=? AND COALESCE(zone_id,'')=COALESCE(?,'')",
                    (deck_id, destination_zone_id),
                ).fetchone()[0]
                self._insert_card(
                    conn,
                    deck_id=deck_id,
                    zone_id=destination_zone_id,
                    card=card,
                    quantity=quantity,
                    is_commander=is_commander,
                    role=str(command.get("role") or "") or None,
                    order=order,
                )
        elif kind == "remove_entry":
            entry_id = str(command.get("entry_id") or "")
            self._entry_exists(conn, deck_id, entry_id)
            conn.execute(
                "DELETE FROM deck_entries WHERE id=? AND deck_id=?", (entry_id, deck_id)
            )
        elif kind == "set_quantity":
            entry_id = str(command.get("entry_id") or "")
            self._entry_exists(conn, deck_id, entry_id)
            quantity = int(command.get("quantity") or 0)
            if quantity <= 0:
                conn.execute("DELETE FROM deck_entries WHERE id=?", (entry_id,))
            elif quantity <= 99:
                conn.execute(
                    "UPDATE deck_entries SET quantity=? WHERE id=?",
                    (quantity, entry_id),
                )
            else:
                raise InvalidCommand("Quantity must be between 0 and 99.")
        elif kind == "adjust_quantity":
            entry_id = str(command.get("entry_id") or "")
            entry = self._entry_exists(conn, deck_id, entry_id)
            delta = int(command.get("delta") or 0)
            if delta not in {-1, 1}:
                raise InvalidCommand("Quantity adjustments must be one card at a time.")
            quantity = int(entry["quantity"]) + delta
            if quantity <= 0:
                conn.execute("DELETE FROM deck_entries WHERE id=?", (entry_id,))
            elif quantity <= 99:
                conn.execute(
                    "UPDATE deck_entries SET quantity=? WHERE id=?",
                    (quantity, entry_id),
                )
            else:
                raise InvalidCommand("Quantity must be between 0 and 99.")
        elif kind == "move_entry":
            entry_id = str(command.get("entry_id") or "")
            entry = self._entry_exists(conn, deck_id, entry_id)
            if entry["is_commander"]:
                raise InvalidCommand("Commanders stay in the command section.")
            zone_id = str(command.get("zone_id") or "")
            if not self._zone_exists(conn, deck_id, zone_id):
                raise InvalidCommand("Destination zone not found.")
            order = int(command.get("sort_order") or 0)
            conn.execute(
                "UPDATE deck_entries SET zone_id=?,sort_order=? WHERE id=?",
                (zone_id, order, entry_id),
            )
        elif kind == "set_role":
            entry_id = str(command.get("entry_id") or "")
            entry = self._entry_exists(conn, deck_id, entry_id)
            if entry["is_commander"]:
                raise InvalidCommand("Commanders do not use card roles.")
            role = str(command.get("role") or "").strip().lower()
            valid_roles = {
                "",
                "ramp",
                "draw",
                "removal",
                "protection",
                "counter",
                "free",
                "tutor",
                "combo",
                "engine",
                "board_wipe",
                "recursion",
                "wincon",
                "land",
                "utility",
                "other",
            }
            if role not in valid_roles:
                raise InvalidCommand("Choose a valid card role.")
            conn.execute(
                "UPDATE deck_entries SET role=? WHERE id=?",
                (role or None, entry_id),
            )
        elif kind == "update_view":
            option_sets = {
                "view_mode": {"table", "playmat"},
                "display_mode": {"text", "grid", "spoiler"},
                "group_mode": {"zone", "type"},
                "sort_mode": {"manual", "name", "mana_value"},
                "density": {"compact", "comfortable"},
            }
            values: dict[str, str] = {}
            for key, valid in option_sets.items():
                if key in command:
                    value = str(command[key])
                    if value not in valid:
                        raise InvalidCommand(f"Invalid {key}.")
                    values[key] = value
            if "collapsed" in command:
                values["collapsed_json"] = json.dumps(command["collapsed"])
            conn.execute(
                "INSERT OR IGNORE INTO deck_view_preferences(owner_id,deck_id,density) "
                "VALUES(?,?,'compact')",
                (owner_id, deck_id),
            )
            for key, value in values.items():
                conn.execute(
                    f"UPDATE deck_view_preferences SET {key}=? WHERE owner_id=? AND deck_id=?",
                    (value, owner_id, deck_id),
                )
        elif kind == "update_presentation":
            updates: dict[str, Any] = {}
            requested_playmat = (
                command.get("playmat_id") if "playmat_id" in command else None
            )
            if requested_playmat not in (None, ""):
                playmat_id = str(requested_playmat)
                owned = AccountPlaymatRepo.get_owned_conn(conn, owner_id, playmat_id)
                if owned is None:
                    raise InvalidCommand("Unknown playmat surface.")
                updates["playmat_id"] = playmat_id
                updates["surface"] = LIBRARY_SURFACE
            else:
                if "playmat_id" in command:
                    updates["playmat_id"] = None
                if "surface" in command:
                    surface = str(command["surface"])
                    if surface in BUILTIN_SURFACES:
                        updates["surface"] = surface
                        updates["playmat_id"] = None
                    elif surface == CUSTOM_SURFACE:
                        updates["surface"] = CUSTOM_SURFACE
                        updates["playmat_id"] = None
                    else:
                        raise InvalidCommand("Unknown playmat surface.")
            for key in ("snap_to_grid", "show_zone_outlines", "dim_inactive"):
                if key in command:
                    updates[key] = 1 if command[key] else 0
            for key in ("pan_x", "pan_y"):
                if key in command:
                    updates[key] = max(-10000.0, min(10000.0, float(command[key])))
            if "zoom" in command:
                updates["zoom"] = max(0.25, min(2.5, float(command["zoom"])))
            if "canvas_width" in command:
                updates["canvas_width"] = max(
                    1200, min(2400, int(command["canvas_width"]))
                )
            if "canvas_height" in command:
                updates["canvas_height"] = max(
                    900, min(1800, int(command["canvas_height"]))
                )
            for key, value in updates.items():
                conn.execute(
                    f"UPDATE deck_presentations SET {key}=? WHERE deck_id=?",
                    (value, deck_id),
                )
        elif kind == "move_zone":
            zone_id = str(command.get("zone_id") or "")
            if not self._zone_exists(conn, deck_id, zone_id):
                raise InvalidCommand("Zone not found.")
            presentation = conn.execute(
                "SELECT canvas_width,canvas_height FROM deck_presentations WHERE deck_id=?",
                (deck_id,),
            ).fetchone()
            max_x = max(
                0.0, float(presentation["canvas_width"] if presentation else 1600) - 180
            )
            max_y = max(
                0.0, float(presentation["canvas_height"] if presentation else 900) - 100
            )
            conn.execute(
                "UPDATE deck_zones SET x=?,y=? WHERE id=?",
                (
                    max(0.0, min(max_x, float(command.get("x") or 0))),
                    max(0.0, min(max_y, float(command.get("y") or 0))),
                    zone_id,
                ),
            )
        else:
            raise InvalidCommand(f"Unsupported command: {kind or 'missing type'}")

    def set_visibility(self, owner_id: str, deck_id: str, visibility: str) -> str:
        """Owner-only private/public switch. Share links do not change this.

        Visibility is stored independently of content revisions so undo/redo of
        card edits cannot republish or unpublish a deck.
        """
        if visibility not in {"private", "public"}:
            raise InvalidCommand("Choose private or public.")
        with self._connect() as conn:
            self._owned_revision(conn, owner_id, deck_id)
            conn.execute(
                "UPDATE deck_documents SET visibility=?, updated_at=? WHERE id=?",
                (visibility, _now(), deck_id),
            )
        return visibility

    def list_public(
        self,
        *,
        query: str = "",
        page: int = 1,
        per_page: int = 24,
        colors: list[str] | None = None,
        color_mode: str = "include",
        commander: str = "",
        partner: str = "",
    ) -> dict[str, Any]:
        """Paginated discovery of currently public decks for signed-in users."""
        page = max(1, page)
        per_page = max(1, min(per_page, 48))
        where = ["d.visibility='public'"]
        params: list[Any] = []
        if query:
            where.append(
                "(d.title LIKE ? OR EXISTS (SELECT 1 FROM deck_entries ec "
                "WHERE ec.deck_id=d.id AND ec.is_commander=1 AND ec.name LIKE ?))"
            )
            term = f"%{query}%"
            params.extend([term, term])
        self._apply_public_color_filter(where, params, colors, color_mode)
        with self._connect() as conn:
            if not self._apply_public_commander_filter(
                conn, where, params, commander, partner
            ):
                return {
                    "results": [],
                    "total": 0,
                    "page": page,
                    "has_next": False,
                }
            count = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM deck_documents d WHERE {' AND '.join(where)}",
                    params,
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""SELECT d.id, d.title, d.updated_at,
                           COALESCE(NULLIF(u.display_name, ''), 'Deck Lab player') AS display_name
                    FROM deck_documents d
                    JOIN users u ON u.id=d.owner_id
                    WHERE {' AND '.join(where)}
                    ORDER BY d.updated_at DESC, d.id DESC LIMIT ? OFFSET ?""",
                [*params, per_page, (page - 1) * per_page],
            ).fetchall()
            results = [dict(row) for row in rows]
            if results:
                deck_ids = [str(item["id"]) for item in results]
                placeholders = ",".join("?" for _ in deck_ids)
                commander_rows = conn.execute(
                    "SELECT e.deck_id,e.name,COALESCE(e.image_uri,c.image_uri) AS image_uri "
                    "FROM deck_entries e LEFT JOIN cards c ON c.id=e.card_id "
                    f"WHERE e.is_commander=1 AND e.deck_id IN ({placeholders}) "
                    "ORDER BY e.deck_id,e.sort_order",
                    deck_ids,
                ).fetchall()
                by_deck: dict[str, list[dict[str, Any]]] = {}
                for row in commander_rows:
                    by_deck.setdefault(str(row["deck_id"]), []).append(
                        {"name": row["name"], "image_uri": row["image_uri"]}
                    )
                for item in results:
                    commanders = by_deck.get(str(item["id"]), [])
                    item["commanders"] = commanders
                    item["commander_names"] = " / ".join(c["name"] for c in commanders)
                    item["commander_image_uri"] = (
                        commanders[0]["image_uri"] if commanders else None
                    )
        return {
            "results": results,
            "total": count,
            "page": page,
            "has_next": page * per_page < count,
        }

    def get_public(self, deck_id: str) -> dict[str, Any]:
        """Read-only public projection. Re-checks visibility on every call."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT d.id, d.title, d.updated_at,
                          COALESCE(NULLIF(u.display_name, ''), 'Deck Lab player') AS display_name
                   FROM deck_documents d JOIN users u ON u.id=d.owner_id
                   WHERE d.id=? AND d.visibility='public'""",
                (deck_id,),
            ).fetchone()
            if row is None:
                raise DeckNotFound()
            entries = conn.execute(
                """SELECT e.card_id,e.name,e.quantity,e.is_commander,e.type_line,
                          COALESCE(e.image_uri,c.image_uri) AS image_uri,
                          z.name AS zone_name
                   FROM deck_entries e LEFT JOIN cards c ON c.id=e.card_id
                   LEFT JOIN deck_zones z ON z.id=e.zone_id
                   WHERE e.deck_id=? ORDER BY e.is_commander DESC, e.name COLLATE NOCASE""",
                (deck_id,),
            ).fetchall()
        commanders = []
        groups: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            if not entry["is_commander"] and not _is_public_library_zone(
                entry["zone_name"]
            ):
                continue
            item = {
                "id": entry["card_id"],
                "name": entry["name"] or "Unknown card",
                "quantity": int(entry["quantity"] or 1),
                "type_line": entry["type_line"] or "",
                "image_uri": entry["image_uri"],
            }
            if entry["is_commander"]:
                commanders.append(item)
                continue
            groups.setdefault(primary_type(item["type_line"]), []).append(item)
        grouped = [
            {"type": kind, "cards": groups[kind]}
            for kind in (
                "Creature",
                "Planeswalker",
                "Battle",
                "Instant",
                "Sorcery",
                "Artifact",
                "Enchantment",
                "Land",
                "Other",
            )
            if kind in groups
        ]
        return {
            "id": row["id"],
            "title": row["title"],
            "display_name": row["display_name"],
            "updated_at": row["updated_at"],
            "commanders": commanders,
            "groups": grouped,
        }

    def create_share(self, owner_id: str, deck_id: str) -> str:
        with self._connect() as conn:
            self._owned_revision(conn, owner_id, deck_id)
            conn.execute(
                "UPDATE deck_share_grants SET revoked_at=? WHERE deck_id=? AND revoked_at IS NULL",
                (_now(), deck_id),
            )
            token = secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO deck_share_grants(id,deck_id,token_digest) VALUES(?,?,?)",
                (db.new_id(), deck_id, hashlib.sha256(token.encode()).hexdigest()),
            )
        return token

    def revoke_shares(self, owner_id: str, deck_id: str) -> None:
        with self._connect() as conn:
            self._owned_revision(conn, owner_id, deck_id)
            conn.execute(
                "UPDATE deck_share_grants SET revoked_at=? WHERE deck_id=? AND revoked_at IS NULL",
                (_now(), deck_id),
            )

    def delete(self, owner_id: str, deck_id: str) -> str | None:
        """Delete an owned editable deck.

        Returns a leftover deck-scoped custom playmat path, never a reusable
        library image.
        """
        with self._connect() as conn:
            self._owned_revision(conn, owner_id, deck_id)
            presentation = conn.execute(
                "SELECT custom_surface_path FROM deck_presentations WHERE deck_id=?",
                (deck_id,),
            ).fetchone()
            self._event(conn, owner_id, "deck.deleted", deck_id)
            conn.execute("DELETE FROM deck_documents WHERE id=?", (deck_id,))
            leftover = (
                str(presentation["custom_surface_path"])
                if presentation and presentation["custom_surface_path"]
                else None
            )
            if (
                leftover
                and conn.execute(
                    "SELECT 1 FROM account_playmats WHERE file_path=?", (leftover,)
                ).fetchone()
            ):
                leftover = None
        return leftover

    def assign_library_playmat(
        self, owner_id: str, deck_id: str, playmat_id: str
    ) -> str | None:
        """Point this deck at an owned library mat. Returns a replaced custom path."""
        with self._connect() as conn:
            self._owned_revision(conn, owner_id, deck_id)
            owned = AccountPlaymatRepo.get_owned_conn(conn, owner_id, playmat_id)
            if owned is None:
                raise InvalidCommand("Unknown playmat surface.")
            previous = conn.execute(
                "SELECT custom_surface_path FROM deck_presentations WHERE deck_id=?",
                (deck_id,),
            ).fetchone()
            conn.execute(
                "UPDATE deck_presentations SET surface=?, playmat_id=?, "
                "custom_surface_path=NULL WHERE deck_id=?",
                (LIBRARY_SURFACE, playmat_id, deck_id),
            )
            conn.execute(
                "UPDATE deck_documents SET revision=revision+1,updated_at=? WHERE id=?",
                (_now(), deck_id),
            )
            self._event(conn, owner_id, "deck.playmat_uploaded", deck_id)
            leftover = str(previous[0]) if previous and previous[0] else None
            if (
                leftover
                and conn.execute(
                    "SELECT 1 FROM account_playmats WHERE file_path=?", (leftover,)
                ).fetchone()
            ):
                leftover = None
        return leftover

    def set_custom_surface(self, owner_id: str, deck_id: str, path: str) -> str | None:
        """Select a validated uploaded surface and return the replaced path.

        New uploads should use the account library. This keeps a deck-scoped
        custom path working for existing files without deleting library images.
        """
        with self._connect() as conn:
            self._owned_revision(conn, owner_id, deck_id)
            previous = conn.execute(
                "SELECT custom_surface_path FROM deck_presentations WHERE deck_id=?",
                (deck_id,),
            ).fetchone()
            conn.execute(
                "UPDATE deck_presentations SET surface=?, custom_surface_path=?, "
                "playmat_id=NULL WHERE deck_id=?",
                (CUSTOM_SURFACE, path, deck_id),
            )
            conn.execute(
                "UPDATE deck_documents SET revision=revision+1,updated_at=? WHERE id=?",
                (_now(), deck_id),
            )
            self._event(conn, owner_id, "deck.playmat_uploaded", deck_id)
            leftover = str(previous[0]) if previous and previous[0] else None
            if (
                leftover
                and conn.execute(
                    "SELECT 1 FROM account_playmats WHERE file_path=?", (leftover,)
                ).fetchone()
            ):
                leftover = None
        return leftover

    @staticmethod
    def export_text(document: dict[str, Any]) -> str:
        lines: list[str] = [f"// {document['title']}", ""]
        commanders = [e for e in document["entries"] if e["is_commander"]]
        if commanders:
            lines.append("Commander")
            lines.extend(f"{e['quantity']} {e['name']}" for e in commanders)
            lines.append("")
        entries_by_zone: dict[str, list[dict[str, Any]]] = {}
        zone_names = {z["id"]: z["name"] for z in document["zones"]}
        for entry in document["entries"]:
            if not entry["is_commander"]:
                entries_by_zone.setdefault(
                    zone_names.get(entry["zone_id"], "Unsorted"), []
                ).append(entry)
        for zone in document["zones"]:
            entries = entries_by_zone.get(zone["name"], [])
            if not entries:
                continue
            lines.append(zone["name"])
            lines.extend(
                f"{e['quantity']} {e['name']}"
                for e in sorted(entries, key=lambda item: item["name"].casefold())
            )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
