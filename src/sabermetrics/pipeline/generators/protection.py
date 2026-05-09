"""Protection package generator.

Deterministic protection spell selection with role-specific quality scoring.
Fills commander protection slots (hexproof, indestructible, phasing, etc.)
that were previously only available via the greedy optimizer.
"""

import json
import logging
import re
from pathlib import Path

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

    return final_score


class ProtectionPackageGenerator:
    """Generate the protection package for a deck."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

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
    ) -> list[SlotAssignment]:
        """Generate protection package with role-specific scoring.

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

        used_names = {c.get("name", "") for c in already_placed}
        assignments: list[SlotAssignment] = []
        running_price = 0.0

        # Score all candidates
        candidates: list[tuple[dict, float]] = []
        for card in role_tag_pool:
            name = card.get("name", "")
            if name in used_names:
                continue

            score = _score_protection(card, colors, deck_avg_cmc)

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

            # Classify protection type for diversity
            prot_type = _classify_protection_type(card)

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
            "Protection generator: %d cards (target %d), types: %s",
            len(assignments), target_count, type_counts,
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
