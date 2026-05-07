"""Removal package generator (6.5.4).

Deterministic removal + board wipe selection with target-type diversity.
"""

import logging
from pathlib import Path

from sabermetrics.models.template import DeckTemplate
from sabermetrics.pipeline.slot_assigner import SlotAssignment

logger = logging.getLogger(__name__)


class RemovalPackageGenerator:
    """Generate the removal + board wipe package for a deck."""

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
        board_wipe_target: int = 2,
    ) -> list[SlotAssignment]:
        """Generate removal package with target-type diversity.

        Args:
            color_identity: Commander's color identity.
            target_count: Target single-target removal count.
            budget_remaining: Remaining deck budget.
            template: Deck template for context.
            already_placed: Cards already in the deck.
            role_tag_pool: Pre-filtered cards with removal/board_wipe role_tags.
            board_wipe_target: Target number of board wipes.

        Returns:
            List of SlotAssignment for removal + board wipe cards.
        """
        used_names = {c.get("name", "") for c in already_placed}
        assignments: list[SlotAssignment] = []
        running_price = 0.0

        # Separate board wipes from single-target removal
        board_wipe_candidates: list[tuple[dict, float]] = []
        single_removal_candidates: list[tuple[dict, float]] = []

        for card in role_tag_pool:
            name = card.get("name", "")
            if name in used_names:
                continue

            cvar = card.get("_cvar_score", 0.3)
            oracle = (card.get("oracle_text") or "").lower()
            type_line = (card.get("type_line") or "").lower()

            # Determine card's role_tags
            role_tags_raw = card.get("role_tags", "[]")
            if isinstance(role_tags_raw, str):
                import json
                try:
                    role_tags = json.loads(role_tags_raw)
                except (json.JSONDecodeError, TypeError):
                    role_tags = []
            else:
                role_tags = role_tags_raw or []

            is_board_wipe = "board_wipe" in role_tags or (
                "destroy all" in oracle or "exile all" in oracle
            )

            # Prefer instant speed for single-target
            if "instant" in type_line:
                cvar += 0.05

            # Budget preference
            price = float(card.get("price_usd", 0) or 0)
            if price <= 2.0:
                cvar += 0.03

            if is_board_wipe:
                board_wipe_candidates.append((card, cvar))
            else:
                single_removal_candidates.append((card, cvar))

        # Sort both pools
        board_wipe_candidates.sort(key=lambda x: x[1], reverse=True)
        single_removal_candidates.sort(key=lambda x: x[1], reverse=True)

        # Fill board wipes first
        for card, score in board_wipe_candidates:
            if len([a for a in assignments if a.slot_role == "removal"
                    and "all" in (a.card.get("oracle_text") or "").lower()]) >= board_wipe_target:
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

        # Fill single-target removal
        # Track target diversity
        target_types = {"creature": 0, "artifact": 0, "enchantment": 0,
                        "planeswalker": 0, "any": 0}

        for card, score in single_removal_candidates:
            if len(assignments) >= target_count + board_wipe_target:
                break

            name = card.get("name", "")
            if name in used_names:
                continue

            price = float(card.get("price_usd", 0) or 0)
            if budget_remaining > 0 and running_price + price > budget_remaining:
                continue

            # Classify removal target
            oracle = (card.get("oracle_text") or "").lower()
            target = _classify_removal_target(oracle)

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
            "Removal generator: %d cards (target %d removal + %d wipes), targets: %s",
            len(assignments), target_count, board_wipe_target, target_types,
        )
        return assignments


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
