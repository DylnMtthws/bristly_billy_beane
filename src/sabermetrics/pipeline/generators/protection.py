"""Protection package generator.

Deterministic protection spell selection with role-specific quality scoring.
Fills commander protection slots (hexproof, indestructible, phasing, etc.)
that were previously only available via the greedy optimizer.
"""

import json
import logging
import re
import sqlite3
from pathlib import Path

import yaml

from sabermetrics.analytics.empirical_valuation import (
    annotate_empirical,
    empirical_bonus,
)
from sabermetrics.config import settings
from sabermetrics.pipeline.greedy_optimizer import is_playable_as_land
from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.slot_assigner import SlotAssignment

logger = logging.getLogger(__name__)

# --- Protection quality regexes ---

_PHASING = re.compile(
    r"phase(?:s)? out",
    re.IGNORECASE,
)
_HEXPROOF = re.compile(
    r"hexproof|can't be the target",
    re.IGNORECASE,
)
_INDESTRUCTIBLE = re.compile(
    r"indestructible|can't be destroyed",
    re.IGNORECASE,
)
_REDIRECT = re.compile(
    r"change the target|choose new targets|changes? its target",
    re.IGNORECASE,
)
_BOARD_WIDE = re.compile(
    r"permanents you control|creatures you control|each (?:creature|permanent) you control",
    re.IGNORECASE,
)
_FREE_CAST = re.compile(
    r"without paying (?:its|their) mana cost|if you control a commander",
    re.IGNORECASE,
)
_PROTECTION_FROM = re.compile(
    r"protection from",
    re.IGNORECASE,
)
_SHROUD = re.compile(
    r"shroud",
    re.IGNORECASE,
)
_TOTEM_ARMOR = re.compile(
    r"totem armor",
    re.IGNORECASE,
)
_WARD = re.compile(
    r"ward",
    re.IGNORECASE,
)


def _coverage_score(oracle: str) -> float:
    """Score protection coverage type (1.0-4.0).

    Phasing is best (dodges everything including exile/sacrifice).
    Hexproof + indestructible together is next.
    Then individual protection modes.
    """
    has_phasing = bool(_PHASING.search(oracle))
    has_hexproof = bool(_HEXPROOF.search(oracle))
    has_indestructible = bool(_INDESTRUCTIBLE.search(oracle))
    has_redirect = bool(_REDIRECT.search(oracle))
    has_protection_from = bool(_PROTECTION_FROM.search(oracle))
    has_shroud = bool(_SHROUD.search(oracle))
    has_totem = bool(_TOTEM_ARMOR.search(oracle))
    has_ward = bool(_WARD.search(oracle))

    if has_phasing:
        return 4.0
    if has_hexproof and has_indestructible:
        return 3.0
    if has_redirect:
        return 2.5
    if has_indestructible:
        return 2.0
    if has_protection_from:
        return 1.8
    if has_hexproof or has_shroud:
        return 1.5
    if has_totem:
        return 1.3
    if has_ward:
        return 1.0
    return 1.0


def _score_protection(
    card: dict,
    commander_colors: list[str],
    avg_cmc: float,
) -> float:
    """Score a protection card on role-specific quality.

    Signals:
    - Coverage type: 1.0-4.0 (phasing > hex+indestructible > redirect > ...)
    - Breadth (board vs single): 0.0-1.5
    - Mana efficiency: 0.0-3.0 (free spells are premium)
    - Instant speed: +2.0 or -1.0
    - CVAR blend at 40%

    Args:
        card: Card dict with oracle_text, cmc, type_line, _cvar_score.
        commander_colors: Commander's color identity.
        avg_cmc: Target average CMC for the deck.

    Returns:
        Combined quality score (higher is better).
    """
    oracle = card.get("oracle_text") or ""
    cmc = float(card.get("cmc", 3) or 3)
    type_line = (card.get("type_line") or "").lower()
    cvar = float(card.get("_cvar_score", 0.3) or 0.3)

    role_score = 0.0

    # --- Coverage type ---
    role_score += _coverage_score(oracle)

    # --- Breadth (board-wide vs single target) ---
    if _BOARD_WIDE.search(oracle):
        role_score += 1.5
    else:
        role_score += 0.0

    # --- Mana efficiency ---
    if _FREE_CAST.search(oracle):
        role_score += 3.0
    elif cmc <= 1:
        role_score += 2.5
    elif cmc <= 2:
        role_score += 2.0
    elif cmc <= 3:
        role_score += 1.0
    else:
        role_score += 0.0

    # --- Instant speed (essential for reactive protection) ---
    if "instant" in type_line or "flash" in oracle.lower():
        role_score += 2.0
    elif "sorcery" in type_line:
        role_score -= 1.0
    else:
        # Creatures/enchantments with protection abilities — moderate
        role_score += 0.5

    # --- Blend with CVAR (40% CVAR, 60% role-specific) ---
    # Max theoretical role_score ~10.5; normalize to 0-1
    normalized_role = min(role_score / 10.5, 1.0)
    final_score = 0.60 * normalized_role + 0.40 * cvar

    # --- Empirical grounding: additive, never penalizes absence (ADR-005) ---
    final_score += empirical_bonus(
        card,
        settings.scoring.generator_empirical_weight,
        settings.scoring.generator_empirical_noisy_weight,
    )

    return final_score


def _load_protection_auto_includes() -> tuple[dict, set[str]]:
    """Load auto-include cards from config.

    Returns:
        Tuple of (auto_includes_dict, protected_names_set).
    """
    config_path = Path(__file__).resolve().parent.parent.parent.parent.parent / "config" / "auto_include_cards.yaml"
    if not config_path.exists():
        return {}, set()
    with open(config_path) as f:
        data = yaml.safe_load(f) or {}
    protected: set[str] = set()
    for section_entries in data.values():
        if not isinstance(section_entries, list):
            continue
        for entry in section_entries:
            if entry.get("protect_from_swap", False):
                protected.add(entry["name"])
    return data, protected


class ProtectionPackageGenerator:
    """Generate the protection package for a deck."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.protected_names: set[str] = set()

    def _load_protection_candidates(
        self,
        color_identity: list[str],
    ) -> list[dict]:
        """Load pre-scored protection candidates from the protection_candidates table.

        Joins with cards table to get full card data. Filters by color identity
        and Commander legality.

        Args:
            color_identity: Commander's color identity.

        Returns:
            List of card dicts augmented with protection_score from protection_candidates.
        """
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row

            color_set = set(color_identity)
            cursor = conn.execute(
                "SELECT c.id, c.name, c.oracle_text, c.type_line, c.cmc, "
                "c.color_identity, c.mana_cost, c.role_tags, c.keywords, "
                "p.protection_type, p.protection_score, p.is_board_wide, "
                "p.is_instant, p.is_free_cast, p.coverage_score "
                "FROM protection_candidates p "
                "JOIN cards c ON p.card_id = c.id "
                "WHERE c.is_legal_in_99 = 1 "
                "ORDER BY p.protection_score DESC"
            )

            results: list[dict] = []
            for row in cursor:
                card = dict(row)
                card_colors_raw = card.get("color_identity") or "[]"
                if isinstance(card_colors_raw, str):
                    try:
                        card_colors = json.loads(card_colors_raw)
                    except (json.JSONDecodeError, TypeError):
                        card_colors = []
                else:
                    card_colors = card_colors_raw
                if not all(c in color_set for c in card_colors):
                    continue

                price_row = conn.execute(
                    "SELECT price_usd FROM card_prices "
                    "WHERE card_id = ? ORDER BY snapshot_date DESC LIMIT 1",
                    (card["id"],),
                ).fetchone()
                card["price_usd"] = price_row["price_usd"] if price_row else 0.0

                results.append(card)

            conn.close()
            return results
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            logger.warning("Failed to load protection_candidates: %s", e)
            return []

    def generate(
        self,
        color_identity: list[str],
        target_count: int,
        budget_remaining: float,
        template: DeckTemplate,
        already_placed: list[dict],
        role_tag_pool: list[dict],
        commander_colors: list[str] | None = None,
        avg_cmc: float | None = None,
        pool_index: dict[str, dict] | None = None,
    ) -> list[SlotAssignment]:
        """Generate protection package with auto-includes and role-specific scoring.

        Prefers the protection_candidates table (pre-scored, reminder-text-stripped)
        over the role_tag_pool. Falls back to role_tag_pool if the table is
        empty or unavailable.

        Args:
            color_identity: Commander's color identity.
            target_count: Target number of protection cards.
            budget_remaining: Remaining deck budget.
            template: Deck template for context.
            already_placed: Cards already in the deck.
            role_tag_pool: Pre-filtered cards with role_tags containing "protection".
            commander_colors: Commander's color identity (defaults to color_identity).
            avg_cmc: Target average CMC (defaults to template value).

        Returns:
            List of SlotAssignment for protection cards.
        """
        colors = commander_colors or color_identity
        deck_avg_cmc = avg_cmc or template.avg_cmc_target

        auto_includes, self.protected_names = _load_protection_auto_includes()
        used_names = {c.get("name", "") for c in already_placed}
        assignments: list[SlotAssignment] = []
        running_price = 0.0

        # --- Auto-include protection staples ---
        auto_prot_names: set[str] = set()
        for entry in auto_includes.get("protection_always", []):
            if entry.get("role") == "protection":
                auto_prot_names.add(entry["name"])

        # Try loading protection_candidates table
        prot_candidates = self._load_protection_candidates(color_identity)
        use_candidates_table = len(prot_candidates) > 0

        if use_candidates_table:
            pool = prot_candidates
            # Candidate-table cards are loaded fresh from SQL; carry the
            # empirical annotations over from role_tag_pool so the bonus applies.
            annotate_empirical(pool, role_tag_pool)
            logger.info("Using protection_candidates table (%d cards)", len(pool))
        else:
            pool = role_tag_pool
            logger.info("Falling back to role_tag_pool (%d cards)", len(pool))

        # Lands are the land package's domain; placing one here inflates the
        # deck's land total past the template target.
        pool = [
            c for c in pool
            if not is_playable_as_land(c.get("type_line") or "")
            and not c.get("_anti_engine")
        ]
        # Inherit the filtered pool's gates: drop table rows not in the pool
        # (excluded there for price/legality/ceiling reasons) and copy its
        # flags and scores onto the survivors.
        if pool_index is not None:
            gated = []
            for c in pool:
                src = pool_index.get(c.get("name", ""))
                if src is None:
                    continue
                # id/price too: the table row may be a different printing
                # with no price snapshot -- it then costs $0 in build-time
                # budget sums and renders at the $0.05 floor in the UI. The
                # pool's printing (cheapest priced) is canonical.
                for k in ("_anti_engine", "_cvar_score",
                          "_empirical_inclusion", "_empirical_reliable",
                          "id", "oracle_id", "set_code", "price_usd"):
                    if k in src:
                        c[k] = src[k]
                if c.get("_anti_engine"):
                    continue
                gated.append(c)
            pool = gated


        # Place auto-includes from pool (or role_tag_pool as backup)
        search_pools = [pool] if use_candidates_table else [role_tag_pool]
        if use_candidates_table:
            search_pools.append(role_tag_pool)

        for search_pool in search_pools:
            for card in search_pool:
                name = card.get("name", "")
                if name in auto_prot_names and name not in used_names \
                        and not card.get("_anti_engine"):
                    price = float(card.get("price_usd", 0) or 0)
                    if budget_remaining <= 0 or running_price + price <= budget_remaining:
                        assignments.append(SlotAssignment(
                            card=card,
                            slot_role="protection",
                            score=0.95,
                            alternatives=[],
                        ))
                        used_names.add(name)
                        running_price += price
                        auto_prot_names.discard(name)

        # --- Score remaining candidates ---
        needed_types = template.unmet_type_targets(already_placed)
        candidates: list[tuple[dict, float]] = []
        for card in pool:
            name = card.get("name", "")
            if name in used_names:
                continue

            # Use pre-computed protection_score if available, otherwise compute.
            # The stored score comes from the variant-agnostic detector, so the
            # empirical bonus must be added here; the _score_protection fallback
            # already includes it (do not add it twice).
            if "protection_score" in card and card["protection_score"] is not None:
                score = float(card["protection_score"]) + empirical_bonus(
                    card,
                    settings.scoring.generator_empirical_weight,
                    settings.scoring.generator_empirical_noisy_weight,
                )
            else:
                score = _score_protection(card, colors, deck_avg_cmc)

            # Type-need: prefer on-type cards while the archetype's engine
            # type is undersupplied (corpus targets; empty without one).
            if needed_types:
                tl = (card.get("type_line") or "").lower()
                if any(t in tl for t in needed_types):
                    score += settings.scoring.generator_type_need_weight

            # Budget preference
            price = float(card.get("price_usd", 0) or 0)
            if price <= 2.0:
                score += 0.01

            candidates.append((card, score))

        candidates.sort(key=lambda x: x[1], reverse=True)

        # Track protection type diversity
        type_counts = {"phasing": 0, "hexproof": 0, "indestructible": 0,
                       "redirect": 0, "other": 0}

        for card, score in candidates:
            if len(assignments) >= target_count:
                break

            name = card.get("name", "")
            if name in used_names:
                continue

            price = float(card.get("price_usd", 0) or 0)
            if budget_remaining > 0 and running_price + price > budget_remaining:
                continue

            # Use pre-computed protection_type or classify
            prot_type = card.get("protection_type") or _classify_protection_type(card)

            # Soft diversity cap (no more than half the slots for one type)
            cap = max(2, target_count // 2)
            if type_counts.get(prot_type, 0) >= cap:
                continue

            assignments.append(SlotAssignment(
                card=card,
                slot_role="protection",
                score=round(score, 4),
                alternatives=[],
            ))
            used_names.add(name)
            running_price += price
            type_counts[prot_type] = type_counts.get(prot_type, 0) + 1

        logger.info(
            "Protection generator: %d cards (target %d), types: %s, protected: %s",
            len(assignments), target_count, type_counts, self.protected_names,
        )
        return assignments


def _classify_protection_type(card: dict) -> str:
    """Classify protection into phasing/hexproof/indestructible/redirect/other."""
    oracle = (card.get("oracle_text") or "").lower()

    if _PHASING.search(oracle):
        return "phasing"
    if _REDIRECT.search(oracle):
        return "redirect"
    if _INDESTRUCTIBLE.search(oracle):
        return "indestructible"
    if _HEXPROOF.search(oracle) or _SHROUD.search(oracle):
        return "hexproof"
    return "other"
