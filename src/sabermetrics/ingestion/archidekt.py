"""Corrected Archidekt commander-scoped decklist ingestion.

Replaces the previously broken commander filter. Archidekt's
``/api/decks/v3/`` endpoint **ignores** the ``commanders=`` parameter
entirely — it returns the global top-1000 decks regardless of value
(verified: identical ``count`` for a real commander, a rare one, gibberish,
and integer IDs). The only working filter is ``cardName=<EXACT full card
name>``, which returns decks that *contain* that card.

Because "contains the card" includes decks that merely run the card in the
99, every candidate is verified against the deck-detail endpoint
(``GET /api/decks/{id}/``): the card must occupy the ``Commander`` category
before the deck is stored.

Popularity caveat (surfaced deliberately, per design):
    Decks are pulled sorted by ``orderBy=-favorites``. "Most favorited" is a
    POPULARITY PROXY, not a power or correctness signal. It biases toward
    early-posted decks, established creators, and decks with good writeups.
    ``popularity_rank`` is stored as a first-class column so this bias stays
    visible to every downstream consumer rather than hidden inside an
    aggregate. Archidekt does not expose a favorites *count* in either the
    search or detail payload, so rank (ordinal position in the favorites
    sort) is the only popularity signal available.

Creator archetype tags (``tags``) and the creator-declared power bracket
(``edhBracket``) ride along free in the search payload and are persisted for
Phase 1 signature-library validation. Coverage is partial (~19% tags, ~26%
bracket on a per-commander pool) — treat them as a labeled subset, not a
complete labeling.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from sabermetrics.ingestion._decklist_base import DecklistIngestionBase
from sabermetrics.ingestion.base import SyncResult
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

ARCHIDEKT_API_URL = "https://archidekt.com/api"
ARCHIDEKT_DECK_URL = "https://archidekt.com/decks"
COMMANDER_FORMAT_ID = 3
USER_AGENT = "bristly_billy_beane/0.1 (personal MTG research tool)"

# orderBy values confirmed to work against /api/decks/v3/.
VALID_SORTS = {"-favorites", "-viewCount", "-createdAt", "-numFollowers"}

# Safety cap on search pages walked per commander (60 decks/page).
_MAX_SEARCH_PAGES = 15


# ---------------------------------------------------------------------------
# Pure parsing / verification helpers (network-free, unit-testable)
# ---------------------------------------------------------------------------


def parse_deck_detail(
    data: dict[str, Any],
) -> tuple[list[str], list[tuple[str, int, bool]]]:
    """Extract commander names and the *actual* deck list from a detail payload.

    Cards that live only in categories the creator flagged ``includedInDeck:
    false`` — Maybeboard, "Consider Adding", "Remove from Deck", etc. — are NOT
    part of the deck and are excluded. Ingesting them badly inflates downstream
    inclusion rates (a card a player is merely *considering* would count as
    played).

    Args:
        data: Parsed JSON from ``GET /api/decks/{id}/``.

    Returns:
        A tuple of ``(commander_names, cards)`` where ``commander_names`` is
        the list of ``Commander``-category cards and ``cards`` is a list of
        ``(card_name, quantity, is_commander)`` tuples for the real deck only.
    """
    # Map category name -> whether it counts toward the deck (default True).
    category_included: dict[str, bool] = {}
    for cat in data.get("categories") or []:
        name = cat.get("name")
        if name is not None:
            category_included[name] = cat.get("includedInDeck", True)

    commander_names: list[str] = []
    cards: list[tuple[str, int, bool]] = []

    for entry in data.get("cards", []) or []:
        oracle = (entry.get("card") or {}).get("oracleCard") or {}
        card_name = oracle.get("name")
        if not card_name:
            continue

        categories = entry.get("categories") or []
        # Exclude cards whose every category is flagged not-in-deck (maybeboard).
        if categories and all(
            category_included.get(cat, True) is False for cat in categories
        ):
            continue

        quantity = entry.get("quantity", 1) or 1
        is_commander = "Commander" in categories

        if is_commander:
            commander_names.append(card_name)
        cards.append((card_name, quantity, is_commander))

    return commander_names, cards


def commander_matches(intended_name: str, commander_names: list[str]) -> bool:
    """Check whether the intended commander occupies the deck's Commander slot.

    Case-insensitive; tolerates partner/background decks where more than one
    card sits in the Commander category.

    Args:
        intended_name: Exact card name we searched for.
        commander_names: Names found in the deck's ``Commander`` category.

    Returns:
        True if ``intended_name`` is one of the deck's commanders.
    """
    target = intended_name.strip().lower()
    return any(target == n.strip().lower() for n in commander_names)


def extract_summary_metadata(summary: dict[str, Any]) -> dict[str, Any]:
    """Pull the metadata fields we persist from a search-result deck summary.

    Args:
        summary: One element of the ``results`` array from ``/api/decks/v3/``.

    Returns:
        Dict with ``creator``, ``power_tier``, ``tags`` (list[str]),
        ``view_count``, ``has_primer``, ``created_at``, ``updated_at``.
    """
    owner = summary.get("owner")
    if isinstance(owner, dict):
        creator = owner.get("username")
    elif isinstance(owner, str):
        creator = owner
    else:
        creator = None

    tags = [
        t.get("name")
        for t in (summary.get("tags") or [])
        if isinstance(t, dict) and t.get("name")
    ]

    bracket = summary.get("edhBracket")
    power_tier = int(bracket) if isinstance(bracket, int) else None

    return {
        "creator": creator,
        "power_tier": power_tier,
        "tags": tags,
        "view_count": summary.get("viewCount"),
        "has_primer": bool(summary.get("hasPrimer")),
        "created_at": summary.get("createdAt"),
        "updated_at": summary.get("updatedAt"),
    }


# ---------------------------------------------------------------------------
# Ingestion source
# ---------------------------------------------------------------------------


class ArchidektIngestion(DecklistIngestionBase):
    """Commander-scoped Archidekt decklist ingestion.

    Primary entry point is :meth:`ingest_commander`. :meth:`sync` is a thin
    batch wrapper that ingests a set of commanders for scheduled refreshes.
    """

    name: str = "archidekt"
    source_name: str = "archidekt"

    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._rate_limiter = RateLimiter(requests_per_second=1.0)
        self._ensure_schema()

    # -- setup ---------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Add the two additive columns this source needs (idempotent)."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(decks)")}
            if "popularity_rank" not in existing:
                conn.execute("ALTER TABLE decks ADD COLUMN popularity_rank INTEGER")
            if "archetype_tags" not in existing:
                conn.execute("ALTER TABLE decks ADD COLUMN archetype_tags TEXT")
            conn.commit()
        finally:
            conn.close()

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": USER_AGENT}

    def is_available(self) -> bool:
        """Check if Archidekt is reachable."""
        try:
            self._rate_limiter.wait()
            resp = httpx.get(
                "https://archidekt.com",
                timeout=10,
                follow_redirects=True,
                headers=self._headers(),
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    # -- commander resolution ------------------------------------------------

    def _resolve_commander(self, commander: str) -> tuple[str, str] | None:
        """Resolve a commander id-or-name to its canonical ``(id, name)``.

        Args:
            commander: A Scryfall card id or an exact/partial card name.

        Returns:
            ``(card_id, exact_name)`` or None if no legal commander matched.
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                "SELECT id, name FROM cards "
                "WHERE (id = ? OR name = ?) AND is_legal_commander = 1 LIMIT 1",
                (commander, commander),
            )
            row = cursor.fetchone()
            if row:
                return row[0], row[1]
            # Fall back to a case-insensitive name match.
            cursor = conn.execute(
                "SELECT id, name FROM cards "
                "WHERE name LIKE ? AND is_legal_commander = 1 LIMIT 1",
                (commander,),
            )
            row = cursor.fetchone()
            return (row[0], row[1]) if row else None
        finally:
            conn.close()

    # -- search --------------------------------------------------------------

    def _fetch_search_page(
        self, exact_name: str, sort: str, page: int
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch one page of the commander's decks (cardName filter).

        Args:
            exact_name: Exact full card name to filter on.
            sort: An ``orderBy`` value from :data:`VALID_SORTS`.
            page: 1-based page number.

        Returns:
            ``(results, has_next)``. ``results`` is empty on error.
        """
        self._rate_limiter.wait()
        try:
            resp = httpx.get(
                f"{ARCHIDEKT_API_URL}/decks/v3/",
                params={
                    "deckFormat": COMMANDER_FORMAT_ID,
                    "cardName": exact_name,
                    "orderBy": sort,
                    "page": page,
                },
                headers=self._headers(),
                timeout=20,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                logger.debug(
                    "[archidekt] search page %d for '%s' returned %d",
                    page, exact_name, resp.status_code,
                )
                return [], False
            data = resp.json()
            results = data.get("results") or []
            return results, bool(data.get("next"))
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            logger.warning(
                "[archidekt] search page %d for '%s' failed: %s",
                page, exact_name, e,
            )
            return [], False

    # -- store ---------------------------------------------------------------

    def _fetch_verify_store(
        self,
        deck_id: int | str,
        url: str,
        intended_id: str,
        intended_name: str,
        summary: dict[str, Any],
        rank: int,
        sort: str,
    ) -> bool:
        """Fetch a deck's detail, verify its commander, and persist it.

        Args:
            deck_id: Archidekt deck id.
            url: Canonical deck URL (used as ``source_id``).
            intended_id: Scryfall id of the commander we searched for.
            intended_name: Exact name of the commander we searched for.
            summary: The search-result summary (source of tags/bracket/etc.).
            rank: 1-based popularity rank among verified decks.
            sort: The ``orderBy`` used (recorded for provenance).

        Returns:
            True if the deck was verified and stored.
        """
        self._rate_limiter.wait()
        try:
            resp = httpx.get(
                f"{ARCHIDEKT_API_URL}/decks/{deck_id}/",
                headers=self._headers(),
                timeout=20,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                logger.debug(
                    "[archidekt] detail for deck %s returned %d",
                    deck_id, resp.status_code,
                )
                return False
            data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            logger.warning("[archidekt] detail fetch for deck %s failed: %s", deck_id, e)
            return False

        commander_names, cards = parse_deck_detail(data)
        if not commander_matches(intended_name, commander_names):
            # The card is in the deck but not as the commander — reject.
            logger.debug(
                "[archidekt] deck %s not commanded by '%s' (commanders=%s); skipping",
                deck_id, intended_name, commander_names,
            )
            return False
        if not cards:
            return False

        meta = extract_summary_metadata(summary)
        deck_name = summary.get("name") or data.get("name") or f"Deck {deck_id}"

        raw_data = json.dumps({
            "archidekt_deck_id": deck_id,
            "sort_used": sort,
            "popularity_rank": rank,
            "view_count": meta["view_count"],
            "power_bracket": meta["power_tier"],
            "has_primer": meta["has_primer"],
            "tags": meta["tags"],
            "created_at": meta["created_at"],
            "updated_at": meta["updated_at"],
            "commanders": commander_names,
        })

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            # Clear any prior version of this deck, children first — deck_cards
            # has no ON DELETE CASCADE, so INSERT OR REPLACE on the parent would
            # fail the FK constraint while old deck_cards still reference it.
            existing = conn.execute(
                "SELECT id FROM decks WHERE source = ? AND source_id = ?",
                (self.source_name, url),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    "DELETE FROM deck_cards WHERE deck_id = ?", (existing[0],)
                )
                conn.execute("DELETE FROM decks WHERE id = ?", (existing[0],))

            new_deck_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO decks
                (id, source, source_id, commander_id, deck_name, creator,
                 power_tier, popularity_rank, archetype_tags, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_deck_id,
                    self.source_name,
                    url,
                    intended_id,
                    deck_name,
                    meta["creator"],
                    meta["power_tier"],
                    rank,
                    json.dumps(meta["tags"]),
                    raw_data,
                ),
            )

            stored_cards = 0
            for card_name, quantity, is_commander in cards:
                card_id = self._resolve_card_id(conn, card_name)
                if card_id:
                    conn.execute(
                        """INSERT OR IGNORE INTO deck_cards
                        (deck_id, card_id, quantity, is_commander)
                        VALUES (?, ?, ?, ?)""",
                        (new_deck_id, card_id, quantity, is_commander),
                    )
                    stored_cards += 1

            conn.commit()
            logger.debug(
                "[archidekt] stored deck %s (rank %d, %d/%d cards resolved)",
                deck_id, rank, stored_cards, len(cards),
            )
            return True
        except sqlite3.Error as e:
            conn.rollback()
            logger.warning("[archidekt] DB error storing deck %s: %s", deck_id, e)
            return False
        finally:
            conn.close()

    # -- public API ----------------------------------------------------------

    def ingest_commander(
        self,
        commander: str,
        target: int = 100,
        sort: str = "-favorites",
        full: bool = False,
        max_candidates: int = 600,
    ) -> SyncResult:
        """Ingest a commander's most-popular decks (Phase 2 primary path).

        Walks the ``cardName``-filtered search in popularity order, verifies
        each candidate is actually commanded by ``commander``, and stores up
        to ``target`` verified decks with their popularity rank and creator
        metadata.

        Because the ``cardName`` filter returns decks that merely *contain*
        the card, commander-precision at the top of the popularity sort can be
        low (~13-20% for premium 99-includable commanders like Korvold). The
        walk therefore stops at the first of two bounds: ``target`` verified
        decks, or ``max_candidates`` decks examined. A commander that hits the
        candidate cap short of ``target`` is logged as a warning — that is the
        signal it lacks enough distinct popular decks, not a silent failure.

        Args:
            commander: Scryfall id or exact/partial name of the commander.
            target: Number of verified commander decks to collect.
            sort: Popularity sort; must be one of :data:`VALID_SORTS`.
            full: If True, re-fetch and overwrite decks already stored.
            max_candidates: Hard cap on candidate decks examined (cost bound).

        Returns:
            A :class:`SyncResult` describing the run.
        """
        started_at = datetime.now()
        errors: list[str] = []
        ingested = 0
        failed = 0

        if sort not in VALID_SORTS:
            raise ValueError(
                f"sort must be one of {sorted(VALID_SORTS)}, got {sort!r}"
            )

        resolved = self._resolve_commander(commander)
        if resolved is None:
            msg = f"Commander not found or not legal: {commander!r}"
            logger.warning("[archidekt] %s", msg)
            self._update_source_health(success=False, error=msg)
            return SyncResult(
                source_name=self.name,
                started_at=started_at,
                completed_at=datetime.now(),
                items_ingested=0,
                items_updated=0,
                items_failed=0,
                errors=[msg],
                success=False,
            )

        commander_id, exact_name = resolved
        logger.info(
            "[archidekt] ingesting up to %d '%s' decks by %s",
            target, exact_name, sort,
        )

        try:
            seen: set[Any] = set()
            candidates_examined = 0
            page = 1
            while (
                ingested < target
                and candidates_examined < max_candidates
                and page <= _MAX_SEARCH_PAGES
            ):
                summaries, has_next = self._fetch_search_page(exact_name, sort, page)
                if not summaries:
                    break

                for summary in summaries:
                    if ingested >= target or candidates_examined >= max_candidates:
                        break
                    deck_id = summary.get("id")
                    if not deck_id or deck_id in seen:
                        continue
                    seen.add(deck_id)
                    candidates_examined += 1
                    url = f"{ARCHIDEKT_DECK_URL}/{deck_id}"

                    # Already have it: count toward target so re-runs converge
                    # cheaply, unless a full refresh was requested.
                    if not full and self._deck_exists(url):
                        ingested += 1
                        continue

                    ok = self._fetch_verify_store(
                        deck_id=deck_id,
                        url=url,
                        intended_id=commander_id,
                        intended_name=exact_name,
                        summary=summary,
                        rank=ingested + 1,
                        sort=sort,
                    )
                    if ok:
                        ingested += 1
                    else:
                        failed += 1

                if not has_next:
                    break
                page += 1

            self._update_source_health(success=True)
            success = True
            logger.info(
                "[archidekt] '%s': %d decks stored, %d rejected/failed "
                "(examined %d/%d candidates over %d pages)",
                exact_name, ingested, failed, candidates_examined,
                max_candidates, page,
            )
            if ingested < target and candidates_examined >= max_candidates:
                logger.warning(
                    "[archidekt] '%s' hit the %d-candidate cap with only %d/%d "
                    "verified decks — likely lacks enough distinct popular decks",
                    exact_name, max_candidates, ingested, target,
                )
        except Exception as e:  # noqa: BLE001 — report, don't crash the run
            errors.append(str(e))
            self._update_source_health(success=False, error=str(e))
            success = False
            logger.warning("[archidekt] ingest for '%s' failed: %s", exact_name, e)

        return SyncResult(
            source_name=self.name,
            started_at=started_at,
            completed_at=datetime.now(),
            items_ingested=ingested,
            items_updated=0,
            items_failed=failed,
            errors=errors,
            success=success,
        )

    def sync(
        self,
        full: bool = False,
        commanders: list[str] | None = None,
        per_commander_target: int = 25,
    ) -> SyncResult:
        """Batch-ingest several commanders (scheduled-refresh wrapper).

        Intended for light/scheduled refreshes; the deliberate per-commander
        Phase 2 pull (target ~100) is the ``pull-decks`` CLI runner, which calls
        :meth:`ingest_commander` directly. The default target here is kept low
        so a blind all-sources ``sync`` sweep stays bounded.

        Args:
            full: Passed through to :meth:`ingest_commander`.
            commanders: Commander ids/names to ingest. If None, uses the
                DB's legal-commander list (bounded).
            per_commander_target: Decks to pull per commander (low by default).

        Returns:
            An aggregate :class:`SyncResult`.
        """
        started_at = datetime.now()

        if commanders is None:
            commanders = [name for _cid, name in self._get_top_commanders(limit=20)]

        total_ingested = 0
        total_failed = 0
        errors: list[str] = []
        any_success = False

        for commander in commanders:
            result = self.ingest_commander(
                commander,
                target=per_commander_target,
                full=full,
            )
            total_ingested += result.items_ingested
            total_failed += result.items_failed
            errors.extend(result.errors)
            any_success = any_success or result.success

        return SyncResult(
            source_name=self.name,
            started_at=started_at,
            completed_at=datetime.now(),
            items_ingested=total_ingested,
            items_updated=0,
            items_failed=total_failed,
            errors=errors,
            success=any_success,
        )
