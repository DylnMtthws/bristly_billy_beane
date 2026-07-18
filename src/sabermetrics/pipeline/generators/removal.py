"""Removal package generator (6.5.4).

Deterministic removal + board wipe selection with role-specific quality scoring
and target-type diversity.
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
from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.slot_assigner import SlotAssignment

logger = logging.getLogger(__name__)

# --- Removal quality regexes ---

_FREE_CAST = re.compile(
    r"without paying (?:its|their) mana cost|if you control a commander",
    re.IGNORECASE,
)
_EXILE_EFFECT = re.compile(
    r"exile target|exiles target|exile all|exile each",
    re.IGNORECASE,
)
_BOUNCE_EFFECT = re.compile(
    r"return target.*to.*(?:owner's|its owner's) hand|put target.*on top",
    re.IGNORECASE,
)
_OPPONENT_DRAWBACK = re.compile(
    r"(?:its |that |their )(?:controller|owner) (?:draws|searches|gains)",
    re.IGNORECASE,
)
_SELF_DRAWBACK = re.compile(
    r"you (?:lose|pay|sacrifice|discard)",
    re.IGNORECASE,
)
_COUNTER_SPELL = re.compile(
    r"counter target (?:spell|activated|triggered)",
    re.IGNORECASE,
)


def _classify_removal_target(oracle: str) -> str:
    """Classify what type of permanent the removal targets."""
    if "target permanent" in oracle or "target nonland" in oracle:
        return "any"
    if "target creature" in oracle:
        return "creature"
    if "target artifact" in oracle:
        return "artifact"
    if "target enchantment" in oracle:
        return "enchantment"
    if "target planeswalker" in oracle:
        return "planeswalker"
    if "counter target spell" in oracle:
        return "any"
    return "any"


def _flexibility_score(oracle: str) -> float:
    """Score how many permanent types the removal can hit (1.0-3.0).

    "Any permanent" or "nonland permanent" scores highest.
    Hitting multiple named types scores in between.
    Single-type scores lowest.
    """
    oracle_lower = oracle.lower()

    # Broadest: any permanent / nonland permanent
    if "target permanent" in oracle_lower or "target nonland" in oracle_lower:
        return 3.0

    # Counterspells hit anything on the stack
    if _COUNTER_SPELL.search(oracle_lower):
        return 2.5

    # Count distinct types mentioned
    types_hit = 0
    for ptype in ["creature", "artifact", "enchantment", "planeswalker"]:
        if ptype in oracle_lower:
            types_hit += 1

    if types_hit >= 3:
        return 2.5
    if types_hit == 2:
        return 2.0
    if types_hit == 1:
        return 1.0
    # "Destroy target" without specific type — treat as flexible
    if "target" in oracle_lower and ("destroy" in oracle_lower or "exile" in oracle_lower):
        return 2.0
    return 1.0


def _permanence_score(oracle: str) -> float:
    """Score removal permanence: exile > destroy > bounce (0.0-1.0)."""
    if _EXILE_EFFECT.search(oracle):
        return 1.0
    if _BOUNCE_EFFECT.search(oracle):
        return 0.2
    # Default "destroy" is middle ground
    return 0.5


def _mana_efficiency_score(cmc: float) -> float:
    """Score mana efficiency (0.0-2.0). Cheaper removal is premium."""
    if cmc <= 1:
        return 2.0
    if cmc <= 2:
        return 1.5
    if cmc <= 3:
        return 1.0
    if cmc <= 4:
        return 0.5
    return 0.0


def _score_removal(
    card: dict,
    commander_colors: list[str],
    avg_cmc: float,
) -> float:
    """Score a removal card on role-specific quality.

    Signals:
    - Flexibility (permanent types hit): 1.0-3.0
    - Speed (instant/flash vs sorcery): 0.0-1.5
    - Permanence (exile > destroy > bounce): 0.0-1.0
    - Mana efficiency (CMC): 0.0-2.0
    - Free-cast potential: +2.0
    - Drawback scaling: -0.5 to 0
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

    # --- Flexibility ---
    role_score += _flexibility_score(oracle)

    # --- Speed ---
    if "instant" in type_line or "flash" in oracle.lower():
        role_score += 1.5
    elif "sorcery" in type_line:
        role_score += 0.0
    else:
        # Creatures/enchantments with removal ETBs — moderate speed
        role_score += 0.5

    # --- Permanence ---
    role_score += _permanence_score(oracle)

    # --- Mana efficiency ---
    role_score += _mana_efficiency_score(cmc)

    # --- Free-cast potential ---
    if _FREE_CAST.search(oracle):
        role_score += 2.0

    # --- Drawback scaling ---
    if _OPPONENT_DRAWBACK.search(oracle):
        role_score -= 0.5
    elif _SELF_DRAWBACK.search(oracle):
        role_score -= 0.2

    # --- Blend with CVAR (40% CVAR, 60% role-specific) ---
    # Max theoretical role_score ~9.5; normalize to 0-1
    normalized_role = min(role_score / 9.5, 1.0)
    final_score = 0.60 * normalized_role + 0.40 * cvar

    # --- Empirical grounding: additive, never penalizes absence (ADR-005) ---
    final_score += empirical_bonus(
        card,
        settings.scoring.generator_empirical_weight,
        settings.scoring.generator_empirical_noisy_weight,
    )

    return final_score


def _load_removal_auto_includes() -> tuple[dict, set[str]]:
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


class RemovalPackageGenerator:
    """Generate the removal + board wipe package for a deck."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.protected_names: set[str] = set()

    def _load_removal_candidates(
        self,
        color_identity: list[str],
    ) -> list[dict]:
        """Load pre-scored removal candidates from the removal_candidates table.

        Joins with cards table to get full card data. Filters by color identity
        and Commander legality.

        Args:
            color_identity: Commander's color identity.

        Returns:
            List of card dicts augmented with removal_score from removal_candidates.
        """
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row

            color_set = set(color_identity)
            cursor = conn.execute(
                "SELECT c.id, c.name, c.oracle_text, c.type_line, c.cmc, "
                "c.color_identity, c.mana_cost, c.role_tags, c.keywords, "
                "r.removal_type, r.target_type, r.removal_score, r.is_exile, "
                "r.is_instant, r.is_free_cast, r.flexibility_score "
                "FROM removal_candidates r "
                "JOIN cards c ON r.card_id = c.id "
                "WHERE c.is_legal_in_99 = 1 "
                "ORDER BY r.removal_score DESC"
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
            logger.warning("Failed to load removal_candidates: %s", e)
            return []

    def generate(
        self,
        color_identity: list[str],
        target_count: int,
        budget_remaining: float,
        template: DeckTemplate,
        already_placed: list[dict],
        role_tag_pool: list[dict],
        board_wipe_target: int = 2,
        commander_colors: list[str] | None = None,
        avg_cmc: float | None = None,
    ) -> list[SlotAssignment]:
        """Generate removal package with auto-includes and role-specific scoring.

        Prefers the removal_candidates table (pre-scored, reminder-text-stripped)
        over the role_tag_pool. Falls back to role_tag_pool if the table is
        empty or unavailable.

        Args:
            color_identity: Commander's color identity.
            target_count: Target single-target removal count.
            budget_remaining: Remaining deck budget.
            template: Deck template for context.
            already_placed: Cards already in the deck.
            role_tag_pool: Pre-filtered cards with removal/board_wipe role_tags.
            board_wipe_target: Target number of board wipes.
            commander_colors: Commander's color identity (defaults to color_identity).
            avg_cmc: Target average CMC (defaults to template value).

        Returns:
            List of SlotAssignment for removal + board wipe cards.
        """
        colors = commander_colors or color_identity
        deck_avg_cmc = avg_cmc or template.avg_cmc_target

        auto_includes, self.protected_names = _load_removal_auto_includes()
        used_names = {c.get("name", "") for c in already_placed}
        assignments: list[SlotAssignment] = []
        running_price = 0.0

        # --- Auto-include removal staples ---
        # Collect single-target and wipe auto-includes separately so each
        # respects its own quota — otherwise section ordering causes wipes
        # to be cut from the cap, and the wipe loop below adds them back
        # on top of an already-full single-removal set, overflowing the
        # combined target.
        single_entries: list[tuple[str, int]] = []  # (name, priority)
        wipe_entries: list[tuple[str, int]] = []
        seen_names: set[str] = set()

        # Color-gated removal staples (single-target)
        color_sections = [
            ("W", "removal_has_white"),
            ("U", "removal_has_blue"),
            ("B", "removal_has_black"),
            ("R", "removal_has_red"),
            ("G", "removal_has_green"),
        ]
        for color, section in color_sections:
            if color in color_identity:
                for entry in auto_includes.get(section, []):
                    if entry.get("role") == "removal" and entry["name"] not in seen_names:
                        is_protected = entry.get("protect_from_swap", False)
                        priority = 0 if is_protected else len(single_entries) + 1
                        single_entries.append((entry["name"], priority))
                        seen_names.add(entry["name"])

        # Color-gated board wipe staples
        wipe_sections = [
            ("W", "wipe_has_white"),
            ("R", "wipe_has_red"),
        ]
        for color, section in wipe_sections:
            if color in color_identity:
                for entry in auto_includes.get(section, []):
                    if entry.get("role") == "removal" and entry["name"] not in seen_names:
                        is_protected = entry.get("protect_from_swap", False)
                        priority = 0 if is_protected else len(wipe_entries) + 1
                        wipe_entries.append((entry["name"], priority))
                        seen_names.add(entry["name"])

        # Sort each bucket by priority (protected first, then order of appearance)
        single_entries.sort(key=lambda x: x[1])
        wipe_entries.sort(key=lambda x: x[1])

        # Cap each bucket independently
        single_names = [n for n, _ in single_entries[:target_count]]
        wipe_names = [n for n, _ in wipe_entries[:board_wipe_target]]
        combined_target = target_count + board_wipe_target
        if len(single_entries) > target_count or len(wipe_entries) > board_wipe_target:
            logger.info(
                "Capping removal auto-includes: single %d→%d, wipes %d→%d",
                len(single_entries), len(single_names),
                len(wipe_entries), len(wipe_names),
            )
        auto_removal_set = set(single_names) | set(wipe_names)
        auto_wipe_set = set(wipe_names)

        # Try loading removal_candidates table
        removal_candidates = self._load_removal_candidates(color_identity)
        use_candidates_table = len(removal_candidates) > 0

        if use_candidates_table:
            pool = removal_candidates
            # Candidate-table cards are loaded fresh from SQL; carry the
            # empirical annotations over from role_tag_pool so the bonus applies.
            annotate_empirical(pool, role_tag_pool)
            logger.info("Using removal_candidates table (%d cards)", len(pool))
        else:
            pool = role_tag_pool
            logger.info("Falling back to role_tag_pool (%d cards)", len(pool))

        # Place auto-includes from pool (or role_tag_pool as backup)
        search_pools = [pool] if use_candidates_table else [role_tag_pool]
        if use_candidates_table:
            search_pools.append(role_tag_pool)

        for search_pool in search_pools:
            for card in search_pool:
                name = card.get("name", "")
                if name in auto_removal_set and name not in used_names:
                    price = float(card.get("price_usd", 0) or 0)
                    if budget_remaining <= 0 or running_price + price <= budget_remaining:
                        assignments.append(SlotAssignment(
                            card=card,
                            slot_role="removal",
                            score=0.95,
                            alternatives=[],
                        ))
                        used_names.add(name)
                        running_price += price
                        auto_removal_set.discard(name)

        # --- Score and sort remaining candidates ---
        board_wipe_candidates: list[tuple[dict, float]] = []
        single_removal_candidates: list[tuple[dict, float]] = []

        for card in pool:
            name = card.get("name", "")
            if name in used_names:
                continue

            oracle = (card.get("oracle_text") or "").lower()

            # Determine card's role_tags
            role_tags_raw = card.get("role_tags", "[]")
            if isinstance(role_tags_raw, str):
                try:
                    role_tags = json.loads(role_tags_raw)
                except (json.JSONDecodeError, TypeError):
                    role_tags = []
            else:
                role_tags = role_tags_raw or []

            is_board_wipe = "board_wipe" in role_tags or (
                "destroy all" in oracle or "exile all" in oracle
            )
            # Also check removal_type from candidates table
            if card.get("removal_type") == "board_wipe":
                is_board_wipe = True

            # Use pre-computed removal_score if available, otherwise compute.
            # The stored score comes from the variant-agnostic detector, so the
            # empirical bonus must be added here; the _score_removal fallback
            # already includes it (do not add it twice).
            if "removal_score" in card and card["removal_score"] is not None:
                score = float(card["removal_score"]) + empirical_bonus(
                    card,
                    settings.scoring.generator_empirical_weight,
                    settings.scoring.generator_empirical_noisy_weight,
                )
            else:
                score = _score_removal(card, colors, deck_avg_cmc)

            # Budget preference
            price = float(card.get("price_usd", 0) or 0)
            if price <= 2.0:
                score += 0.01

            if is_board_wipe:
                board_wipe_candidates.append((card, score))
            else:
                single_removal_candidates.append((card, score))

        # Sort both pools
        board_wipe_candidates.sort(key=lambda x: x[1], reverse=True)
        single_removal_candidates.sort(key=lambda x: x[1], reverse=True)

        # Count wipes already placed via auto-includes (by name match — more
        # reliable than scanning oracle text for "all").
        wipes_placed = sum(
            1 for a in assignments if a.card.get("name", "") in auto_wipe_set
        )

        # Fill board wipes first, but never exceed the combined cap
        for card, score in board_wipe_candidates:
            if wipes_placed >= board_wipe_target:
                break
            if len(assignments) >= combined_target:
                break

            name = card.get("name", "")
            if name in used_names:
                continue

            price = float(card.get("price_usd", 0) or 0)
            if budget_remaining > 0 and running_price + price > budget_remaining:
                continue

            assignments.append(SlotAssignment(
                card=card,
                slot_role="removal",
                score=round(score, 4),
                alternatives=[],
            ))
            used_names.add(name)
            running_price += price
            wipes_placed += 1

        # Fill single-target removal
        target_types = {"creature": 0, "artifact": 0, "enchantment": 0,
                        "planeswalker": 0, "any": 0}

        for card, score in single_removal_candidates:
            if len(assignments) >= combined_target:
                break

            name = card.get("name", "")
            if name in used_names:
                continue

            price = float(card.get("price_usd", 0) or 0)
            if budget_remaining > 0 and running_price + price > budget_remaining:
                continue

            # Use pre-computed target_type or classify
            oracle = (card.get("oracle_text") or "").lower()
            target = card.get("target_type") or _classify_removal_target(oracle)

            # Soft diversity cap
            cap = max(2, (target_count + board_wipe_target) // 3)
            if target != "any" and target_types.get(target, 0) >= cap:
                continue

            assignments.append(SlotAssignment(
                card=card,
                slot_role="removal",
                score=round(score, 4),
                alternatives=[],
            ))
            used_names.add(name)
            running_price += price
            target_types[target] = target_types.get(target, 0) + 1

        logger.info(
            "Removal generator: %d cards (target %d removal + %d wipes), targets: %s, protected: %s",
            len(assignments), target_count, board_wipe_target, target_types,
            self.protected_names,
        )
        return assignments
