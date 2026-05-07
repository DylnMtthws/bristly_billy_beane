"""Draw package generator (6.5.4).

Deterministic card draw selection: prefer repeatable over one-shot,
adjusted for commanders that provide inherent card advantage.
"""

import logging
from pathlib import Path

from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.slot_assigner import SlotAssignment

logger = logging.getLogger(__name__)


class DrawPackageGenerator:
    """Generate the card draw package for a deck."""

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
    ) -> list[SlotAssignment]:
        """Generate draw package sorted by CVAR, preferring repeatable draw.

        Args:
            color_identity: Commander's color identity.
            target_count: Target number of draw cards.
            budget_remaining: Remaining deck budget.
            template: Deck template for context.
            already_placed: Cards already in the deck.
            role_tag_pool: Pre-filtered cards with role_tags containing "draw".

        Returns:
            List of SlotAssignment for draw cards.
        """
        used_names = {c.get("name", "") for c in already_placed}
        assignments: list[SlotAssignment] = []
        running_price = 0.0

        # Score candidates
        candidates: list[tuple[dict, float]] = []
        for card in role_tag_pool:
            name = card.get("name", "")
            if name in used_names:
                continue

            cvar = card.get("_cvar_score", 0.3)
            oracle = (card.get("oracle_text") or "").lower()
            type_line = (card.get("type_line") or "").lower()

            # Prefer repeatable draw (permanents with draw triggers)
            is_repeatable = (
                ("creature" in type_line or "enchantment" in type_line
                 or "artifact" in type_line)
                and ("whenever" in oracle or "at the beginning" in oracle
                     or "each" in oracle)
            )
            if is_repeatable:
                cvar += 0.15

            # Budget preference
            price = float(card.get("price_usd", 0) or 0)
            if price <= 2.0:
                cvar += 0.03

            candidates.append((card, cvar))

        candidates.sort(key=lambda x: x[1], reverse=True)

        for card, score in candidates:
            if len(assignments) >= target_count:
                break

            name = card.get("name", "")
            if name in used_names:
                continue

            price = float(card.get("price_usd", 0) or 0)
            if budget_remaining > 0 and running_price + price > budget_remaining:
                continue

            assignments.append(SlotAssignment(
                card=card,
                slot_role="draw",
                score=round(score, 4),
                alternatives=[],
            ))
            used_names.add(name)
            running_price += price

        logger.info(
            "Draw generator: %d draw cards (target %d)",
            len(assignments), target_count,
        )
        return assignments
